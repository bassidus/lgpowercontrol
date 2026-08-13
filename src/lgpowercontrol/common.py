# Shared helpers, importable by every module in this package.
import os
import re
import shlex
import subprocess
import sys
import syslog
from pathlib import Path

INSTALL_DIR  = Path("/opt/lgpowercontrol")
CONF_FILE    = INSTALL_DIR / "lgpowercontrol.conf"
PAIRING_DB   = INSTALL_DIR / ".aiopylgtv.sqlite"
# A plain package directory rather than a venv: a venv embeds a copy of the system Python and a
# version-stamped lib/pythonX.Y path, so a distro upgrade to a new Python leaves it unable to
# start at all - the copied binary still links the old libpython. The one compiled extension in
# the tree (websockets' speedups) falls back to pure Python when its version tag stops matching.
LIB_DIR      = INSTALL_DIR / "lib"
BIN_DIR      = INSTALL_DIR / "bin"
LGPC_BIN     = BIN_DIR     / "lgpowercontrol"

TV_OFF_FLAG     = Path("/run/lgpowercontrol-tv-off")
SLEEP_FLAG      = Path("/run/lgpowercontrol-sleep")
HOOK_SLEEP_FLAG = Path("/run/lgpowercontrol-hook-sleep")


def require_root() -> None:
    if os.geteuid() != 0:
        sys.exit("This script needs to be run as root or with sudo.")


def load_conf(path: Path | str = CONF_FILE) -> dict[str, str]:
    conf = {}
    with open(path) as f:
        for line in f:
            tokens = shlex.split(line, comments=True)
            if not tokens or "=" not in tokens[0]:
                continue
            key, _, value = tokens[0].partition("=")
            conf[key] = value
    return conf


# Missing/non-numeric/negative, or zero unless allow_zero, falls back to default. allow_zero
# exists because a configured 0 once read as "unset", breaking the documented way to disable
# a feature by setting it to zero - never make zero mean default for such a setting again.
def conf_int(conf: dict[str, str], key: str, default: int, allow_zero: bool = False) -> int:
    value = conf.get(key, "")
    if not value.isdigit():
        return default
    parsed = int(value)
    if parsed == 0 and not allow_zero:
        return default
    return parsed


# Tagged syslog line; reads LOGGING from conf at construction. Off unless the conf asks for it, so
# an install nobody has had trouble with stays out of the journal. "on" is accepted next to 1
# because that is what every conf written before 4.2 says, and those outlive an update whenever
# the user copies their settings back by hand. An unreadable conf logs without being asked: the
# program cannot work at all then, and the line is what says so.
class Logger:
    def __init__(self, tag: str):
        self.tag = tag
        try:
            conf = load_conf(CONF_FILE)
        except OSError:
            conf = {"LOGGING": "1"}
        self.enabled = conf.get("LOGGING", "").strip() in ("1", "on")
        syslog.openlog("lgpowercontrol", 0, syslog.LOG_USER)

    def __call__(self, msg: str) -> None:
        if self.enabled:
            syslog.syslog(syslog.LOG_INFO, f"{self.tag}: {msg}")


# "" means nmcli failed (not installed, unknown device, ...). check=True hard-fails instead,
# for callers where a silent no-op is worse than an error, e.g. changing a WoL setting.
def nmcli(*args: str, check: bool = False) -> str:
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        if check:
            sys.exit("nmcli not found.")
        return ""
    if result.returncode != 0:
        if check:
            sys.exit(result.stderr.strip() or f"Command failed: nmcli {' '.join(args)}")
        return ""
    return result.stdout.strip()


def wired_devices() -> list[str]:
    out = nmcli("-g", "DEVICE,TYPE", "device", "status")
    return [line.split(":")[0] for line in out.splitlines() if line.endswith(":ethernet")]


def connection_for(device: str) -> str:
    connection = nmcli("-g", "GENERAL.CONNECTION", "device", "show", device)
    return "" if connection == "--" else connection


# Saved profile value, not necessarily what the card runs now - see set_nic_wol() in admin.py.
# "magic" is on, "" (no flags) is off, and "default"/"ignore" both mean NetworkManager leaves the
# card's own setting alone, so they say nothing about whether it is on. Callers must not treat
# anything-but-magic as off; nic_wol() in admin.py reports the three cases apart.
def nic_wol_setting(connection: str) -> str:
    return nmcli("-g", "802-3-ethernet.wake-on-lan", "connection", "show", connection)


# (device, connection) when exactly one wired device with an active connection exists, else None.
# Callers that need to tell "none"/"several"/"no connection" apart re-check wired_devices() - the
# three cases share only that classification, and each caller words all three differently.
def sole_wired_connection() -> tuple[str, str] | None:
    devices = wired_devices()
    if len(devices) != 1:
        return None
    connection = connection_for(devices[0])
    return (devices[0], connection) if connection else None


def confirm(prompt: str, default: bool = True) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer.startswith("y")


# All D-Bus goes through the busctl binary: the stdlib has no D-Bus support, and a library
# for it would be a new dependency this project deliberately does without.
def busctl(*args: str) -> str:
    return subprocess.run(["busctl", *args], capture_output=True, text=True, check=False).stdout


def preparing_for_sleep() -> bool:
    return "true" in busctl(
        "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager", "PreparingForSleep",
    )


# Returns the notification id, or 0 on failure.
def notify_send(summary: str, body: str, timeout_ms: int = 0) -> int:
    out = busctl(
        "--user", "call", "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
        "susssasa{sv}i", "LGPowerControl", "0", "video-television", summary, body,
        "0", "0", str(timeout_ms),
    )
    match = re.search(r"\d+", out)
    return int(match.group()) if match else 0


def notify_close(notification_id: int) -> None:
    if not notification_id:
        return
    busctl(
        "--user", "call", "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
        "CloseNotification", "u", str(notification_id),
    )


# Detached via systemd-run --collect, so a retrying ON doesn't block the dispatcher/hook.
def run_detached(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["systemd-run", "--quiet", "--collect"]
    if env:
        cmd += [f"--setenv={key}={value}" for key, value in env.items()]
    cmd += list(args)
    subprocess.run(cmd, check=False)
