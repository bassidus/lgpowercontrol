#!/usr/bin/env python3
# At most once per UPDATE_CHECK_DAYS, compare the installed version with the
# latest GitHub release (or dev commit, see UPDATE_CHANNEL) and show a desktop
# notification when an update is available. Nothing is installed automatically.
# Triggered daily by lgtvpc-update-check.timer, independent of the notify
# service, so long-running sessions (suspend/resume, no reboot) still get
# checked on schedule.
import os
import time
from pathlib import Path

from lgtvpc_common import COMMIT_FILE, CONF_FILE, VERSION_FILE, Logger, conf_int, github_api, load_conf, notify_send

log = Logger("update-check")


# mtime = time of the last successful check. The notification repeats every
# UPDATE_CHECK_DAYS until the update is installed, as a reminder. Content is
# only used on the dev channel as a baseline sha when COMMIT is missing.
def stamp_path() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(cache_home) / "lgtvpc-update-check"


def update_check_due(conf: dict[str, str], stamp: Path) -> bool:
    days = conf_int(conf, "UPDATE_CHECK_DAYS", 7, allow_zero=True)
    if days <= 0:
        return False
    if not stamp.exists():
        return True
    return time.time() - stamp.stat().st_mtime >= days * 86400


def check_for_update(conf: dict[str, str], stamp: Path) -> None:
    channel = conf.get("UPDATE_CHANNEL") or "main"

    if channel == "dev":
        try:
            latest = github_api("commits/dev", timeout=10).get("sha", "")
        except (OSError, ValueError):
            latest = ""
        # Offline or API hiccup: skip the stamp touch so the next tick retries.
        if not latest:
            return

        # COMMIT is written by update.py --dev; absent on git-clone installs,
        # where the stamp content serves as a stand-in baseline instead.
        if os.access(COMMIT_FILE, os.R_OK):
            installed = COMMIT_FILE.read_text().strip()
        elif stamp.exists() and stamp.stat().st_size > 0:
            installed = stamp.read_text().strip()
        else:
            # First check with nothing to compare against: record the current
            # dev commit silently and notify from the next new commit on.
            stamp.write_text(latest)
            return

        stamp.touch()
        if latest == installed:
            return
        log(f"Update available: dev @ {latest[:7]}")
        notify_send(
            "Update available",
            f"A new dev commit ({latest[:7]}) is available. Install it with: sudo /opt/lgtvpc/update.py --dev",
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
            f"Update with: sudo /opt/lgtvpc/update.py",
        )


def main() -> None:
    conf = load_conf(CONF_FILE)
    log.configure(conf)

    stamp = stamp_path()
    if update_check_due(conf, stamp):
        check_for_update(conf, stamp)


if __name__ == "__main__":
    main()
