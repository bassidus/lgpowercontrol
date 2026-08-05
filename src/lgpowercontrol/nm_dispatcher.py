# NM dispatcher script: called as <name> <interface> <action>. Symlinked into pre-down.d/
# too, to receive both pre-down (blocking, network still up) and up (post-resume) events.
import os
import subprocess
import sys

from lgpowercontrol.common import (
    LGPC,
    SLEEP_FLAG,
    TV_OFF_FLAG,
    Logger,
    preparing_for_sleep,
    run_detached,
)

log = Logger("nm-dispatcher")


def main() -> None:
    os.environ["LGPC_SOURCE"] = "nm-dispatcher"  # tags lgpowercontrol's log lines

    action = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "pre-down":
        if not preparing_for_sleep():
            return

        if SLEEP_FLAG.exists():  # fires once per NIC; only act on the first (cleared by 'up')
            return  # NM runs dispatcher scripts serially, so this check can't race
        SLEEP_FLAG.touch()

        if TV_OFF_FLAG.exists():  # monitor's 10-min escalation may have already turned it off
            log("System going to sleep - TV already off, skipping")
            return
        log("System going to sleep, turning TV off")
        subprocess.run([LGPC, "OFF"])  # full power off: lands the TV in Always Ready where available

    elif action == "up":
        if not SLEEP_FLAG.exists():
            return
        SLEEP_FLAG.unlink(missing_ok=True)

        log("System woke up, turning TV on")
        run_detached(str(LGPC), "ON", env={"LGPC_SOURCE": "resume"})  # detached: dispatcher runs sequentially
