#!/usr/bin/env python3
# systemd-sleep hook: fallback TV-off/on at suspend/resume for setups where
# the NM dispatcher never fires. Known case (issue #12): when the PC's own
# NIC has Wake-on-LAN enabled, NM skips the device entirely at sleep
# ("device has wake-on-lan, skipping") - no deactivation, no pre-down, and
# the network stays up through suspend. That also means this hook is not
# racing NM's teardown, unlike naive sleep hooks on dispatcher setups.
#
# The resume side is needed too: with the device never taken down there is
# no 'up' event at resume either, and the monitor cannot be relied on - it
# may freeze before observing the DPMS-off, in which case resume shows no
# off->on transition and it stays silent (seen on p600s, 2026-07-17).
#
# On dispatcher setups the pre-down script has already run by the time
# systemd-sleep executes hooks (NM holds a logind inhibitor until its
# dispatcher queue finishes), so the sleep flag is set and this hook is a
# no-op; its own flag below then keeps the post phase a no-op too.
#
# systemd calls this as: lgtvpc pre|post suspend|...
import os
import subprocess
import sys

# Unlike the scripts installed into /opt/lgtvpc/, this one lives at
# /usr/lib/systemd/system-sleep/, so its own directory isn't on sys.path.
sys.path.insert(0, "/opt/lgtvpc")
from lgtvpc_common import (  # noqa: E402
    CONF_FILE,
    HOOK_SLEEP_FLAG,
    LGTVPC,
    SLEEP_FLAG,
    TV_OFF_FLAG,
    Logger,
    load_conf,
    run_detached,
)

log = Logger("sleep-hook")


def main() -> None:
    # Conf gone means the project was removed but the hook left behind somehow.
    if not os.access(CONF_FILE, os.R_OK):
        return
    log.configure(load_conf(CONF_FILE))

    phase = sys.argv[1] if len(sys.argv) > 1 else ""

    if phase == "pre":
        # Dispatcher already handled this suspend.
        if SLEEP_FLAG.exists():
            return

        # Own flag (not the dispatcher's): nothing would clear the dispatcher's
        # flag on these setups (no 'up' fires), and a stale sleep flag makes the
        # monitor misbehave. Set before the tv-off check so the post phase turns
        # the TV on even when the off was skipped here.
        HOOK_SLEEP_FLAG.touch()

        # The monitor's 10-min escalation may already have powered the TV off; a
        # second power_off would hang against a standby TV until the connect
        # timeout and delay suspend.
        if TV_OFF_FLAG.exists():
            log("System going to sleep (dispatcher pre-down did not fire) - TV already off, skipping")
            return

        log("System going to sleep (dispatcher pre-down did not fire), turning TV off")

        # --retries 1: on setups where the network IS torn down before this
        # hook runs (e.g. bridges), one fast failed attempt beats a full
        # retry cycle holding up suspend.
        env = dict(os.environ, LGPC_SOURCE="sleep-hook")
        subprocess.run([LGTVPC, "--retries", "1", "OFF"], env=env)

    elif phase == "post":
        if not HOOK_SLEEP_FLAG.exists():
            return
        HOOK_SLEEP_FLAG.unlink()

        log("System woke up, turning TV on")

        # Detached like the dispatcher's ON: it can retry for a while and must
        # not hold up resume.
        run_detached(str(LGTVPC), "ON", env={"LGPC_SOURCE": "sleep-hook"})


if __name__ == "__main__":
    main()
