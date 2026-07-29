"""Shared code for every lgtvpc script: paths, conf parsing, logging, and the
small D-Bus/systemd-run helpers used by more than one script.

Installed to /opt/lgtvpc/ alongside everything else. Scripts that live
elsewhere (the NM dispatcher hook, the systemd-sleep hook) are not in that
directory at run time, so they add /opt/lgtvpc to sys.path before importing
this - see the comment in each.
"""
import re
import shlex
import subprocess
import sys
import syslog
from pathlib import Path

INSTALL_DIR = Path("/opt/lgtvpc")
CONF_FILE = INSTALL_DIR / "lgtvpc.conf"
PAIRING_DB = INSTALL_DIR / ".aiopylgtv.sqlite"
LGTVPC = INSTALL_DIR / "lgtvpc"
VERSION_FILE = INSTALL_DIR / "VERSION"
COMMIT_FILE = INSTALL_DIR / "COMMIT"

# Present when install.py enabled NIC Wake-on-LAN (the user said yes to the
# installer's question), so uninstall.py knows to revert it. Preserved across
# reinstalls/updates like the pairing DB.
NIC_WOL_MARKER = INSTALL_DIR / ".nic-wol-enabled"

ON_LOCK = Path("/run/lgtvpc-on.lock")
TV_OFF_FLAG = Path("/run/lgtvpc-tv-off")
SLEEP_FLAG = Path("/run/lgtvpc-sleep")
HOOK_SLEEP_FLAG = Path("/run/lgtvpc-hook-sleep")

REPO = "bassidus/lgpowercontrol"


def require_root() -> None:
    import os

    if os.geteuid() != 0:
        sys.exit("This script needs to be run as root or with sudo.")


def load_conf(path=CONF_FILE) -> dict[str, str]:
    conf = {}
    with open(path) as f:
        for line in f:
            tokens = shlex.split(line, comments=True)
            if not tokens or "=" not in tokens[0]:
                continue
            key, _, value = tokens[0].partition("=")
            conf[key] = value
    return conf


def conf_int(conf: dict[str, str], key: str, default: int, allow_zero: bool = False) -> int:
    """A conf value that must be a non-negative integer (or, unless
    allow_zero, a positive one); anything else (missing, non-numeric,
    negative, or zero when not allowed) silently falls back to default."""
    value = conf.get(key, "")
    if not value.isdigit():
        return default
    n = int(value)
    if n == 0 and not allow_zero:
        return default
    return n


class Logger:
    """`log = Logger("dpms-monitor")`, then `log("message")` tags and sends
    it via syslog. Call `.configure(conf)` once conf is loaded to honor
    `LOGGING="no"` - logging defaults to on until then."""

    def __init__(self, tag: str):
        self.tag = tag
        self.enabled = True
        syslog.openlog("lgtvpc", 0, syslog.LOG_USER)

    def configure(self, conf: dict[str, str]) -> None:
        self.enabled = conf.get("LOGGING") != "no"

    def __call__(self, msg: str) -> None:
        if self.enabled:
            syslog.syslog(syslog.LOG_INFO, f"{self.tag}: {msg}")


def preparing_for_sleep() -> bool:
    result = subprocess.run(
        [
            "busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", "PreparingForSleep",
        ],
        capture_output=True, text=True,
    )
    return "true" in result.stdout


def notify_send(summary: str, body: str, timeout_ms: int = 0) -> int:
    """Sends a desktop notification via busctl (no libnotify/D-Bus library
    dependency) and returns its id, or 0 if sending failed."""
    result = subprocess.run(
        [
            "busctl", "--user", "call", "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
            "susssasa{sv}i", "LGPowerControl", "0", "video-television", summary, body,
            "0", "0", str(timeout_ms),
        ],
        capture_output=True, text=True,
    )
    m = re.search(r"\d+", result.stdout)
    return int(m.group()) if m else 0


def notify_close(notif_id: int) -> None:
    if not notif_id:
        return
    subprocess.run(
        [
            "busctl", "--user", "call", "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
            "CloseNotification", "u", str(notif_id),
        ],
        capture_output=True,
    )


def run_detached(*args: str, env: dict[str, str] | None = None) -> None:
    """Launches a command via systemd-run --collect so it can outlive and
    run independent of the caller (used for ON, which can retry for up to a
    minute and must not block the NM dispatcher queue or a sleep hook)."""
    cmd = ["systemd-run", "--quiet", "--collect"]
    if env:
        cmd += [f"--setenv={key}={value}" for key, value in env.items()]
    cmd += list(args)
    subprocess.run(cmd)
