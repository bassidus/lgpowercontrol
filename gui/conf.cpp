#include "conf.h"

#include <QFile>
#include <QFileInfo>
#include <QRegularExpression>
#include <QTemporaryFile>
#include <QTextStream>

#include <cerrno>
#include <cstring>
#include <sys/stat.h>
#include <unistd.h>

namespace conf {

namespace {

// One line through the same mill as shlex.split(line, comments=True), stopped after the first
// token because load_conf() only ever looks at tokens[0]. Quotes are stripped, whitespace inside
// them is kept, and an unquoted '#' ends the line - all three are pinned by tests/test_common.py.
// Returns false with *unterminated set when a quote is left open: Python raises ValueError there,
// so such a line does not merely parse differently, it stops lgpowercontrol from starting at all.
bool firstToken(const QString &line, QString *token, bool *unterminated)
{
    QString out;
    bool started = false;
    QChar quote;  // null while outside quotes

    for (int i = 0; i < line.size(); ++i) {
        const QChar c = line.at(i);

        if (!quote.isNull()) {
            if (c == quote) {
                quote = QChar();
            } else if (c == QLatin1Char('\\') && quote == QLatin1Char('"') && i + 1 < line.size()
                       && (line.at(i + 1) == QLatin1Char('"') || line.at(i + 1) == QLatin1Char('\\'))) {
                // Inside double quotes a backslash escapes only a quote or another backslash;
                // anything else keeps the backslash, exactly as shlex's escapedquotes does.
                out.append(line.at(++i));
            } else {
                out.append(c);
            }
            continue;
        }

        if (c == QLatin1Char('\'') || c == QLatin1Char('"')) {
            quote = c;
            started = true;
        } else if (c == QLatin1Char('\\') && i + 1 < line.size()) {
            out.append(line.at(++i));
            started = true;
        } else if (c == QLatin1Char('#')) {
            break;
        } else if (c.isSpace()) {
            if (started)
                break;
        } else {
            out.append(c);
            started = true;
        }
    }

    *unterminated = !quote.isNull();
    *token = out;
    return started && !*unterminated;
}

// Values reach the file inside double quotes, so a quote or a backslash in one would produce a
// line the reader mis-parses - or, with an unbalanced quote, cannot parse at all. No setting has
// any use for either character, so they are dropped rather than escaped.
QString sanitize(const QString &value)
{
    QString out = value;
    out.remove(QLatin1Char('"'));
    out.remove(QLatin1Char('\\'));
    out.replace(QRegularExpression(QStringLiteral("[\\r\\n]")), QString());
    return out;
}

}  // namespace

LoadResult load(const QString &path)
{
    LoadResult result;

    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        result.error = file.errorString();
        return result;
    }

    QTextStream stream(&file);
    int lineNumber = 0;
    while (!stream.atEnd()) {
        const QString line = stream.readLine();
        ++lineNumber;

        QString token;
        bool unterminated = false;
        if (!firstToken(line, &token, &unterminated)) {
            if (unterminated) {
                result.error = QStringLiteral("Line %1 has an unclosed quote. lgpowercontrol "
                                              "cannot read this file until that is fixed by hand.")
                                   .arg(lineNumber);
                return result;
            }
            continue;
        }

        const int split = token.indexOf(QLatin1Char('='));
        if (split < 0)
            continue;
        result.values.insert(token.left(split), token.mid(split + 1));  // last definition wins
    }

    result.ok = true;
    return result;
}

