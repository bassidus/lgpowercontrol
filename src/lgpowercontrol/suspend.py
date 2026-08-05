# One behavior (turn the TV off before suspend, back on at resume), three entry points for
# the three ways a system can tell us it's about to sleep - see CLAUDE.md section 5 for why
# each exists and why their guards below are not duplication:
#
#   dispatcher()  NetworkManager pre-down/up       - the primary path, network still up
#   hook()        systemd-sleep pre/post           - NIC-WoL setups where NM skips the device
#   listener()    busctl monitor + delay inhibitor - immutable /usr, hook() can't be installed
#
# Each entry point is its own installed script/process, so each gets its own tagged Logger
# rather than sharing one - that tag is what tells the three apart in journalctl.
import os
import subprocess
import sys
import time
from pathlib import Path

from lgpowercontrol.common import (
    CONF_FILE,
    HOOK_SLEEP_FLAG,
    LGPC,
    SLEEP_FLAG,
    TV_OFF_FLAG,
    Logger,
    preparing_for_sleep,
    run_detached,
)

log_dispatcher = Logger("nm-dispatcher")
log_hook = Logger("sleep-hook")
log_listener = Logger("sleep-listener")

MATCH = (
    "type='signal',sender='org.freedesktop.login1',"
    "path='/org/freedesktop/login1',"
    "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"
)


# retries=None means don't pass --retries at all, i.e. use LGPC's own default.
def _tv_off(log: Logger, source: str, flag: Path, retries: int | None) -> None:
    flag.touch()  # own flag: cleared by _tv_on on the matching wake path

    if TV_OFF_FLAG.exists():  # monitor's 10-min escalation may have already turned it off
        log("System going to sleep - TV already off, skipping")
        return

    log("System going to sleep, turning TV off")
    cmd = [LGPC, *(["--retries", str(retries)] if retries is not None else []), "OFF"]
    subprocess.run(cmd, env=dict(os.environ, LGPC_SOURCE=source))


def _tv_on(log: Logger, source: str, flag: Path) -> None:
    if not flag.exists():
        return
    flag.unlink(missing_ok=True)
    log("System woke up, turning TV on")
    run_detached(str(LGPC), "ON", env={"LGPC_SOURCE": source})


# NM dispatcher script: called as <name> <interface> <action>. Symlinked into pre-down.d/
# too, to receive both pre-down (blocking, network still up) and up (post-resume) events.
def dispatcher() -> None:
    action = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "pre-down":
        if not preparing_for_sleep():
            return

        if SLEEP_FLAG.exists():  # fires once per NIC; only act on the first (cleared by 'up')
            return  # NM runs dispatcher scripts serially, so this check can't race
        _tv_off(log_dispatcher, "nm-dispatcher", SLEEP_FLAG, None)

    elif action == "up":
        _tv_on(log_dispatcher, "resume", SLEEP_FLAG)  # detached: dispatcher runs sequentially


# systemd-sleep hook: lgpowercontrol pre|post suspend|.... Fallback TV-off/on for NIC-WoL
# setups where NM skips the device and the dispatcher never fires (see CLAUDE.md).
def hook() -> None:
    if not os.access(CONF_FILE, os.R_OK):  # conf gone: project removed, hook left behind somehow
        return

    phase = sys.argv[1] if len(sys.argv) > 1 else ""

    if phase == "pre":
        if SLEEP_FLAG.exists():  # dispatcher's pre-down already handled this suspend
            return
        _tv_off(log_hook, "sleep-hook", HOOK_SLEEP_FLAG, 1)

    elif phase == "post":
        _tv_on(log_hook, "sleep-hook", HOOK_SLEEP_FLAG)


# killing the child process releases the lock; avoids the fd-passing D-Bus call
# that taking it directly would need (no D-Bus library in the stdlib)
def take_inhibitor() -> subprocess.Popen:
    return subprocess.Popen(
        [
            "systemd-inhibit", "--what=sleep", "--mode=delay", "--who=lgpowercontrol",
            "--why=Turning the TV off before suspend", "sleep", "infinity",
        ]
    )


# Fallback TV-off/on for immutable distros where hook() can't be installed (read-only /usr).
# See CLAUDE.md ("Immutable-OS fallback") for why a delay inhibitor is safe here despite being
# a dead end elsewhere, and why the grace wait below is needed.
def listener() -> None:
    inhibitor = take_inhibitor()
    monitor = subprocess.Popen(
        ["busctl", "--system", "monitor", "--match", MATCH],
        stdout=subprocess.PIPE,
        text=True,
    )

    # the match above means the only BOOLEAN lines are PrepareForSleep's payload
    for line in monitor.stdout:
        if "BOOLEAN true" in line:
            for _ in range(10):  # grace wait: let pre-down's flag win the race if it fires
                if SLEEP_FLAG.exists():
                    break
                time.sleep(0.1)
            else:
                _tv_off(log_listener, "sleep-listener", HOOK_SLEEP_FLAG, 1)
            inhibitor.terminate()
            inhibitor.wait()
        elif "BOOLEAN false" in line:
            inhibitor = take_inhibitor()  # re-arm before turning the TV on
            _tv_on(log_listener, "sleep-listener", HOOK_SLEEP_FLAG)

    sys.exit(1)  # busctl exiting means the system bus went away; let systemd restart us
