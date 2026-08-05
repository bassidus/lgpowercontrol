# Fallback TV-off/on for setups where the primary path (NM dispatcher pre-down/up) can't
# run: NIC Wake-on-LAN setups (sleep_hook.py) and immutable /usr (sleep_listener.py).
import os
import subprocess

from lgpowercontrol.common import HOOK_SLEEP_FLAG, LGPC, TV_OFF_FLAG, Logger, run_detached


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
