#!/usr/bin/env python3
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from lgtvpc_common import CONF_FILE, Logger, conf_int, load_conf, notify_close, notify_send

log = Logger("notify-service")


def read_powerdevil(group: str, key: str, default) -> str:
    result = subprocess.run(
        [
            "kreadconfig6", "--file", "powerdevilrc", "--group", group, "--group", "Display",
            "--key", key, "--default", str(default),
        ],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def get_current_profile() -> str:
    result = subprocess.run(
        [
            "busctl", "--user", "call", "org.kde.Solid.PowerManagement",
            "/org/kde/Solid/PowerManagement", "org.kde.Solid.PowerManagement", "currentProfile",
        ],
        capture_output=True, text=True,
    )
    m = re.search(r'"([^"]*)"', result.stdout)
    return m.group(1) if m else ""


# Plasma's idle dim lowers each output's "dimming" value (normally 100%,
# 30% while dimmed). This is KWin-internal state with no D-Bus signal, so it
# is polled via kscreen-doctor, which ships with Plasma. Note: the value
# only appears in the text output (-o), not in the JSON (-j).
def screen_dimmed() -> bool:
    result = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True)
    return any(pct != "100" for pct in re.findall(r"dimming to (\d+)%", result.stdout))


# Owns the timing state and the pending-warning timer/notification.
#
# A single instance lives for the process lifetime; grouping this here
# (rather than as module globals) keeps the timer thread's state and the
# main loop's state in one place.
class Notifier:
    def __init__(self, off_warning_seconds: int):
        self.off_warning_seconds = off_warning_seconds
        self.profile = "AC"
        self.dim_timeout = 300
        self.off_timeout = 600
        self.notify_delay = 0
        self.remaining = 0
        self.notification_id = 0
        self.off_enabled = True
        self.timer: threading.Timer | None = None

    # Reads Plasma's idle timeouts (seconds) for the currently active power
    # profile. Called again on every dim (see arm_timer), so settings
    # changes and AC/battery switches apply without restarting the service.
    # The dim event is our only idle anchor: the warning fires notify_delay
    # seconds after the screen dims. Non-numeric kreadconfig6 output falls
    # back to the defaults.
    def compute_timings(self) -> None:
        self.profile = get_current_profile()
        def_dim, def_off = 300, 600
        if self.profile == "Battery":
            def_dim, def_off = 120, 300
        elif self.profile == "LowBattery":
            def_dim, def_off = 60, 120
        else:
            self.profile = "AC"

        value = read_powerdevil(self.profile, "DimDisplayIdleTimeoutSec", def_dim)
        self.dim_timeout = int(value) if value.isdigit() else def_dim
        value = read_powerdevil(self.profile, "TurnOffDisplayIdleTimeoutSec", def_off)
        self.off_timeout = int(value) if value.isdigit() else def_off

        self.notify_delay = max(0, self.off_timeout - self.dim_timeout - self.off_warning_seconds)
        self.remaining = self.off_timeout - self.dim_timeout - self.notify_delay

        # Re-read per dim (not just at startup): a profile switch (AC/battery)
        # or a settings change while the service runs must not leave a stale
        # warning armed for a TV-off that is no longer coming.
        off_enabled = read_powerdevil(self.profile, "TurnOffDisplayWhenIdle", "true") == "true"
        if off_enabled != self.off_enabled:
            if not off_enabled:
                log(
                    "'Turn off screen' is disabled in System Settings -> Power Management "
                    f"(profile={self.profile}); no TV-off warning needed"
                )
            else:
                log(f"'Turn off screen' is enabled again (profile={self.profile}); resuming warnings")
        self.off_enabled = off_enabled

    def fire_timer(self) -> None:
        # Re-check the dim before firing: this thread's wait is real
        # wall-clock time, but the process can be suspended mid-wait - a
        # timer armed before a suspend would otherwise fire late, warning
        # about a screen-off that is no longer coming.
        if screen_dimmed():
            self.notification_id = notify_send(
                "TV turning off",
                f"The TV turns off in {self.remaining} seconds. Move the mouse or press a key to keep it on.",
                timeout_ms=self.remaining * 1000,
            )
            log("Warning notification sent")

    def arm_timer(self) -> None:
        if self.timer is not None and self.timer.is_alive():
            return
        self.compute_timings()
        if not self.off_enabled:
            return
        log(f"Screen dimmed; warning notification in {self.notify_delay}s (profile={self.profile})")
        self.timer = threading.Timer(self.notify_delay, self.fire_timer)
        self.timer.daemon = True
        self.timer.start()

    # Dismiss a still-visible warning as soon as activity ends the dim.
    def cancel_timer(self) -> None:
        notify_close(self.notification_id)
        self.notification_id = 0
        if self.timer is not None and self.timer.is_alive():
            self.timer.cancel()
            log("Screen dim ended, pending warning canceled")
        self.timer = None


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    # Everything from the conf is untrusted text: a bad value must degrade to
    # a default, never crash the service into a systemd restart loop.
    raw = conf.get("OFF_WARNING_SECONDS", "")
    if raw and not raw.isdigit():
        log(f"Invalid OFF_WARNING_SECONDS='{raw}' - using 120")
    off_warning_seconds = conf_int(conf, "OFF_WARNING_SECONDS", 120, allow_zero=True)
    if off_warning_seconds <= 0:
        return

    # KDE Plasma only - exit quietly on other desktop environments.
    if not (shutil.which("kscreen-doctor") and shutil.which("kreadconfig6")):
        return

    poll_interval = conf_int(conf, "NOTIFY_POLL_SECONDS", 2)

    notifier = Notifier(off_warning_seconds)

    def handle_signal(signum, frame) -> None:
        notifier.cancel_timer()
        log("Notify service stopped")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    notifier.compute_timings()

    log(
        f"Notify service started (dim={notifier.dim_timeout}s, off={notifier.off_timeout}s, "
        f"warning={notifier.remaining}s before off, profile={notifier.profile})"
    )

    # The dim is our idle anchor, so warn if it is disabled. Keep running: if
    # the user enables it later, warnings start working without a service restart.
    if read_powerdevil(notifier.profile, "DimDisplayWhenIdle", "true") != "true":
        log(
            "Warning: 'Dim automatically' is disabled in System Settings -> Power "
            "Management; no TV-off warning can be shown until it is enabled"
        )

    state = "inactive"
    while True:
        new_state = "active" if screen_dimmed() else "inactive"
        if new_state != state:
            state = new_state
            if state == "active":
                notifier.arm_timer()
            else:
                notifier.cancel_timer()
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