// A line-wise value swap that leaves everything else byte for byte: the comment blocks below each
// key, the section rules, and the trailing legend on the same line ("# 1 = enabled | 0 = disabled").
// set_conf_value() in install.py does the same job with '^KEY=.*', which eats that legend - it only
// ever writes LGTV_MAC, which has none, so the difference has never shown up there.
//
// Written to a temp file in the same directory and renamed over the original rather than truncated
// in place: a half-written conf leaves every part of lgpowercontrol unable to start. The rename is
// permitted despite the sticky bit on /opt/lgpowercontrol (1775) because the user owns both the
// conf entry and the temp file - see the reasoning above set_ownership() in install.py, which this
// must not work around. Mode and ownership are copied off the original so the file stays the
// user's 0644 even when this runs as root.
SaveResult save(const QString &path, const QMap<QString, QString> &values)
{
    SaveResult result;

    QFile source(path);
    if (!source.open(QIODevice::ReadOnly | QIODevice::Text)) {
        result.error = source.errorString();
        return result;
    }
    QString content = QString::fromUtf8(source.readAll());
    source.close();

    struct stat original {};
    if (::stat(QFile::encodeName(path).constData(), &original) != 0) {
        result.error = QStringLiteral("Cannot stat %1: %2").arg(path, QString::fromLocal8Bit(strerror(errno)));
        return result;
    }

    for (auto it = values.constBegin(); it != values.constEnd(); ++it) {
        // The value is either quoted, or a bare run of non-space characters; group 1 keeps the
        // trailing comment. Only the first definition is rewritten, as install.py's count=1 does.
        const QRegularExpression pattern(
            QStringLiteral("^%1=(?:\"[^\"]*\"|'[^']*'|[^\\s#]*)([ \\t]*#.*)?$")
                .arg(QRegularExpression::escape(it.key())),
            QRegularExpression::MultilineOption);

        const QRegularExpressionMatch match = pattern.match(content);
        if (!match.hasMatch()) {
            result.missing.append(it.key());
            continue;
        }
        const QString replacement = QStringLiteral("%1=\"%2\"%3")
                                        .arg(it.key(), sanitize(it.value()), match.captured(1));
        content.replace(match.capturedStart(0), match.capturedLength(0), replacement);
    }

    const QFileInfo info(path);
    QTemporaryFile temp(info.absolutePath() + QStringLiteral("/.lgpowercontrol.conf.XXXXXX"));
    temp.setAutoRemove(false);
    if (!temp.open()) {
        result.error = temp.errorString();
        return result;
    }
    const QString tempPath = temp.fileName();

    const QByteArray bytes = content.toUtf8();
    if (temp.write(bytes) != bytes.size() || !temp.flush() || ::fsync(temp.handle()) != 0) {
        result.error = temp.errorString();
        temp.close();
        QFile::remove(tempPath);
        return result;
    }
    temp.close();

    const QByteArray tempName = QFile::encodeName(tempPath);
    ::chmod(tempName.constData(), original.st_mode & 07777);
    if (::geteuid() == 0) {
        // Best effort, and deliberately not an error: the rename below still produces a readable
        // conf. A cast to void does not silence glibc's warn_unused_result on chown - GCC 11 warns
        // through it - so the result is bound to a variable instead.
        [[maybe_unused]] const int owned =
            ::chown(tempName.constData(), original.st_uid, original.st_gid);
    }

    if (::rename(tempName.constData(), QFile::encodeName(path).constData()) != 0) {
        result.error = QStringLiteral("Cannot replace %1: %2").arg(path, QString::fromLocal8Bit(strerror(errno)));
        QFile::remove(tempPath);
        return result;
    }

    result.ok = true;
    return result;
}

bool writable(const QString &path)
{
    return ::access(QFile::encodeName(path).constData(), W_OK) == 0;
}

QMap<QString, QString> defaults()
{
    return {
        {QString::fromLatin1(KeyIp), QString()},
        {QString::fromLatin1(KeyMac), QString()},
        {QString::fromLatin1(KeyHdmi), QString()},
        {QString::fromLatin1(KeyShared), QStringLiteral("0")},
        {QString::fromLatin1(KeySuspend), QStringLiteral("1")},
        {QString::fromLatin1(KeyShutdown), QStringLiteral("1")},
        {QString::fromLatin1(KeyWarning), QStringLiteral("120")},
        {QString::fromLatin1(KeyPoll), QStringLiteral("5")},
        {QString::fromLatin1(KeyLogging), QStringLiteral("0")},
    };
}

}  // namespace conf
