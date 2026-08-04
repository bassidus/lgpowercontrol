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
WOL          = VENV_DIR    / "bin" / "lgpowercontrol-wol"

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


# Tagged syslog line; call .configure(conf) to honor LOGGING="off".
class Logger:
    def __init__(self, tag: str):
        self.tag = tag
        self.enabled = True
        syslog.openlog("lgpowercontrol", 0, syslog.LOG_USER)

    def configure(self, conf: dict[str, str]) -> None:
        self.enabled = conf.get("LOGGING") != "off"

    def __call__(self, msg: str) -> None:
        if self.enabled:
            syslog.syslog(syslog.LOG_INFO, f"{self.tag}: {msg}")


# "" means nmcli failed (not installed, unknown device, ...); callers decide what that means.
def nmcli(*args: str) -> str:
    try:
        result = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def wired_devices() -> list[str]:
    out = nmcli("-g", "DEVICE,TYPE", "device", "status")
    return [line.split(":")[0] for line in out.splitlines() if line.endswith(":ethernet")]


def connection_for(device: str) -> str:
    con = nmcli("-g", "GENERAL.CONNECTION", "device", "show", device)
    return "" if con == "--" else con


# Saved profile value, not necessarily what the card runs now - see wol.py.
def wol_setting(con: str) -> str:
    return nmcli("-g", "802-3-ethernet.wake-on-lan", "connection", "show", con)


def github_api(path: str, timeout: float = 15) -> dict:
    # Lazy: avoids the urllib/email import cost for suspend-critical importers.
    import json
    import urllib.request

    with urllib.request.urlopen(f"https://api.github.com/repos/{REPO}/{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def preparing_for_sleep() -> bool:
    result = subprocess.run(
        [
            "busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", "PreparingForSleep",
        ],
        capture_output=True, text=True,
    )
    return "true" in result.stdout


# Via busctl, no libnotify dependency. Returns the notification id, or 0 on failure.
def notify_send(summary: str, body: str, timeout_ms: int = 0) -> int:
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


def notify_close(nid: int) -> None:
    if not nid:
        return
    subprocess.run(
        [
            "busctl", "--user", "call", "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
            "CloseNotification", "u", str(nid),
        ],
        capture_output=True,
    )


# Detached via systemd-run --collect, so a retrying ON doesn't block the dispatcher/hook.
def run_detached(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["systemd-run", "--quiet", "--collect"]
    if env:
        cmd += [f"--setenv={key}={value}" for key, value in env.items()]
    cmd += list(args)
    subprocess.run(cmd)


# Shared by sleep_hook.py/sleep_listener.py for setups where NM's pre-down never fires.
def fallback_tv_off(log: Logger, source: str) -> None:
    HOOK_SLEEP_FLAG.touch()  # own flag: dispatcher's flag has no 'up' event to clear it here

    if TV_OFF_FLAG.exists():  # monitor's 10-min escalation may have already turned it off
        log("System going to sleep (dispatcher pre-down did not fire) - TV already off, skipping")
        return

    log("System going to sleep (dispatcher pre-down did not fire), turning TV off")
    # retries=1: on setups where the network IS torn down (bridges), fail fast instead of stalling suspend
    subprocess.run([LGPC, "--retries", "1", "OFF"], env=dict(os.environ, LGPC_SOURCE=source))


def fallback_tv_on(log: Logger, source: str) -> None:
    if not HOOK_SLEEP_FLAG.exists():
        return
    HOOK_SLEEP_FLAG.unlink()
    log("System woke up, turning TV on")
    run_detached(str(LGPC), "ON", env={"LGPC_SOURCE": source})
