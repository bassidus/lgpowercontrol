# One behavior (turn the TV off before suspend, back on at resume), three entry points for the
# three ways a system can tell us it's about to sleep. Their guards are not duplication: each
# path has to detect that another one already handled this suspend.
#
#   dispatcher()  NetworkManager pre-down/up       - the primary path, network still up
#   hook()        systemd-sleep pre/post           - NIC-WoL setups where NM skips the device
#   listener()    busctl monitor + delay inhibitor - immutable /usr, hook() can't be installed
#
# Why not one path: NetworkManager tears its connections down ~17 ms after logind's
# PrepareForSleep and won't wait for a foreign inhibitor, so only pre-down provably has the
# network up. Proven dead ends, don't retry any of them: sleep-target units (network already
# gone), an inhibitor held by us (delays the kernel, not NM), PowerDevil's aboutToSuspend
# (milliseconds of margin), and NetworkManager config options (no such setting exists).
#
# Each entry point is its own installed script/process, so each builds its own tagged Logger -
# that tag is what tells the three apart in journalctl. Built inside the function, not at import:
# a Logger reads the conf, and the dispatcher runs on every link event on every interface.
import os
import subprocess
import sys
import time
from pathlib import Path

from lgpowercontrol.common import (
    HOOK_SLEEP_FLAG,
    LGPC_BIN,
    SLEEP_FLAG,
    TV_OFF_FLAG,
    Logger,
    preparing_for_sleep,
    run_detached,
)

SLEEP_SIGNAL_MATCH = (
    "type='signal',sender='org.freedesktop.login1',"
    "path='/org/freedesktop/login1',"
    "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"
)


# tag names the entry point in the journal; source tags the lgpowercontrol child's own lines.
# retries=None means omit --retries entirely and use the lgpowercontrol command's own default.
def _tv_off(tag: str, source: str, flag: Path, retries: int | None) -> None:
    flag.touch()  # own flag: cleared by _tv_on on the matching wake path
    log = Logger(tag)

    if TV_OFF_FLAG.exists():  # monitor's 10-min escalation may have already turned it off
        log("System going to sleep - TV already off, skipping")
        return

    log("System going to sleep, turning TV off")
    cmd = [LGPC_BIN, *(["--retries", str(retries)] if retries is not None else []), "OFF"]
    subprocess.run(cmd, env=dict(os.environ, LGPC_SOURCE=source))


def _tv_on(tag: str, source: str, flag: Path) -> None:
    if not flag.exists():
        return  # before Logger(): 'up' also fires on boot and cable replug, where this is a no-op
    flag.unlink(missing_ok=True)
    Logger(tag)("System woke up, turning TV on")
    run_detached(str(LGPC_BIN), "ON", env={"LGPC_SOURCE": source})


# NM dispatcher script: called as <name> <interface> <action>. Symlinked into pre-down.d/
# too, to receive both pre-down (blocking, network still up) and up (post-resume) events.
def dispatcher() -> None:
    action = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "pre-down":
        if not preparing_for_sleep():
            return

        if SLEEP_FLAG.exists():  # fires once per NIC; only act on the first (cleared by 'up')
            return  # NM runs dispatcher scripts serially, so this check can't race
        _tv_off("nm-dispatcher", "nm-dispatcher", SLEEP_FLAG, None)

    elif action == "up":
        # detached, so a long retry sequence doesn't block the other dispatcher scripts
        _tv_on("nm-dispatcher", "resume", SLEEP_FLAG)


# systemd-sleep hook: lgpowercontrol pre|post suspend|.... Fallback TV-off/on for NIC-WoL
# setups where NM skips the device at sleep entirely, so the dispatcher never fires. Acting
# only when the dispatcher's flag is absent is safe here: with the device skipped there is no
# teardown left to race. No 'up' event exists on this path, and the display watcher can be too
# slow, so this hook also owns the wake - hence its own flag.
def hook() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else ""

    if phase == "pre":
        if SLEEP_FLAG.exists():  # dispatcher's pre-down already handled this suspend
            return
        _tv_off("sleep-hook", "sleep-hook", HOOK_SLEEP_FLAG, 1)

    elif phase == "post":
        _tv_on("sleep-hook", "sleep-hook", HOOK_SLEEP_FLAG)


# killing the child releases the lock; taking it directly would need an fd-passing D-Bus call,
# and the stdlib has no D-Bus library
def take_inhibitor() -> subprocess.Popen:
    return subprocess.Popen(
        [
            "systemd-inhibit", "--what=sleep", "--mode=delay", "--who=lgpowercontrol",
            "--why=Turning the TV off before suspend", "sleep", "infinity",
        ]
    )


# Fallback TV-off/on for immutable distros where hook() can't be installed (read-only /usr).
# Must never replace the hook on an ordinary distro: the hook's after-every-inhibitor ordering
# is a guarantee, the grace wait below is only a heuristic. The inhibitor is a dead end
# elsewhere (it holds back the kernel, not NetworkManager) but is safe here, since this path
# only exists where NM already skips the device and there is no teardown left to race.
def listener() -> None:
    inhibitor = take_inhibitor()
    busctl_monitor = subprocess.Popen(
        ["busctl", "--system", "monitor", "--match", SLEEP_SIGNAL_MATCH],
        stdout=subprocess.PIPE,
        text=True,
    )

    assert busctl_monitor.stdout is not None  # stdout=PIPE guarantees it; for the type checker

    # the match above means the only BOOLEAN lines are PrepareForSleep's payload
    for line in busctl_monitor.stdout:
        if "BOOLEAN true" in line:
            # We get the sleep signal at the same time as NM, not after it, so the dispatcher's
            # flag may not be set yet. Wait briefly - and keep this inside the inhibitor timeout.
            for _ in range(10):
                if SLEEP_FLAG.exists():
                    break
                time.sleep(0.1)
            else:
                _tv_off("sleep-listener", "sleep-listener", HOOK_SLEEP_FLAG, 1)
            inhibitor.terminate()
            inhibitor.wait()
        elif "BOOLEAN false" in line:
            inhibitor = take_inhibitor()  # re-arm before turning the TV on
            _tv_on("sleep-listener", "sleep-listener", HOOK_SLEEP_FLAG)

    sys.exit(1)  # busctl exiting means the system bus went away; let systemd restart us
