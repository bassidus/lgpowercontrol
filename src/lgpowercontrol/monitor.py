import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from lgpowercontrol.common import LGPC_BIN, SLEEP_FLAG, Logger, preparing_for_sleep

os.environ["LGPC_SOURCE"] = "dpms-monitor"  # tags lgpowercontrol's log lines
log = Logger("dpms-monitor")

# The TV drops into deep standby ~13 min after screen-off, on an internal timer that ignores
# incoming connections - keep-alive polling cannot hold it off, that was tried. Getting in first
# with a power_off lands Always Ready instead, which wakes far faster. Always Ready only engages
# on power_off, never on screen-off alone, which is the whole reason this escalation exists.
ESCALATE_AFTER_SECONDS = 600


def get_dpms_state() -> str:  # "on"/"off", or "" if no output connected (e.g. mid-hotplug)
    connected = False
    for card in Path("/sys/class/drm").glob("card*-*"):
        try:
            if (card / "status").read_text().strip() != "connected":
                continue
            dpms = (card / "dpms").read_text().strip()
        except OSError:
            continue
        connected = True
        if dpms == "On":
            return "on"
    return "off" if connected else ""


def run_lgpc(cmd: str) -> None:
    if subprocess.run([LGPC_BIN, cmd]).returncode != 0:
        log(f"lgpowercontrol {cmd} failed")


def handle_signal(signum, frame) -> None:
    log("Monitor stopped")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    prev = get_dpms_state()
    log(f"DPMS monitor started - Initial state: {prev or 'unknown'}")

    off_since = None
    escalated = False
    last_tick = time.time()

    while True:
        now = time.time()
        if off_since is not None and now - last_tick > 30:  # clock jump = was asleep, don't count that time
            off_since = now
        last_tick = now

        cur = get_dpms_state()

        if cur and cur != prev:
            transition = f"DPMS state: {prev or 'unknown'} -> {cur}"
            # Suspend TV-off belongs to the sleep path; don't also fire our own SCREEN_OFF.
            # This gate reads logind and nothing else. Also requiring a sleep flag was tried
            # and reverted: on hook/listener setups the flag lands too late, letting this
            # screen-off slip in just before the sleep path's turn-off. logind is safe alone
            # because it reports the sleep state before the display reacts to the same signal.
            if cur == "off" and preparing_for_sleep():
                log(f"{transition} - suspend in progress, TV off handled by the sleep path")
            else:
                if cur == "off" and SLEEP_FLAG.exists():  # stale flag would suppress every escalation
                    log("Stale sleep flag removed - no suspend in progress")
                    SLEEP_FLAG.unlink(missing_ok=True)
                if cur == "on":
                    log(f"{transition}, turning TV on")  # dispatcher's up + this watcher both fire ON
                    run_lgpc("ON")  # lgpowercontrol ON's flock dedupes
                else:
                    log(f"{transition}, turning screen off")
                    run_lgpc("SCREEN_OFF")
            prev = cur
            if cur == "off":
                off_since = time.time()
                escalated = False
            else:
                off_since = None

        # Wall-clock, not iteration count: a blocking TV command can skew iteration timing.
        # The flag check is a safety net for a suspend that started between two ticks, not the
        # suspend gate above - don't grow it into one.
        if (off_since is not None and not escalated and not SLEEP_FLAG.exists()
                and time.time() - off_since >= ESCALATE_AFTER_SECONDS):
            escalated = True
            log(f"Screen off for {ESCALATE_AFTER_SECONDS // 60} min - escalating to full power off "
                "(fast wake via Always Ready)")
            run_lgpc("OFF")

        time.sleep(1)
