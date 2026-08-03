import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from lgpowercontrol.common import CONF_FILE, LGPC, SLEEP_FLAG, Logger, load_conf, preparing_for_sleep

os.environ["LGPC_SOURCE"] = "dpms-monitor"  # tags lgpowercontrol's log lines
log = Logger("dpms-monitor")

# screen-off -> deep standby in ~13min; a power_off before that lands Always Ready instead
ESCALATE_AFTER = 600


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


def run_command(cmd: str) -> None:
    if subprocess.run([LGPC, cmd]).returncode != 0:
        log(f"lgpowercontrol {cmd} failed")


def handle_signal(signum, frame) -> None:
    log("Monitor stopped")
    sys.exit(0)


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    prev = get_dpms_state()
    log(f"DPMS monitor started - Initial state: {prev or 'unknown'}")

    off_at = None
    escalated = False
    last_t = time.time()

    while True:
        now = time.time()
        if off_at is not None and now - last_t > 30:  # clock jump = was asleep, don't count that time
            off_at = now
        last_t = now

        cur = get_dpms_state()

        if cur and cur != prev:
            transition = f"DPMS state: {prev or 'unknown'} -> {cur}"
            # suspend TV-off belongs to the sleep path; don't also fire our own SCREEN_OFF
            if cur == "off" and preparing_for_sleep():
                log(f"{transition} - suspend in progress, TV off handled by the sleep path")
            else:
                if cur == "off" and SLEEP_FLAG.exists():  # stale flag would suppress every screen-off
                    log("Stale sleep flag removed - no suspend in progress")
                    SLEEP_FLAG.unlink()
                if cur == "on":
                    log(f"{transition}, turning TV on")  # dispatcher's up + this watcher both fire ON
                    run_command("ON")  # lgpowercontrol ON's flock dedupes
                else:
                    log(f"{transition}, turning screen off")
                    run_command("SCREEN_OFF")
            prev = cur
            if cur == "off":
                off_at = time.time()
                escalated = False
            else:
                off_at = None

        # wall-clock, not iteration count: a blocking TV command can skew iteration timing
        if off_at is not None and not escalated and not SLEEP_FLAG.exists() and time.time() - off_at >= ESCALATE_AFTER:
            escalated = True
            log(f"Screen off for {ESCALATE_AFTER // 60} min - escalating to full power off "
                "(fast wake via Always Ready)")
            run_command("OFF")

        time.sleep(1)
