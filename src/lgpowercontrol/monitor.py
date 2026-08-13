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
    if subprocess.run([LGPC_BIN, cmd], check=False).returncode != 0:
        log(f"lgpowercontrol {cmd} failed")


def handle_signal(signum, frame) -> None:
    log("Monitor stopped")
    sys.exit(0)


# The screen-off state machine. Every field was a local in main()'s loop; they are here so the
# loop can be driven a tick at a time against a fake clock, which is the only way the 10-minute
# escalation and the clock-jump reset are testable at all - the alternative is sleeping through
# them. main() supplies the clock and the DPMS reading and owns nothing else.
class DpmsWatcher:
    def __init__(self, initial_state: str, now: float):
        self.prev = initial_state
        self.off_since: float | None = None
        self.escalated = False
        self.last_tick = now

    def tick(self, now: float, state: str) -> None:
        # clock jump = the machine was asleep, so don't count that time towards the escalation
        if self.off_since is not None and now - self.last_tick > 30:
            self.off_since = now
        self.last_tick = now

        if state and state != self.prev:
            self._transition(now, state)

        # Wall-clock, not iteration count: a blocking TV command can skew iteration timing.
        # The flag check is a safety net for a suspend that started between two ticks, not the
        # suspend gate in _transition - don't grow it into one.
        if (self.off_since is not None and not self.escalated and not SLEEP_FLAG.exists()
                and now - self.off_since >= ESCALATE_AFTER_SECONDS):
            self.escalated = True
            log(f"Screen off for {ESCALATE_AFTER_SECONDS // 60} min - escalating to full power off "
                "(fast wake via Always Ready)")
            run_lgpc("OFF")

    def _transition(self, now: float, state: str) -> None:
        transition = f"DPMS state: {self.prev or 'unknown'} -> {state}"
        # Suspend TV-off belongs to the sleep path; don't also fire our own SCREEN_OFF. This
        # gate reads logind and nothing else - also requiring a sleep flag lets the screen-off
        # slip in ahead of the sleep path on hook/listener setups, where the flag lands late.
        # logind is safe alone: it reports the sleep state before the display reacts.
        if state == "off" and preparing_for_sleep():
            log(f"{transition} - suspend in progress, TV off handled by the sleep path")
        else:
            if state == "off" and SLEEP_FLAG.exists():  # stale flag would suppress every escalation
                log("Stale sleep flag removed - no suspend in progress")
                SLEEP_FLAG.unlink(missing_ok=True)
            if state == "on":
                log(f"{transition}, turning TV on")  # dispatcher's up + this watcher both fire ON
                run_lgpc("ON")  # lgpowercontrol ON's flock dedupes
            else:
                log(f"{transition}, turning screen off")
                run_lgpc("SCREEN_OFF")
        self.prev = state
        # This tick's clock, not a fresh reading: the screen went off when the DPMS state changed,
        # not when the TV command above returned. Only ever moves the escalation earlier, deeper
        # inside the TV's own deep-standby timer, never later.
        if state == "off":
            self.off_since = now
            self.escalated = False
        else:
            self.off_since = None


def main() -> None:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    watcher = DpmsWatcher(get_dpms_state(), time.time())
    log(f"DPMS monitor started - Initial state: {watcher.prev or 'unknown'}")

    while True:
        watcher.tick(time.time(), get_dpms_state())
        time.sleep(1)
