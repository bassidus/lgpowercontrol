#!/usr/bin/env python3
# At most once per UPDATE_CHECK_DAYS, compare the installed version with the
# latest GitHub release (or dev commit, see UPDATE_CHANNEL) and show a desktop
# notification when an update is available. Nothing is installed automatically.
# Triggered daily by lgtvpc-update-check.timer, independent of the notify
# service, so long-running sessions (suspend/resume, no reboot) still get
# checked on schedule.
import json
import os
import time
import urllib.request

from lgtvpc_common import INSTALL_DIR, REPO, CONF_FILE, Logger, conf_int, load_conf, notify_send

log = Logger("update-check")


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read().decode()


def touch(path: str) -> None:
    with open(path, "a"):
        pass
    os.utime(path, None)


def stamp_path() -> str:
    # mtime = time of the last successful check. The notification repeats every
    # UPDATE_CHECK_DAYS until the update is installed, as a reminder. Content is
    # only used on the dev channel as a baseline sha when COMMIT is missing.
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.environ["HOME"], ".cache")
    return os.path.join(cache_home, "lgtvpc-update-check")


def update_check_due(conf: dict[str, str], stamp: str) -> bool:
    days = conf_int(conf, "UPDATE_CHECK_DAYS", 7, allow_zero=True)
    if days <= 0:
        return False
    if not os.path.exists(stamp):
        return True
    return time.time() - os.path.getmtime(stamp) >= days * 86400


def check_for_update(conf: dict[str, str], stamp: str) -> None:
    channel = conf.get("UPDATE_CHANNEL") or "main"

    if channel == "dev":
        try:
            commit = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/dev"))
            latest = commit.get("sha", "")
        except (OSError, ValueError):
            latest = ""
        # Offline or API hiccup: skip the stamp touch so the next tick retries.
        if not latest:
            return

        # COMMIT is written by update.py --dev; absent on git-clone installs,
        # where the stamp content serves as a stand-in baseline instead.
        commit_file = INSTALL_DIR / "COMMIT"
        if os.access(commit_file, os.R_OK):
            installed = commit_file.read_text().strip()
        elif os.path.exists(stamp) and os.path.getsize(stamp) > 0:
            with open(stamp) as f:
                installed = f.read().strip()
        else:
            # First check with nothing to compare against: record the current
            # dev commit silently and notify from the next new commit on.
            with open(stamp, "w") as f:
                f.write(latest)
            return

        touch(stamp)
        if latest == installed:
            return
        log(f"Update available: dev @ {latest[:7]}")
        notify_send(
            "Update available",
            f"A new dev commit ({latest[:7]}) is available. Install it with: sudo /opt/lgtvpc/update.py --dev",
        )
    else:
        try:
            release = json.loads(fetch(f"https://api.github.com/repos/{REPO}/releases/latest"))
            latest = release.get("tag_name", "").removeprefix("v")
        except (OSError, ValueError):
            latest = ""
        if not latest:
            return

        installed = ""
        version_file = INSTALL_DIR / "VERSION"
        if os.access(version_file, os.R_OK):
            installed = version_file.read_text().strip()

        touch(stamp)
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
