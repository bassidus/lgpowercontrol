# Shared code for every lgtvpc script: paths, conf parsing, logging, and the
# small nmcli/D-Bus/systemd-run helpers used by more than one script.
#
# Installed to /opt/lgtvpc/ alongside everything else. Scripts that live
# elsewhere (the NM dispatcher hook, the systemd-sleep hook) are not in that
# directory at run time, so they add /opt/lgtvpc to sys.path before importing
# this - see the comment in each.
import os
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


# A conf value that must be a non-negative integer (or, unless allow_zero, a
# positive one); anything else (missing, non-numeric, negative, or zero when
# not allowed) silently falls back to default.
def conf_int(conf: dict[str, str], key: str, default: int, allow_zero: bool = False) -> int:
    value = conf.get(key, "")
    if not value.isdigit():
        return default
    n = int(value)
    if n == 0 and not allow_zero:
        return default
    return n


# log = Logger("dpms-monitor"), then log("message") tags and sends it via
# syslog. Call .configure(conf) once conf is loaded to honor LOGGING="no" -
# logging defaults to on until then.
class Logger:
    def __init__(self, tag: str):
        self.tag = tag
        self.enabled = True
        syslog.openlog("lgtvpc", 0, syslog.LOG_USER)

    def configure(self, conf: dict[str, str]) -> None:
        self.enabled = conf.get("LOGGING") != "no"

    def __call__(self, msg: str) -> None:
        if self.enabled:
            syslog.syslog(syslog.LOG_INFO, f"{self.tag}: {msg}")


# Runs nmcli and returns its output, or "" if it failed (NetworkManager not
# installed, unknown device, ...). Callers decide what an empty answer means
# - lgtvpc-wol.py errors out, install.py skips its question.
def nmcli(*args: str) -> str:
    result = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


# Every wired (ethernet) network device NetworkManager knows about.
def wired_devices() -> list[str]:
    out = nmcli("-g", "DEVICE,TYPE", "device", "status")
    return [line.split(":")[0] for line in out.splitlines() if line.endswith(":ethernet")]


# The device's active connection, or "" when it has none.
def connection_for(device: str) -> str:
    con = nmcli("-g", "GENERAL.CONNECTION", "device", "show", device)
    return "" if con == "--" else con


# The connection profile's 802-3-ethernet.wake-on-lan value ("magic" when
# enabled). Note this is the saved profile, not necessarily what the card is
# running with right now - see lgtvpc-wol.py.
def wol_setting(con: str) -> str:
    return nmcli("-g", "802-3-ethernet.wake-on-lan", "connection", "show", con)


# GET api.github.com/repos/<REPO>/<path> as parsed JSON. Raises OSError/
# ValueError as urllib/json do; callers decide (update.py exits with a
# message, update-check.py skips silently until the next tick).
def github_api(path: str, timeout: float = 15) -> dict:
    # Lazy imports: urllib pulls in the email package; the suspend-critical
    # importers of this module (dispatcher, sleep hook) shouldn't pay for it.
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


# Sends a desktop notification via busctl (no libnotify/D-Bus library
# dependency) and returns its id, or 0 if sending failed.
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


# Launches a command via systemd-run --collect so it can outlive and run
# independent of the caller (used for ON, which can retry for up to a minute
# and must not block the NM dispatcher queue or a sleep hook).
def run_detached(*args: str, env: dict[str, str] | None = None) -> None:
    cmd = ["systemd-run", "--quiet", "--collect"]
    if env:
        cmd += [f"--setenv={key}={value}" for key, value in env.items()]
    cmd += list(args)
    subprocess.run(cmd)


# TV-off at suspend, shared by the two fallback paths that cover setups
# where the NM dispatcher's pre-down never fires (NIC WoL enabled, so NM
# skips the device): the systemd-sleep hook (sleep.py) and the
# sleep-listener service. Callers have already established that the
# dispatcher did not handle this suspend (SLEEP_FLAG absent).
def fallback_tv_off(log: Logger, source: str) -> None:
    # Sets HOOK_SLEEP_FLAG - its own flag, not the dispatcher's: nothing
    # would clear the dispatcher's flag on these setups (no 'up' event
    # fires), and a stale sleep flag makes the monitor misbehave. Set before
    # the tv-off check so fallback_tv_on() turns the TV on even when the off
    # is skipped here.
    HOOK_SLEEP_FLAG.touch()

    # The monitor's 10-min escalation may already have powered the TV off; a
    # second power_off would hang against a standby TV until the connect
    # timeout and delay suspend.
    if TV_OFF_FLAG.exists():
        log("System going to sleep (dispatcher pre-down did not fire) - TV already off, skipping")
        return

    log("System going to sleep (dispatcher pre-down did not fire), turning TV off")

    # --retries 1: on setups where the network IS torn down at sleep (e.g.
    # bridges), one fast failed attempt beats a full retry cycle holding up
    # suspend / eating the listener's inhibitor budget.
    subprocess.run([LGTVPC, "--retries", "1", "OFF"], env=dict(os.environ, LGPC_SOURCE=source))


# Resume half of the sleep fallback: turn the TV on, but only when
# fallback_tv_off() handled the matching suspend.
def fallback_tv_on(log: Logger, source: str) -> None:
    if not HOOK_SLEEP_FLAG.exists():
        return
    HOOK_SLEEP_FLAG.unlink()

    log("System woke up, turning TV on")

    # Detached: ON can retry for a while and must not hold up resume or the
    # caller's signal handling.
    run_detached(str(LGTVPC), "ON", env={"LGPC_SOURCE": source})
