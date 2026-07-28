#!/usr/bin/env python3
import glob
import os
import signal
import subprocess
import sys
import time

from lgtvpc_common import CONF_FILE, LGTVPC, SLEEP_FLAG, Logger, load_conf, preparing_for_sleep

# Tags the main script's log lines with who triggered the command.
os.environ["LGPC_SOURCE"] = "dpms-monitor"
log = Logger("dpms-monitor")

# One-shot per screen-off period. The TV drops into deep standby ~13 min
# after a mere screen-off (~10 s wake). A full power_off before that
# threshold lands it in Always Ready instead (~3-4 s wake) on TVs that have
# it enabled; on others it's a no-op wake-wise.
ESCALATE_AFTER = 600


def get_dpms_state() -> str:
    """Returns "on" if any connected DRM output is active, "off" if all
    connected outputs are inactive, or "" if no output is connected (e.g.
    mid-hotplug)."""
    connected = False
    for card_dir in glob.glob("/sys/class/drm/card*-*"):
        status_path = f"{card_dir}/status"
        dpms_path = f"{card_dir}/dpms"
        if not (os.access(status_path, os.R_OK) and os.access(dpms_path, os.R_OK)):
            continue
        try:
            with open(status_path) as f:
                if f.read().strip() != "connected":
                    continue
            connected = True
            with open(dpms_path) as f:
                if f.read().strip() == "On":
                    return "on"
        except OSError:
            continue
    return "off" if connected else ""


def run_command(cmd: str) -> None:
    if subprocess.run([LGTVPC, cmd]).returncode != 0:
        log(f"lgtvpc {cmd} failed")


def handle_signal(signum, frame) -> None:
    log("Monitor stopped")
    sys.exit(0)


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    previous_state = get_dpms_state()
    log(f"DPMS monitor started - Initial state: {previous_state or 'unknown'}")

    off_since = None
    escalated = False
    last_tick = time.time()

    while True:
        now = time.time()
        # A jump in the clock means the system was suspended (this loop never
        # legitimately stalls that long). Restart the screen-off clock so time
        # the machine spent asleep doesn't count toward the escalation - else
        # a resume with the display still off would power the TV straight off.
        if off_since is not None and now - last_tick > 30:
            off_since = now
        last_tick = now

        current_state = get_dpms_state()

        if current_state and current_state != previous_state:
            transition = f"DPMS state: {previous_state or 'unknown'} -> {current_state}"
            # NM kills the network within milliseconds of PrepareForSleep, so the
            # dispatcher's blocking pre-down window (90-lgtvpc) is the only
            # reliable spot to turn the TV off - already done by the time this
            # transition is observed here. Its flag file marks that window.
            if current_state == "off" and SLEEP_FLAG.exists() and preparing_for_sleep():
                log(f"{transition} - suspend in progress, TV already off via dispatcher")
            else:
                # A leftover flag (dispatcher 'up' never fired, e.g. the network
                # never came back after resume) would suppress every screen-off.
                if current_state == "off" and SLEEP_FLAG.exists():
                    log("Stale sleep flag removed - no suspend in progress")
                    SLEEP_FLAG.unlink()
                if current_state == "on":
                    # At resume both the dispatcher's up event and this watcher
                    # fire ON; a flock in turn_tv_on deduplicates.
                    log(f"{transition}, turning TV on")
                    run_command("ON")
                else:
                    log(f"{transition}, turning screen off")
                    run_command("SCREEN_OFF")
            previous_state = current_state
            if current_state == "off":
                off_since = time.time()
                escalated = False
            else:
                off_since = None

        # Wall-clock based (not loop-iteration counting): iterations are not
        # 1 s apart when a TV command blocks the loop, and a >= comparison
        # cannot miss the threshold the way an == on a counter could.
        if (
            off_since is not None
            and not escalated
            and not SLEEP_FLAG.exists()
            and time.time() - off_since >= ESCALATE_AFTER
        ):
            escalated = True
            log("Screen off for 10 min - escalating to full power off (fast wake via Always Ready)")
            run_command("OFF")

        time.sleep(1)


if __name__ == "__main__":
    main()
