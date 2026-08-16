// Reading and writing the installed lgpowercontrol.conf. Deliberately free of widget code: the
// parsing has to mirror load_conf() in src/lgpowercontrol/common.py exactly rather than merely
// plausibly, and that argument is easier to follow on its own.
#pragma once

#include <QMap>
#include <QString>
#include <QStringList>

namespace conf {

// The installed copy is the only one this program edits. The repo template is not a target: its
// values never reach a running service, so editing it would look like it worked and do nothing.
inline const char *const FilePath = "/opt/lgpowercontrol/lgpowercontrol.conf";

// Every key the code in src/ reads. Anything outside this list is left alone by the writer, and
// no key is ever added to the file - tests/test_common.py::ShippedConfTest asserts the shipped
// key set, and install.py:139 explains why a key naming a path or a command would be worse still.
inline const char *const KeyIp        = "LGTV_IP";
inline const char *const KeyMac       = "LGTV_MAC";
inline const char *const KeyHdmi      = "HDMI_INPUT";
inline const char *const KeyShared    = "SHARED_TV";
inline const char *const KeySuspend   = "POWER_OFF_AT_SUSPEND";
inline const char *const KeyShutdown  = "POWER_OFF_AT_SHUTDOWN";
inline const char *const KeyWarning   = "OFF_WARNING_SECONDS";
inline const char *const KeyPoll      = "NOTIFY_POLL_SECONDS";
inline const char *const KeyLogging   = "LOGGING";

struct LoadResult {
    bool ok = false;
    QString error;
    QMap<QString, QString> values;
};

// missing lists keys that are not in the file. The writer never appends them, matching
// set_conf_value() in install.py ("a key the template lacks is silently ignored") - except that
// here it is reported instead of silent, because a GUI that says "saved" must not have dropped one.
struct SaveResult {
    bool ok = false;
    QString error;
    QStringList missing;
};

LoadResult load(const QString &path);
SaveResult save(const QString &path, const QMap<QString, QString> &values);
bool writable(const QString &path);

// The values the shipped template carries, offered when the installed file cannot be read at all.
// Held in step with lgpowercontrol.conf by hand - the template's own values are pinned by
// tests/test_common.py::ShippedConfTest, so a disagreement between the two shows up there first.
QMap<QString, QString> defaults();

}  // namespace conf
