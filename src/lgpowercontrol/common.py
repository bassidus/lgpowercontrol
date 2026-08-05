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
VENV_DIR     = INSTALL_DIR / "bscpylgtv"
VERSION_FILE = INSTALL_DIR / "VERSION"
COMMIT_FILE  = INSTALL_DIR / "COMMIT"
LGPC         = VENV_DIR    / "bin" / "lgpowercontrol"

TV_OFF_FLAG     = Path("/run/lgpowercontrol-tv-off")
SLEEP_FLAG      = Path("/run/lgpowercontrol-sleep")
HOOK_SLEEP_FLAG = Path("/run/lgpowercontrol-hook-sleep")

REPO = "bassidus/lgpowercontrol"


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


# Missing/non-numeric/negative, or zero unless allow_zero, falls back to default.
def conf_int(conf: dict[str, str], key: str, default: int, allow_zero: bool = False) -> int:
    value = conf.get(key, "")
    if not value.isdigit():
        return default
    n = int(value)
    if n == 0 and not allow_zero:
        return default
    return n


# Tagged syslog line; reads LOGGING from conf at construction (missing/unreadable conf = on).
class Logger:
    def __init__(self, tag: str):
        self.tag = tag
        try:
            conf = load_conf(CONF_FILE)
        except OSError:
            conf = {}
        self.enabled = conf.get("LOGGING") != "off"
        syslog.openlog("lgpowercontrol", 0, syslog.LOG_USER)

    def __call__(self, msg: str) -> None:
        if self.enabled:
            syslog.syslog(syslog.LOG_INFO, f"{self.tag}: {msg}")


# "" means nmcli failed (not installed, unknown device, ...); check=True hard-fails instead,
# for callers where a silent no-op would be worse than an error (e.g. changing a WoL setting).
def nmcli(*args: str, check: bool = False) -> str:
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True)
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
    con = nmcli("-g", "GENERAL.CONNECTION", "device", "show", device)
    return "" if con == "--" else con


# Saved profile value, not necessarily what the card runs now - see wol.py.
def wol_setting(con: str) -> str:
    return nmcli("-g", "802-3-ethernet.wake-on-lan", "connection", "show", con)


# (device, connection) when exactly one wired device with an active connection exists,
# else None - callers that need to tell "none"/"several" apart in a message re-check wired_devices().
def sole_wired_connection() -> tuple[str, str] | None:
    devices = wired_devices()
    if len(devices) != 1:
        return None
    con = connection_for(devices[0])
    return (devices[0], con) if con else None


def confirm(prompt: str, default: bool = True) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer.startswith("y")


def github_api(path: str, timeout: float = 15) -> dict:
    # Lazy: avoids the urllib/email import cost for suspend-critical importers.
    import json
    import urllib.request

    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def busctl(*args: str) -> str:
    return subprocess.run(["busctl", *args], capture_output=True, text=True).stdout


def preparing_for_sleep() -> bool:
    return "true" in busctl(
        "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager", "PreparingForSleep",
    )


# Via busctl, no libnotify dependency. Returns the notification id, or 0 on failure.
def notify_send(summary: str, body: str, timeout_ms: int = 0) -> int:
    out = busctl(
        "--user", "call", "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
        "susssasa{sv}i", "LGPowerControl", "0", "video-television", summary, body,
        "0", "0", str(timeout_ms),
    )
    m = re.search(r"\d+", out)
    return int(m.group()) if m else 0


def notify_close(nid: int) -> None:
    if not nid:
        return
    busctl(
        "--user", "call", "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
        "CloseNotification", "u", str(nid),
    )


# Detached via systemd-run --collect, so a retrying ON doesn't block the dispatcher/hook.
def run_detached(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["systemd-run", "--quiet", "--collect"]
    if env:
        cmd += [f"--setenv={key}={value}" for key, value in env.items()]
    cmd += list(args)
    subprocess.run(cmd)
