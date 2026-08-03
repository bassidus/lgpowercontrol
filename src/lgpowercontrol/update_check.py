# Runs daily via the timer; notifies if a newer release/dev commit exists. Installs nothing.
import os
import time
from pathlib import Path

from lgpowercontrol.common import COMMIT_FILE, CONF_FILE, VERSION_FILE, Logger, conf_int, github_api, load_conf, notify_send

log = Logger("update-check")


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    # mtime = last check (throttles); content = dev-channel baseline sha
    stamp = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "lgpowercontrol-update-check"
    days = conf_int(conf, "UPDATE_CHECK_DAYS", 7, allow_zero=True)
    due = days > 0 and (not stamp.exists() or time.time() - stamp.stat().st_mtime >= days * 86400)
    if not due:
        return

    channel = conf.get("UPDATE_CHANNEL") or "main"

    if channel == "dev":
        try:
            latest = github_api("commits/dev", timeout=10).get("sha", "")
        except (OSError, ValueError):
            latest = ""
        if not latest:  # offline or API hiccup: skip the stamp touch so the next tick retries
            return

        if os.access(COMMIT_FILE, os.R_OK):
            installed = COMMIT_FILE.read_text().strip()
        elif stamp.exists() and stamp.stat().st_size > 0:
            installed = stamp.read_text().strip()
        else:  # nothing to compare yet; record silently, notify starting next new commit
            stamp.write_text(latest)
            return

        stamp.touch()
        if latest == installed:
            return
        log(f"Update available: dev @ {latest[:7]}")
        notify_send(
            "Update available",
            f"A new dev commit ({latest[:7]}) is available. Install it with: sudo lgpowercontrol-update --dev",
        )
    else:
        try:
            latest = github_api("releases/latest", timeout=10).get("tag_name", "").removeprefix("v")
        except (OSError, ValueError):
            latest = ""
        if not latest:
            return

        installed = ""
        if os.access(VERSION_FILE, os.R_OK):
            installed = VERSION_FILE.read_text().strip()

        stamp.touch()
        if latest == installed:
            return
        log(f"Update available: {latest} (installed: {installed or 'unknown'})")
        notify_send(
            "Update available",
            f"LGPowerControl {latest} is available (installed: {installed or 'unknown'}). "
            "Update with: sudo lgpowercontrol-update",
        )
