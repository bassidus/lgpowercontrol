import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from lgpowercontrol.common import CONF_FILE, Logger, busctl, conf_int, load_conf, notify_close, notify_send

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


def read_powerdevil_int(group: str, key: str, default: int) -> int:
    value = read_powerdevil(group, key, default)
    return int(value) if value.isdigit() else default


def screen_dimmed() -> bool:  # kscreen-doctor -o only; -j lacks the dimming value
    result = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True)
    return any(pct != "100" for pct in re.findall(r"dimming to (\d+)%", result.stdout))


# Plasma's own per-profile defaults: (dim_timeout, off_timeout) in seconds.
PROFILE_DEFAULTS = {"AC": (300, 600), "Battery": (120, 300), "LowBattery": (60, 120)}


class Notifier:
    def __init__(self, off_warning_seconds: int):
        self.off_warning_seconds = off_warning_seconds
        self.profile = "AC"
        self.dim_timeout, self.off_timeout = PROFILE_DEFAULTS["AC"]
        self.notify_delay = 0
        self.remaining = 0
        self.notification_id = 0
        self.off_enabled = True
        self.timer: threading.Timer | None = None

    # Re-read every dim (not just at startup): profile/setting changes must apply without a restart.
    def compute_timings(self) -> None:
        out = busctl(
            "--user", "call", "org.kde.Solid.PowerManagement",
            "/org/kde/Solid/PowerManagement", "org.kde.Solid.PowerManagement", "currentProfile",
        )
        match = re.search(r'"([^"]*)"', out)
        self.profile = match.group(1) if match else ""
        if self.profile not in PROFILE_DEFAULTS:  # also the --group name below, so normalize it
            self.profile = "AC"
        default_dim, default_off = PROFILE_DEFAULTS[self.profile]

        self.dim_timeout = read_powerdevil_int(self.profile, "DimDisplayIdleTimeoutSec", default_dim)
        self.off_timeout = read_powerdevil_int(self.profile, "TurnOffDisplayIdleTimeoutSec", default_off)

        self.notify_delay = max(0, self.off_timeout - self.dim_timeout - self.off_warning_seconds)
        self.remaining = self.off_timeout - self.dim_timeout - self.notify_delay

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

    def show_warning(self) -> None:
        if screen_dimmed():  # re-check: process may have been suspended mid-wait, dim may have ended
            self.notification_id = notify_send(
                "TV turning off",
                f"The TV turns off in {self.remaining} seconds. Move the mouse or press a key to keep it on.",
                timeout_ms=self.remaining * 1000,
            )
            log("Warning notification sent")

    def cancel_timer(self) -> None:
        notify_close(self.notification_id)
        self.notification_id = 0
        if self.timer is not None and self.timer.is_alive():
            self.timer.cancel()
            log("Screen dim ended, pending warning canceled")
        self.timer = None


def main() -> None:
    conf = load_conf(CONF_FILE)

    # bad conf value must degrade to default, never crash into a restart loop
    raw_warning_seconds = conf.get("OFF_WARNING_SECONDS", "")
    if raw_warning_seconds and not raw_warning_seconds.isdigit():
        log(f"Invalid OFF_WARNING_SECONDS='{raw_warning_seconds}' - using 120")
    off_warning_seconds = conf_int(conf, "OFF_WARNING_SECONDS", 120, allow_zero=True)
    if off_warning_seconds <= 0:
        return

    if not (shutil.which("kscreen-doctor") and shutil.which("kreadconfig6")):  # non-KDE desktop
        return

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

    # dim is the idle anchor; keep running so enabling it later just works
    if read_powerdevil(notifier.profile, "DimDisplayWhenIdle", "true") != "true":
        log(
            "Warning: 'Dim automatically' is disabled in System Settings -> Power "
            "Management; no TV-off warning can be shown until it is enabled"
        )

    poll_seconds = conf_int(conf, "NOTIFY_POLL_SECONDS", 2)
    state = "inactive"
    while True:
        new_state = "active" if screen_dimmed() else "inactive"
        if new_state != state:
            state = new_state
            if state == "active":
                if notifier.timer is None or not notifier.timer.is_alive():
                    notifier.compute_timings()
                    if notifier.off_enabled and notifier.remaining > 0:
                        log(f"Screen dimmed; warning notification in {notifier.notify_delay}s "
                            f"(profile={notifier.profile})")
                        notifier.timer = threading.Timer(notifier.notify_delay, notifier.show_warning)
                        notifier.timer.daemon = True
                        notifier.timer.start()
                    elif notifier.off_enabled:  # off timeout <= dim timeout: no window to warn in
                        log(f"Screen dimmed; no warning - off timeout ({notifier.off_timeout}s) is not "
                            f"later than dim timeout ({notifier.dim_timeout}s)")
            else:
                notifier.cancel_timer()
        time.sleep(poll_seconds)
