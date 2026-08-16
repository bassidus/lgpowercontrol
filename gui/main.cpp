// A form over the nine keys in lgpowercontrol.conf. The point is not that editing a text file is
// hard, it is that several of the keys mean something the file does not say: POWER_OFF_AT_* is
// switched off by the exact string "0" and nothing else, SHARED_TV does nothing at all without
// HDMI_INPUT, and an empty or non-numeric value is a documented setting rather than a mistake.
// Each widget below reads and writes its key the way src/lgpowercontrol reads it, not the way it
// looks - including the fallbacks, so the two must be changed together.
//
// Nothing here styles itself - no palettes, no fonts, no stylesheets. That is what lets Qt use
// Breeze under Plasma and Fusion plus the freedesktop appearance portal (light/dark, font) under
// GNOME. Hardcoding any of it would look deliberate on one desktop and broken on the other. The
// spacing is set in font metrics for the same reason: it grows with the desktop's own font.
#include "conf.h"

#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QIcon>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QProcess>
#include <QPainter>
#include <QPalette>
#include <QPixmap>
#include <QPushButton>
#include <QRegularExpression>
#include <QSlider>
#include <QSpinBox>
#include <QStandardPaths>
#include <QTextStream>
#include <QVBoxLayout>

namespace {

const char *const NotifyUnit = "lgpowercontrol-notify.service";
const char *const MonitorUnit = "lgpowercontrol-monitor.service";

// --- Reading the conf the way the code reads it --------------------------------------------

// SHARED_TV and LOGGING: the exact string "1" and nothing else. cli.py:151 logs anything that is
// neither 1 nor 0 and reads it as 0, so an unchecked box is an honest picture of a typo.
bool readEnabled(const QMap<QString, QString> &values, const QString &key)
{
    return values.value(key).trimmed() == QLatin1String("1");
}

// POWER_OFF_AT_*: the inverse reading in cli.py:168-182 - only an explicit "0" disables, so a
// missing key or a typo leaves the off event on. The box has to show that, not a tidier guess.
bool readNotDisabled(const QMap<QString, QString> &values, const QString &key)
{
    return values.value(key).trimmed() != QLatin1String("0");
}

// Four dotted decimal octets, checked by hand rather than with QHostAddress: that class lives in
// Qt6::Network, and linking a whole module to read four numbers would be the only dependency in
// this program that is not Widgets. IPv6 is not accepted because bscpylgtv is given this string
// as a host and the TV is on a home LAN; a v6 literal here would be a surprise, not a feature.
bool isIpv4(const QString &value)
{
    const QStringList octets = value.split(QLatin1Char('.'));
    if (octets.size() != 4)
        return false;
    for (const QString &octet : octets) {
        bool ok = false;
        const int number = octet.toInt(&ok);
        if (!ok || octet.isEmpty() || octet.size() > 3 || number < 0 || number > 255)
            return false;
        for (const QChar c : octet)
            if (c < QLatin1Char('0') || c > QLatin1Char('9'))
                return false;
    }
    return true;
}

bool allDigits(const QString &value)
{
    if (value.isEmpty())
        return false;
    for (const QChar c : value)
        if (c < QLatin1Char('0') || c > QLatin1Char('9'))
            return false;
    return true;
}

// conf_int() from common.py, including why allow_zero exists: a configured 0 once read as "unset",
// which broke the documented way to switch OFF_WARNING_SECONDS off.
int readInt(const QMap<QString, QString> &values, const QString &key, int fallback, bool allowZero = false)
{
    const QString value = values.value(key);
    if (!allDigits(value))
        return fallback;
    const int parsed = value.toInt();
    return (parsed == 0 && !allowZero) ? fallback : parsed;
}

// HDMI_INPUT: cli.py:157-161 wants a digit string of at least 1; anything else is "not configured",
// which the spin box shows as its special value rather than as a zero the TV would never have.
int readHdmi(const QMap<QString, QString> &values)
{
    const QString value = values.value(conf::KeyHdmi).trimmed();
    if (!allDigits(value))
        return 0;
    const int parsed = value.toInt();
    return parsed >= 1 ? parsed : 0;
}

// --- Widgets ---------------------------------------------------------------------------------

// Breeze and Adwaita name their padlocks differently, and a bare hicolor theme has neither. The
// icon is the entire affordance here, so a null one is not dressed up with a blank button: the
// caller leaves the field plainly editable instead of locking it behind something invisible.
// A padlock drawn from the palette's own text colour, used only when the icon theme has none.
// Measured on Ubuntu 22.04 GNOME: with no Qt platform-theme plugin installed, QIcon::themeName()
// is "hicolor" rather than the desktop's Yaru, so every themed name comes back null - and
// setFallbackThemeName("Adwaita") does not change that, which was tried before this was written.
// Painting it keeps the fields protected on such a desktop instead of quietly leaving them open.
QIcon paintedLock(bool locked)
{
    const int side = 16;
    QPixmap pixmap(side, side);
    pixmap.fill(Qt::transparent);

    QPainter painter(&pixmap);
    painter.setRenderHint(QPainter::Antialiasing);
    QColor ink = QApplication::palette().color(QPalette::WindowText);
    ink.setAlpha(200);  // the themed icons are lighter than body text; this matches them
    painter.setPen(QPen(ink, 1.6));

    // Shackle: a closed one sits centred over the body, an open one is raised and hinged left.
    const QRectF shackle = locked ? QRectF(4.5, 2.0, 7.0, 8.0) : QRectF(2.5, 0.5, 7.0, 8.0);
    painter.drawArc(shackle, 0, 180 * 16);
    painter.drawLine(QPointF(shackle.left(), shackle.center().y()),
                     QPointF(shackle.left(), locked ? 7.0 : 5.0));
    if (locked)
        painter.drawLine(QPointF(shackle.right(), shackle.center().y()), QPointF(shackle.right(), 7.0));

    painter.setPen(Qt::NoPen);
    painter.setBrush(ink);
    painter.drawRoundedRect(QRectF(2.5, 7.0, 11.0, 7.5), 1.5, 1.5);
    painter.end();

    return QIcon(pixmap);
}

QIcon lockIcon(bool locked)
{
    const QIcon themed =
        locked ? QIcon::fromTheme(QStringLiteral("object-locked"),
                                  QIcon::fromTheme(QStringLiteral("changes-prevent-symbolic"),
                                                   QIcon::fromTheme(QStringLiteral("system-lock-screen"))))
               : QIcon::fromTheme(QStringLiteral("object-unlocked"),
                                  QIcon::fromTheme(QStringLiteral("changes-allow-symbolic"),
                                                   QIcon::fromTheme(QStringLiteral("document-edit"))));
    return themed.isNull() ? paintedLock(locked) : themed;
}

// The address and the MAC are the two settings that are set once and then never touched, and the
// two where a stray keystroke leaves lgpowercontrol talking to nothing. They open on a deliberate
// click on the padlock rather than on a click anywhere in the field.
void setFieldLocked(QLineEdit *field, QAction *padlock, bool locked)
{
    field->setReadOnly(locked);
    padlock->setIcon(lockIcon(locked));
    padlock->setToolTip(locked ? QStringLiteral("Locked so it cannot be changed by accident. "
                                                "Click to edit it.")
                               : QStringLiteral("Unlocked. Click to lock it again."));
}

// Two tests, because either one alone is wrong on a real machine. notify.py:112 exits when
// kscreen-doctor or kreadconfig6 is missing, so those binaries decide whether the warning can work
// at all - but on a machine with Plasma and GNOME both installed they are on PATH in the GNOME
// session too, where nothing dims through KScreen and the warning never appears. So the session is
// asked as well. When it does not answer - XDG_CURRENT_DESKTOP is unset, as it is from a tty - the
// binaries are trusted on their own rather than greying out settings that probably do work.
bool plasmaWarningAvailable()
{
    if (QStandardPaths::findExecutable(QStringLiteral("kscreen-doctor")).isEmpty()
        || QStandardPaths::findExecutable(QStringLiteral("kreadconfig6")).isEmpty())
        return false;

    // "KDE" is the entry Plasma puts in it; the variable is colon-separated and often carries the
    // distribution's own name first, as in "ubuntu:GNOME".
    const QString desktop = qEnvironmentVariable("XDG_CURRENT_DESKTOP");
    return desktop.isEmpty()
        || desktop.split(QLatin1Char(':')).contains(QStringLiteral("KDE"), Qt::CaseInsensitive);
}

// A unit that is not running is never started by this program: it reads the conf on its own when
// it next starts, and starting it here would turn a settings edit into a service being enabled.
bool unitIsActive(bool userScope, const QString &unit)
{
    QStringList args;
    if (userScope)
        args << QStringLiteral("--user");
    args << QStringLiteral("is-active") << QStringLiteral("--quiet") << unit;

    QProcess probe;
    probe.start(QStringLiteral("systemctl"), args);
    if (!probe.waitForFinished(5000))
        return false;
    return probe.exitStatus() == QProcess::NormalExit && probe.exitCode() == 0;
}

// A slider is how a duration should be picked and a spin box is how an exact one gets typed, so
// the two are shown together and kept in step. setValue() on an unchanged value is a no-op, which
// is what stops the pair from bouncing between each other.
QWidget *secondsRow(QSlider **sliderOut, QSpinBox **spinOut, int minimum, int maximum, int step,
                    const QString &specialValueText = QString())
{
    auto *slider = new QSlider(Qt::Horizontal);
    slider->setRange(minimum, maximum);
    slider->setSingleStep(step);
    slider->setPageStep(step * 5);

    auto *spin = new QSpinBox;
    spin->setRange(minimum, maximum);
    // The slider is the coarse instrument and steps by `step`; the spin box arrows are how the
    // value gets nudged onto an exact second, so they always move by one whatever the slider does.
    spin->setSingleStep(1);
    spin->setSuffix(QStringLiteral(" s"));
    if (!specialValueText.isEmpty())
        spin->setSpecialValueText(specialValueText);

    QObject::connect(slider, &QSlider::valueChanged, spin, &QSpinBox::setValue);
    QObject::connect(spin, &QSpinBox::valueChanged, slider, &QSlider::setValue);

    auto *row = new QWidget;
    auto *layout = new QHBoxLayout(row);
    layout->setContentsMargins(0, 0, 0, 0);
    layout->addWidget(slider, 1);
    layout->addWidget(spin);

    *sliderOut = slider;
    *spinOut = spin;
    return row;
}

class ConfWindow : public QWidget
{
public:
    ConfWindow(const QMap<QString, QString> &values, bool editable, bool valuesAreDefaults = false);

protected:
    void closeEvent(QCloseEvent *event) override;

private:
    QMap<QString, QString> collect() const;
    void applyValues(const QMap<QString, QString> &values, bool becomesBaseline = true);
    void updateDirty();
    bool validate(QString *error) const;
    bool save();
    void restore();
    void loadDefaults();
    void noteWhatWentStale(const QMap<QString, QString> &saved);
    void restartStaleServices();
    void setStatus(const QString &text);

