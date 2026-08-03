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


def screen_dimmed() -> bool:  # kscreen-doctor -o only; -j lacks the dimming value
    result = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True)
    return any(pct != "100" for pct in re.findall(r"dimming to (\d+)%", result.stdout))


class Notifier:
    def __init__(self, off_warning_seconds: int):
        self.off_warning_seconds = off_warning_seconds
        self.profile = "AC"
        self.dim_timeout = 300
        self.off_timeout = 600
        self.notify_delay = 0
        self.remaining = 0
        self.notif_id = 0
        self.off_enabled = True
        self.timer: threading.Timer | None = None

    # Re-read every dim (not just at startup): profile/setting changes must apply without a restart.
    def compute_timings(self) -> None:
        result = subprocess.run(
            [
                "busctl", "--user", "call", "org.kde.Solid.PowerManagement",
                "/org/kde/Solid/PowerManagement", "org.kde.Solid.PowerManagement", "currentProfile",
            ],
            capture_output=True, text=True,
        )
        m = re.search(r'"([^"]*)"', result.stdout)
        self.profile = m.group(1) if m else ""
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
        if screen_dimmed():  # re-check: process may have been suspended mid-wait, dim may have ended
            self.notif_id = notify_send(
                "TV turning off",
                f"The TV turns off in {self.remaining} seconds. Move the mouse or press a key to keep it on.",
                timeout_ms=self.remaining * 1000,
            )
            log("Warning notification sent")

    def cancel_timer(self) -> None:
        notify_close(self.notif_id)
        self.notif_id = 0
        if self.timer is not None and self.timer.is_alive():
            self.timer.cancel()
            log("Screen dim ended, pending warning canceled")
        self.timer = None


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    raw = conf.get("OFF_WARNING_SECONDS", "")
    if raw and not raw.isdigit():  # bad conf value must degrade to default, never crash into a restart loop
        log(f"Invalid OFF_WARNING_SECONDS='{raw}' - using 120")
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

    state = "inactive"
    while True:
        new_state = "active" if screen_dimmed() else "inactive"
        if new_state != state:
            state = new_state
            if state == "active":
                if notifier.timer is None or not notifier.timer.is_alive():
                    notifier.compute_timings()
                    if notifier.off_enabled:
                        log(f"Screen dimmed; warning notification in {notifier.notify_delay}s "
                            f"(profile={notifier.profile})")
                        notifier.timer = threading.Timer(notifier.notify_delay, notifier.fire_timer)
                        notifier.timer.daemon = True
                        notifier.timer.start()
            else:
                notifier.cancel_timer()
        time.sleep(conf_int(conf, "NOTIFY_POLL_SECONDS", 2))


if __name__ == "__main__":
    main()
