#!/usr/bin/env python3
"""Fallback TV-off/on at suspend/resume for immutable distros (read-only
/usr), where the systemd-sleep hook (scripts/sleep.py) cannot be installed.

Same job and flag semantics as sleep.py, but packaged as a long-running
service: it listens for logind's PrepareForSleep signal and holds a sleep
delay inhibitor so there is time to turn the TV off before the kernel
suspends. A delay inhibitor is a proven dead end against NM's network
teardown (it delays the kernel, not NM) - but in the NIC-WoL case this
mainly exists for, NM skips the device entirely and the network stays up
through suspend, so there is nothing to race.

Unlike the sleep hook, which systemd runs only after every delay lock is
released (so the NM dispatcher queue is provably finished), this service
gets PrepareForSleep at the same instant as NetworkManager - the
dispatcher's sleep flag may not be set yet when the signal arrives. The
grace wait in on_sleep() covers that: pre-down touches the flag within
tens of ms of being spawned, long before the wait expires. The inhibitor
makes waiting safe: systemd holds suspend until the delay locks are
released, or InhibitDelayMaxSec (5 s by default) - which also budgets the
grace wait plus the OFF at --retries 1.
"""
import os
import subprocess
import sys
import time

from lgtvpc_common import (
    CONF_FILE,
    HOOK_SLEEP_FLAG,
    LGTVPC,
    SLEEP_FLAG,
    TV_OFF_FLAG,
    Logger,
    load_conf,
    run_detached,
)

log = Logger("sleep-listener")

MATCH = (
    "type='signal',sender='org.freedesktop.login1',"
    "path='/org/freedesktop/login1',"
    "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"
)


def take_inhibitor() -> subprocess.Popen:
    """Holds a logind sleep-delay lock via a child process - killing the
    child releases the lock. Avoids the fd-passing D-Bus call that taking
    the lock directly would need (no D-Bus library in the stdlib)."""
    return subprocess.Popen(
        [
            "systemd-inhibit", "--what=sleep", "--mode=delay", "--who=lgtvpc",
            "--why=Turning the TV off before suspend", "sleep", "infinity",
        ]
    )


def on_sleep() -> None:
    # Grace wait for the NM dispatcher: if pre-down fires this suspend, its
    # flag appears well within a second and this service must stay out of
    # the way.
    for _ in range(10):
        if SLEEP_FLAG.exists():
            return
        time.sleep(0.1)

    # Own flag (not the dispatcher's): nothing would clear the dispatcher's
    # flag on these setups (no 'up' fires), and a stale sleep flag makes the
    # monitor misbehave. Set before the tv-off check so the resume side turns
    # the TV on even when the off was skipped here.
    HOOK_SLEEP_FLAG.touch()

    # The monitor's 10-min escalation may already have powered the TV off; a
    # second power_off would hang against a standby TV until the connect
    # timeout and delay suspend.
    if TV_OFF_FLAG.exists():
        log("System going to sleep (dispatcher pre-down did not fire) - TV already off, skipping")
        return

    log("System going to sleep (dispatcher pre-down did not fire), turning TV off")

    # --retries 1: on setups where the network IS torn down at sleep (e.g.
    # bridges), one fast failed attempt beats a full retry cycle eating the
    # inhibitor budget.
    env = dict(os.environ, LGPC_SOURCE="sleep-listener")
    subprocess.run([LGTVPC, "--retries", "1", "OFF"], env=env)


def on_resume() -> None:
    if not HOOK_SLEEP_FLAG.exists():
        return
    HOOK_SLEEP_FLAG.unlink()

    log("System woke up, turning TV on")

    # Detached like the dispatcher's ON: it can retry for a while and must
    # not block signal handling here.
    run_detached(str(LGTVPC), "ON", env={"LGPC_SOURCE": "sleep-listener"})


def main() -> None:
    if os.access(CONF_FILE, os.R_OK):
        log.configure(load_conf(CONF_FILE))

    inhibitor = take_inhibitor()
    monitor = subprocess.Popen(
        ["busctl", "--system", "monitor", "--match", MATCH],
        stdout=subprocess.PIPE,
        text=True,
    )

    # With the match above the only BOOLEAN lines in the stream are
    # PrepareForSleep's payload (verified: busctl monitor flushes per
    # message even when piped).
    for line in monitor.stdout:
        if "BOOLEAN true" in line:
            on_sleep()
            # Release the lock so suspend proceeds.
            inhibitor.terminate()
            inhibitor.wait()
        elif "BOOLEAN false" in line:
            # Re-arm for the next suspend before turning the TV on.
            inhibitor = take_inhibitor()
            on_resume()

    # busctl exiting means the system bus went away; let systemd restart us.
    sys.exit(1)


if __name__ == "__main__":
    main()