    QLineEdit *m_ip = nullptr;
    QLineEdit *m_mac = nullptr;
    QAction *m_ipLock = nullptr;
    QAction *m_macLock = nullptr;
    QComboBox *m_hdmi = nullptr;
    QCheckBox *m_shared = nullptr;
    QCheckBox *m_suspend = nullptr;
    QCheckBox *m_shutdown = nullptr;
    QSlider *m_warningSlider = nullptr;
    QSpinBox *m_warningSpin = nullptr;
    QSlider *m_pollSlider = nullptr;
    QSpinBox *m_pollSpin = nullptr;
    QCheckBox *m_logging = nullptr;

    QPushButton *m_save = nullptr;
    QPushButton *m_restore = nullptr;
    QPushButton *m_defaults = nullptr;
    QLabel *m_status = nullptr;

    // What the file said, as the widgets read it. Save is offered against this rather than against
    // "has anything been touched", so changing a value and changing it back leaves nothing to save.
    QMap<QString, QString> m_baseline;

    // Set by a save that changed a key one of the long-running services is holding in memory, and
    // cleared once that service has been restarted. m_monitorStale is the only thing that ever
    // leads to an authorisation prompt, and only LOGGING sets it - see noteWhatWentStale().
    bool m_notifyStale = false;
    bool m_monitorStale = false;
    bool m_editable = true;
};

ConfWindow::ConfWindow(const QMap<QString, QString> &values, bool editable, bool valuesAreDefaults)
    : m_editable(editable)
{
    setWindowTitle(QStringLiteral("LGPowerControl Settings"));

    // One unit of air, in font metrics rather than pixels: a desktop with a bigger font or a
    // fractional scale gets a window that grew with it instead of one that only got tighter.
    const int gap = fontMetrics().height();

    auto *tvBox = new QGroupBox(QStringLiteral("TV"));
    auto *tvForm = new QFormLayout(tvBox);
    tvForm->setContentsMargins(gap, gap, gap, gap);
    tvForm->setVerticalSpacing(gap * 2 / 3);
    tvForm->setHorizontalSpacing(gap);

    m_ip = new QLineEdit;
    m_ip->setPlaceholderText(QStringLiteral("192.168.1.100"));
    m_ip->setToolTip(QStringLiteral("Required. The TV's address on the network."));
    m_ipLock = m_ip->addAction(lockIcon(true), QLineEdit::TrailingPosition);
    tvForm->addRow(QStringLiteral("Address:"), m_ip);

    m_mac = new QLineEdit;
    m_mac->setPlaceholderText(QStringLiteral("00:11:22:33:44:55"));
    m_mac->setToolTip(QStringLiteral("Filled in by the installer while the TV is on.\n"
                                     "Without it, a TV in deep standby cannot be woken."));
    m_macLock = m_mac->addAction(lockIcon(true), QLineEdit::TrailingPosition);
    tvForm->addRow(QStringLiteral("MAC address:"), m_mac);

    // A list rather than a number to type: an LG set has four or five HDMI sockets, so the choice
    // is a pick from what exists. The item data carries the number written to the file, which is
    // what lets applyValues() append an out-of-range input a conf may already name.
    m_hdmi = new QComboBox;
    m_hdmi->addItem(QStringLiteral("None - never switch input"), 0);
    for (int input = 1; input <= 5; ++input)
        m_hdmi->addItem(QString::number(input), input);
    m_hdmi->setToolTip(QStringLiteral("The input this computer is connected to. The TV is\n"
                                      "switched to it when this computer turns the TV on."));
    tvForm->addRow(QStringLiteral("HDMI input:"), m_hdmi);

    auto *offBox = new QGroupBox(QStringLiteral("Turning the TV off"));
    auto *offLayout = new QVBoxLayout(offBox);
    offLayout->setContentsMargins(gap, gap, gap, gap);
    offLayout->setSpacing(gap * 2 / 3);

    m_shared = new QCheckBox(QStringLiteral("Another device shares this TV"));
    offLayout->addWidget(m_shared);

    m_suspend = new QCheckBox(QStringLiteral("Turn the TV off when this computer suspends"));
    offLayout->addWidget(m_suspend);

    m_shutdown = new QCheckBox(QStringLiteral("Turn the TV off when this computer shuts down"));
    offLayout->addWidget(m_shutdown);

    auto *warnBox = new QGroupBox(QStringLiteral("Idle warning (Plasma only)"));
    auto *warnLayout = new QVBoxLayout(warnBox);
    warnLayout->setContentsMargins(gap, gap, gap, gap);
    warnLayout->setSpacing(gap);

    auto *warnNote = new QLabel(QStringLiteral(
        "A notification warns you before idling turns the TV off. Needs \"Dim automatically\" "
        "enabled in system settings - the dim is what starts the countdown."));
    warnNote->setWordWrap(true);
    warnLayout->addWidget(warnNote);

    auto *warnForm = new QFormLayout;
    warnForm->setVerticalSpacing(gap * 2 / 3);
    warnForm->setHorizontalSpacing(gap);
    warnForm->addRow(QStringLiteral("Warn ahead by:"),
                     secondsRow(&m_warningSlider, &m_warningSpin, 0, 600, 10,
                                QStringLiteral("No warning")));
    m_warningSpin->setToolTip(QStringLiteral("More than the gap between dimming and screen-off means the\n"
                                             "warning comes as soon as the screen dims. 0 turns it off."));

    warnForm->addRow(QStringLiteral("Check every:"), secondsRow(&m_pollSlider, &m_pollSpin, 1, 30, 1));
    m_pollSpin->setToolTip(QStringLiteral("How often the service checks whether the screen has dimmed.\n"
                                          "A lower value uses more CPU, but lands the warning closer\n"
                                          "to the configured time."));
    warnLayout->addLayout(warnForm);

    // Greyed out where the warning cannot run, rather than hidden: the two values stay visible and
    // keep whatever the file says, so a conf edited here on GNOME still works when the same file is
    // read on Plasma. Saving keeps them untouched because a disabled widget cannot be changed.
    // The explanation goes in the label rather than a tooltip on purpose: Qt delivers no mouse
    // events to a disabled widget, so a tooltip set here would never appear.
    if (!plasmaWarningAvailable()) {
        warnBox->setEnabled(false);
        warnNote->setText(QStringLiteral(
            "A notification warns you before idling turns the TV off. This desktop is not Plasma, "
            "so nothing shows the warning here and these two settings have no effect."));
    }

    // One checkbox on the button row rather than in a box of its own: it is not a setting about the
    // TV like the two columns above, it is about this installation, and a group heading over a
    // single line was more furniture than it earned.
    m_logging = new QCheckBox(QStringLiteral("Write log messages to the journal"));
    m_logging->setToolTip(QStringLiteral("Run journalctl -t lgpowercontrol to read them."));

    m_status = new QLabel;
    m_status->setWordWrap(true);
    m_status->setTextInteractionFlags(Qt::TextSelectableByMouse);

    m_defaults = new QPushButton(QStringLiteral("Defaults"));
    m_defaults->setToolTip(QStringLiteral("Put every setting back to what the shipped file has.\n"
                                          "The TV's address and MAC are left as they are, and\n"
                                          "nothing is written until you save."));
    m_restore = new QPushButton(QStringLiteral("Restore"));
    m_restore->setToolTip(QStringLiteral("Read the settings back from the file, dropping anything\n"
                                         "changed here but not saved."));
    m_save = new QPushButton(QStringLiteral("Save"));
    m_save->setDefault(true);
    m_save->setEnabled(false);
    auto *close = new QPushButton(QStringLiteral("Close"));

    auto *buttons = new QHBoxLayout;
    buttons->setSpacing(gap * 2 / 3);
    buttons->addWidget(m_logging);
    buttons->addStretch(1);
    buttons->addWidget(m_defaults);
    buttons->addWidget(m_restore);
    buttons->addWidget(m_save);
    buttons->addWidget(close);

    // Two columns: what the TV is and when it may be switched off on the left, the idle warning on
    // the right. The stretch under each column keeps its boxes at their natural height at the top,
    // rather than pulling the shorter column's boxes tall to fill the gap.
    auto *left = new QVBoxLayout;
    left->setSpacing(gap);
    left->addWidget(tvBox);
    left->addWidget(offBox);
    left->addStretch(1);

    auto *right = new QVBoxLayout;
    right->setSpacing(gap);
    right->addWidget(warnBox);
    right->addStretch(1);

    auto *columns = new QHBoxLayout;
    columns->setSpacing(gap);
    columns->addLayout(left, 1);
    columns->addLayout(right, 1);

    // The status line and the buttons span both columns: what they say and do is about the file,
    // not about either half of it.
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(gap, gap, gap, gap);
    layout->setSpacing(gap);
    layout->addLayout(columns);
    layout->addWidget(m_status);
    layout->addSpacing(gap / 3);
    layout->addLayout(buttons);

    // SHARED_TV without a valid HDMI_INPUT is a setting cli.py stands down from with a log line
    // nobody reads. Greying the box out says the same thing where the decision is being made.
    auto syncShared = [this] {
        const bool hasInput = m_hdmi->currentData().toInt() >= 1;
        m_shared->setEnabled(hasInput && m_editable);
        m_shared->setToolTip(hasInput ? QStringLiteral("The TV is then only turned off while it is showing "
                                                       "the input above, and one that is already on keeps its "
                                                       "picture when this computer wakes.")
                                      : QStringLiteral("Needs an HDMI input above: without one, lgpowercontrol "
                                                       "cannot tell whether the TV is showing this computer."));
    };
    connect(m_hdmi, &QComboBox::currentIndexChanged, this, syncShared);

    connect(close, &QPushButton::clicked, this, &QWidget::close);
    connect(m_save, &QPushButton::clicked, this, &ConfWindow::save);
    connect(m_restore, &QPushButton::clicked, this, &ConfWindow::restore);
    connect(m_defaults, &QPushButton::clicked, this, &ConfWindow::loadDefaults);

    connect(m_ipLock, &QAction::triggered, this, [this] {
        setFieldLocked(m_ip, m_ipLock, !m_ip->isReadOnly());
        if (!m_ip->isReadOnly())
            m_ip->setFocus();
    });
    connect(m_macLock, &QAction::triggered, this, [this] {
        setFieldLocked(m_mac, m_macLock, !m_mac->isReadOnly());
        if (!m_mac->isReadOnly())
            m_mac->setFocus();
    });

    for (QLineEdit *edit : {m_ip, m_mac})
        connect(edit, &QLineEdit::textEdited, this, &ConfWindow::updateDirty);
    connect(m_hdmi, &QComboBox::currentIndexChanged, this, &ConfWindow::updateDirty);
    for (QSpinBox *spin : {m_warningSpin, m_pollSpin})
        connect(spin, &QSpinBox::valueChanged, this, &ConfWindow::updateDirty);
    for (QCheckBox *box : {m_shared, m_suspend, m_shutdown, m_logging})
        connect(box, &QCheckBox::toggled, this, &ConfWindow::updateDirty);

    applyValues(values);
    syncShared();

    // The two spin boxes are matched to the wider of the pair - one of them carries "No warning" -
    // so the sliders beside them start and end at the same place.
    const int spinWidth = qMax(m_warningSpin->sizeHint().width(), m_pollSpin->sizeHint().width());
    m_warningSpin->setMinimumWidth(spinWidth);
    m_pollSpin->setMinimumWidth(spinWidth);
    setMinimumWidth(fontMetrics().averageCharWidth() * 108);  // two columns of the old single one

    if (!m_editable) {
        for (QWidget *widget : QList<QWidget *>{m_ip, m_mac, m_hdmi, m_shared, m_suspend, m_shutdown,
                                                m_warningSlider, m_warningSpin, m_pollSlider,
                                                m_pollSpin, m_logging})
            widget->setEnabled(false);
        setStatus(QStringLiteral(
            "%1 is not writable by you, so this window is read-only. That happens when the "
            "installer ran from a root login: SUDO_USER was unset, so the file stayed root-owned. "
            "Re-run sudo ./install.py from your own session, or edit the file with sudo.")
                      .arg(QString::fromLatin1(conf::FilePath)));
    }

    // Defaults match nothing on disk, so there is no baseline to be equal to: Save is offered at
    // once, because writing them back is the whole point of having been given them.
    if (valuesAreDefaults) {
        m_baseline.clear();
        updateDirty();
        setStatus(QStringLiteral("Showing the built-in defaults. Saving them rewrites every setting "
                                 "in %1, which also repairs the line that could not be read.")
                      .arg(QString::fromLatin1(conf::FilePath)));
    }
}

void ConfWindow::setStatus(const QString &text)
{
    m_status->setText(text);
}

// Fills the widgets from a set of values. becomesBaseline says whether those values are now what
// the file holds: true for the first load and for Restore, false for Defaults, which puts values
// on screen that disagree with the file on purpose - and so has to leave Save offered.
void ConfWindow::applyValues(const QMap<QString, QString> &values, bool becomesBaseline)
{
    m_ip->setText(values.value(conf::KeyIp));
    m_mac->setText(values.value(conf::KeyMac));

    // A conf naming an input past the end of the list gets that input added rather than snapped
    // to something the list does have: this window may not overrule a working setup it does not
    // recognise. findData() finds it again on the next load, so reloading cannot pile up copies.
    const int hdmi = readHdmi(values);
    int hdmiIndex = m_hdmi->findData(hdmi);
    if (hdmiIndex < 0) {
        m_hdmi->addItem(QString::number(hdmi), hdmi);
        hdmiIndex = m_hdmi->count() - 1;
    }
    m_hdmi->setCurrentIndex(hdmiIndex);

    m_shared->setChecked(readEnabled(values, conf::KeyShared));
    m_suspend->setChecked(readNotDisabled(values, conf::KeySuspend));
    m_shutdown->setChecked(readNotDisabled(values, conf::KeyShutdown));
    m_warningSpin->setValue(readInt(values, conf::KeyWarning, 120, true));
    m_pollSpin->setValue(readInt(values, conf::KeyPoll, 5));
    m_logging->setChecked(readEnabled(values, conf::KeyLogging));

    // Re-locked on every load: an unlocked address field left open across a Reload is exactly the
    // accident the padlock is there to prevent.
    setFieldLocked(m_ip, m_ipLock, true);
    setFieldLocked(m_mac, m_macLock, true);

    if (becomesBaseline)
        m_baseline = collect();
    updateDirty();
}

void ConfWindow::updateDirty()
{
    m_save->setEnabled(m_editable && collect() != m_baseline);
}

QMap<QString, QString> ConfWindow::collect() const
{
    QMap<QString, QString> values;
    values[conf::KeyIp] = m_ip->text().trimmed();
    values[conf::KeyMac] = m_mac->text().trimmed();
    // 0 is the list's "not configured", and the file spells that as an empty value. Writing a
    // literal "0" would read the same way to cli.py but contradict the comment in the file itself.
    const int hdmi = m_hdmi->currentData().toInt();
    values[conf::KeyHdmi] = hdmi >= 1 ? QString::number(hdmi) : QString();
    values[conf::KeyShared] = m_shared->isChecked() ? QStringLiteral("1") : QStringLiteral("0");
    values[conf::KeySuspend] = m_suspend->isChecked() ? QStringLiteral("1") : QStringLiteral("0");
    values[conf::KeyShutdown] = m_shutdown->isChecked() ? QStringLiteral("1") : QStringLiteral("0");
    values[conf::KeyWarning] = QString::number(m_warningSpin->value());
    values[conf::KeyPoll] = QString::number(m_pollSpin->value());
    values[conf::KeyLogging] = m_logging->isChecked() ? QStringLiteral("1") : QStringLiteral("0");
    return values;
}

// Only the two free-text fields can be wrong in a way the file cannot express. Everything else is
// a widget whose range is the validation. An empty address is refused here because install.py
// refuses it too - a saved conf that lgpowercontrol exits on is not a saved conf.
bool ConfWindow::validate(QString *error) const
{
    const QString ip = m_ip->text().trimmed();
    if (ip.isEmpty() || !isIpv4(ip)) {
        *error = QStringLiteral("Invalid IP-address");
        return false;
    }

    // send_wol() in cli.py strips ':' and '-' and needs exactly six bytes; anything else makes it
    // return without sending, so it is worth catching here rather than at 3 a.m. on a dark TV.
    const QString mac = m_mac->text().trimmed();
    if (!mac.isEmpty()) {
        QString hex = mac;
        hex.remove(QLatin1Char(':'));
        hex.remove(QLatin1Char('-'));
        static const QRegularExpression sixBytes(QStringLiteral("^[0-9a-fA-F]{12}$"));
        if (!sixBytes.match(hex).hasMatch()) {
            *error = QStringLiteral("Invalid MAC-address");
            return false;
        }
    }
    return true;
}

// Returns whether the file was written. closeEvent() needs to know: a save refused for a bad
// address must not let the window close as if it had gone through.
bool ConfWindow::save()
{
    QString error;
    if (!validate(&error)) {
        QMessageBox::warning(this, QStringLiteral("Nothing saved"), error);
        return false;
    }

    const QMap<QString, QString> values = collect();
    const conf::SaveResult result = conf::save(QString::fromLatin1(conf::FilePath), values);
    if (!result.ok) {
        QMessageBox::critical(this, QStringLiteral("Nothing saved"),
                              QStringLiteral("%1 was left unchanged:\n\n%2")
                                  .arg(QString::fromLatin1(conf::FilePath), result.error));
        return false;
    }

    noteWhatWentStale(values);
    m_baseline = values;
    updateDirty();

    QString message = QStringLiteral("Settings saved.");
    if (!result.missing.isEmpty()) {
        // Keys are never appended: the file is the contract, and one that has lost a key is a file
        // to look at by hand, not one to patch up from here.
        message += QStringLiteral(" These keys are missing from the file and were left out: %1. "
                                  "Re-run the installer to get a complete conf.")
                       .arg(result.missing.join(QStringLiteral(", ")));
    }
    setStatus(message);

    // Last, so that a failure below has the final word on the status line. Applying the change is
    // part of saving it, so a restart that worked says nothing: it is not news. A failure is,
    // because then the file and what the service is acting on have come apart.
    restartStaleServices();
    return true;
}

// Both the Close button and the window manager's own close land here, so unsaved work is caught in
// one place whichever way the window is being shut. Cancel and a refused save both leave it open.
void ConfWindow::closeEvent(QCloseEvent *event)
{
    if (!m_editable || collect() == m_baseline) {
        event->accept();
        return;
    }

    const QMessageBox::StandardButton answer = QMessageBox::warning(
        this, QStringLiteral("Unsaved changes"),
        QStringLiteral("The settings have been changed but not saved.\n\n"
                       "Closing now leaves %1 as it was.")
            .arg(QString::fromLatin1(conf::FilePath)),
        QMessageBox::Save | QMessageBox::Discard | QMessageBox::Cancel, QMessageBox::Save);

    if (answer == QMessageBox::Discard)
        event->accept();
    else if (answer == QMessageBox::Save && save())
        event->accept();
    else
        event->ignore();
}

// Which service is now out of date, worked out from what each one actually keeps in memory rather
// than from "something changed":
//
//   LGTV_IP, LGTV_MAC, HDMI_INPUT, SHARED_TV, POWER_OFF_AT_*
//       Read by cli.py in a fresh process on every single invocation, so a save is live at once.
//       Even the monitor makes no TV decision itself: it runs LGPC_BIN as a subprocess
//       (monitor.py:35-37), and that subprocess loads the conf on its own.
//   OFF_WARNING_SECONDS, NOTIFY_POLL_SECONDS
//       Read once in notify.main() (notify.py:108, 139) and held for the life of the process.
//       That is the user unit, so restarting it needs no authorisation.
//   LOGGING
//       Read once per process by Logger(), so both long-running services hold their own copy.
//       This is the only key that can make the system monitor stale, and therefore the only one
//       that ever leads to a password prompt.
//
// The remaining units - boot, shutdown and sleep - are oneshots. "Restarting" one would run it,
// which means turning the TV on or off. They are never touched here.
void ConfWindow::noteWhatWentStale(const QMap<QString, QString> &saved)
{
    for (auto it = saved.constBegin(); it != saved.constEnd(); ++it) {
        if (it.value() == m_baseline.value(it.key()))
            continue;
        if (it.key() == QLatin1String(conf::KeyWarning) || it.key() == QLatin1String(conf::KeyPoll))
            m_notifyStale = true;
        if (it.key() == QLatin1String(conf::KeyLogging)) {
            m_notifyStale = true;
            m_monitorStale = true;
        }
    }
}

// Both services, straight from save(). Each flag is cleared as it is dealt with, so a service that
// is not running is simply dropped - it reads the file when it next starts - and nothing is left
// pending for the user to remember. Silence means it worked; only failures reach the status line.
//
// The monitor runs as root, so its restart goes through pkexec and puts an authorisation dialog on
// screen. That happens for the logging setting and nothing else, which is why an ordinary save is
// never interrupted by a password prompt. pkexec runs asynchronously: waiting for that dialog
// synchronously would freeze this window behind it.
void ConfWindow::restartStaleServices()
{
    if (m_notifyStale) {
        m_notifyStale = false;
        if (unitIsActive(true, QString::fromLatin1(NotifyUnit))) {
            const int code = QProcess::execute(QStringLiteral("systemctl"),
                                               {QStringLiteral("--user"), QStringLiteral("restart"),
                                                QString::fromLatin1(NotifyUnit)});
            if (code != 0)
                setStatus(QStringLiteral("Settings saved, but %1 did not restart (exit %2), so it "
                                         "is still using the old values. Run "
                                         "systemctl --user restart %1 by hand.")
                              .arg(QString::fromLatin1(NotifyUnit))
                              .arg(code));
        }
    }

    if (!m_monitorStale)
        return;
    m_monitorStale = false;
    if (!unitIsActive(false, QString::fromLatin1(MonitorUnit)))
        return;

    auto *pkexec = new QProcess(this);
    connect(pkexec, &QProcess::finished, this, [this, pkexec](int code, QProcess::ExitStatus status) {
        if (status != QProcess::NormalExit || code != 0) {
            // 126 is pkexec's "dismissed or not authorised" and 127 its "could not run at all";
            // both are reported rather than swallowed, because a silent success here would mean
            // the monitor keeps logging - or not logging - against what the file now says.
            const QString reason = code == 126 ? QStringLiteral("authorisation was declined")
                                   : code == 127 ? QStringLiteral("pkexec could not run systemctl")
                                                 : QStringLiteral("systemctl exited with %1").arg(code);
            setStatus(QStringLiteral("Settings saved, but %1 did not restart: %2. Its own log lines "
                                     "keep the old setting until you run "
                                     "sudo systemctl restart %1.")
                          .arg(QString::fromLatin1(MonitorUnit), reason));
        }
        pkexec->deleteLater();
    });
    pkexec->start(QStringLiteral("pkexec"),
                  {QStringLiteral("systemctl"), QStringLiteral("restart"), QString::fromLatin1(MonitorUnit)});
}

// Starting over is reading the file again, not putting back something this window remembered: the
// file is the only thing that says what this machine's settings are, and it may have been edited
// by hand or reinstalled since this window opened.
void ConfWindow::restore()
{
    const QString path = QString::fromLatin1(conf::FilePath);
    const conf::LoadResult loaded = conf::load(path);
    if (!loaded.ok) {
        QMessageBox::critical(this, QStringLiteral("Nothing reloaded"),
                              QStringLiteral("%1 could not be read:\n\n%2").arg(path, loaded.error));
        return;
    }

    const bool discarded = collect() != m_baseline;
    applyValues(loaded.values);
    setStatus(discarded ? QStringLiteral("Read back from the file. Unsaved changes were discarded.")
                        : QStringLiteral("Read back from the file."));
}

// The shipped defaults, on screen but not on disk - Save is left offered, and Restore is still the
// way back. The address and the MAC are deliberately kept: they are this machine's identity for the
// TV rather than a preference with a sensible default, which is why the shipped file has them empty
// and why they sit behind padlocks. Wiping them from under a button labelled "Defaults" is the
// accident those padlocks exist to prevent.
void ConfWindow::loadDefaults()
{
    QMap<QString, QString> values = conf::defaults();
    values[QString::fromLatin1(conf::KeyIp)] = m_ip->text();
    values[QString::fromLatin1(conf::KeyMac)] = m_mac->text();

    applyValues(values, false);
    setStatus(QStringLiteral("Defaults loaded, but not saved. Restore reads the file back."));
}

// Naming a .desktop file is how a program tells the desktop who it is: it becomes the Wayland app
// id that matches the window to its icon in the task manager, and the id Qt registers with
// xdg-desktop-portal. The portal resolves that id to an installed .desktop file *and* checks that
// the running binary is the one the file's Exec names - measured on Qt 6.11, both halves are
// required - so a copy started from the build tree cannot satisfy it and warns on every start:
//
//   qt.qpa.services: Failed to register with host portal ... App info not found for ...
//
// The identity is therefore claimed only when it is true. An installed copy keeps its icon and its
// portal registration; a development build is simply anonymous, which costs it nothing, since this
// program opens no portal dialogs of its own.
void claimDesktopIdentityIfInstalled(QApplication &app)
{
    const QString entry = QStandardPaths::locate(QStandardPaths::ApplicationsLocation,
                                                 QStringLiteral("lgpowercontrol-gui.desktop"));
    if (entry.isEmpty())
        return;

    QFile file(entry);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return;

    QTextStream in(&file);
    while (!in.atEnd()) {
        const QString line = in.readLine();
        if (!line.startsWith(QLatin1String("Exec=")))
            continue;
        // The first token of Exec is the program; the rest are arguments and field codes.
        const QString program = line.mid(5).section(QLatin1Char(' '), 0, 0);
        const QString resolved = QStandardPaths::findExecutable(program);
        if (!resolved.isEmpty()
            && QFileInfo(resolved).canonicalFilePath()
                   == QFileInfo(app.applicationFilePath()).canonicalFilePath())
            app.setDesktopFileName(QStringLiteral("lgpowercontrol-gui"));
        return;
    }
}

}  // namespace

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("lgpowercontrol-gui"));
    app.setApplicationDisplayName(QStringLiteral("LGPowerControl Settings"));
    app.setWindowIcon(QIcon::fromTheme(QStringLiteral("video-television")));
    claimDesktopIdentityIfInstalled(app);

    const QString path = QString::fromLatin1(conf::FilePath);

    // The conf is never created here. It is written by install.py, and a file conjured up by this
    // program would be one with no services reading it and no pairing key beside it.
    if (!QFileInfo::exists(path)) {
        QMessageBox::critical(nullptr, QStringLiteral("LGPowerControl Settings"),
                              QStringLiteral("%1 does not exist.\n\nLGPowerControl does not look "
                                             "installed. Install it first.")
                                  .arg(path));
        return 1;
    }

    // An unreadable conf is usually one line with an unclosed quote, which stops lgpowercontrol
    // itself from starting too. Offering the shipped defaults turns this window into the way out:
    // saving them rewrites every key's line, and that is exactly what repairs the broken one.
    conf::LoadResult loaded = conf::load(path);
    const bool usingDefaults = !loaded.ok;
    if (usingDefaults) {
        QMessageBox box(QMessageBox::Critical, QStringLiteral("LGPowerControl Settings"),
                        QStringLiteral("%1 could not be read:\n\n%2").arg(path, loaded.error));
        QPushButton *useDefaults = box.addButton(QStringLiteral("Load defaults"),
                                                 QMessageBox::AcceptRole);
        box.addButton(QStringLiteral("Quit"), QMessageBox::RejectRole);
        box.setDefaultButton(useDefaults);
        box.exec();
        if (box.clickedButton() != useDefaults)
            return 1;
        loaded.values = conf::defaults();
    }

    ConfWindow window(loaded.values, conf::writable(path), usingDefaults);
    window.show();
    return app.exec();
}
