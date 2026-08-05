# Updates to the latest release, or (--dev) the dev branch HEAD. Settings/pairing survive.
# Also runs daily via the timer (check_main), notifying if a newer release/dev commit exists.
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from lgpowercontrol.common import (
    REPO,
    CONF_FILE,
    COMMIT_FILE,
    VERSION_FILE,
    Logger,
    confirm,
    conf_int,
    github_api,
    load_conf,
    notify_send,
    require_root,
)

log = Logger("update-check")


# (label, id, tarball-url) for the given channel ("main" or "dev"). label/id are the short
# sha and full sha for dev, or the version string twice for main - same value, since a
# release has no separate short/long form. Raises OSError/ValueError on a GitHub API miss.
def latest(channel: str, timeout: float = 15) -> tuple[str, str, str]:
    if channel == "dev":
        commit = github_api("commits/dev", timeout=timeout)
        sha = commit.get("sha", "")
        if not sha:
            raise ValueError("could not determine the latest dev commit")
        return sha[:7], sha, f"https://github.com/{REPO}/archive/refs/heads/dev.tar.gz"

    release = github_api("releases/latest", timeout=timeout)
    tag = release.get("tag_name", "")
    if not tag:
        raise ValueError("could not determine the latest release")
    version = tag.removeprefix("v")
    return version, version, f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"


# VERSION_FILE for "main", COMMIT_FILE for "dev" - "" if unreadable/not yet written.
def installed(channel: str) -> str:
    path = COMMIT_FILE if channel == "dev" else VERSION_FILE
    return path.read_text().strip() if os.access(path, os.R_OK) else ""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    branch = ""
    if argv:
        if argv[0] == "--dev":
            branch = "dev"
        else:
            sys.exit("Usage: lgpowercontrol-update [--dev]")

    require_root()
    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed. Run install.py instead.")

    channel = branch or "main"
    try:
        label, commit_id, url = latest(channel)
    except (OSError, ValueError) as exc:
        what = "dev commit" if branch else "release"
        sys.exit(f"Could not determine the latest {what}: {exc}")

    # VERSION lags on dev, so this is always the release version, even for a --dev install
    print(f"Installed version: {installed('main') or 'none'}")

    if branch:
        print(f"Latest on {branch}:     {label}")
        if not confirm(f"Install {branch} @ {label}? [Y/n] "):
            return 0
    else:
        print(f"Latest release:    {label}")
        if installed("main") == label:
            print("Already up to date.")
            return 0
        if not confirm(f"Update to {label}? [Y/n] "):
            return 0

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            archive = resp.read()
    except OSError as exc:
        sys.exit(f"Download failed: {exc}")

    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            if sys.version_info >= (3, 12):  # filter="data" needs 3.12+; older distro pythons lack it
                tf.extractall(tmp, filter="data")
            else:
                dest = os.path.realpath(tmp)
                for member in tf.getmembers():
                    target = os.path.realpath(os.path.join(tmp, member.name))
                    if os.path.commonpath([dest, target]) != dest:
                        sys.exit(f"Unsafe path in archive: {member.name}")
                tf.extractall(tmp)

        extracted = os.path.join(tmp, os.listdir(tmp)[0])  # tarball has exactly one top-level dir
        shutil.copy(CONF_FILE, extracted)  # keep current settings; new options fall back to defaults
        result = subprocess.run(["./install.py"], cwd=extracted)
        if result.returncode:
            sys.exit(result.returncode)

    if branch:  # lets check_main compare against dev; a release install clears it anyway
        COMMIT_FILE.write_text(commit_id)

    return 0


def check_main() -> None:
    conf = load_conf(CONF_FILE)

    # mtime = last check (throttles); content = dev-channel baseline sha
    stamp = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "lgpowercontrol-update-check"
    stamp.parent.mkdir(parents=True, exist_ok=True)  # ~/.cache may not exist on a fresh account
    days = conf_int(conf, "UPDATE_CHECK_DAYS", 7, allow_zero=True)
    due = days > 0 and (not stamp.exists() or time.time() - stamp.stat().st_mtime >= days * 86400)
    if not due:
        return

    channel = conf.get("UPDATE_CHANNEL") or "main"
    try:
        label, commit_id, _ = latest(channel, timeout=10)
    except (OSError, ValueError):  # offline or API hiccup: skip the stamp touch so the next tick retries
        return

    if channel == "dev":
        inst = installed("dev")
        if not inst:  # nothing to compare yet; record silently, notify starting next new commit
            stamp.write_text(commit_id)
            return
        stamp.touch()
        if commit_id == inst:
            return
        log(f"Update available: dev @ {label}")
        notify_send(
            "Update available",
            f"A new dev commit ({label}) is available. Install it with: sudo lgpowercontrol-update --dev",
        )
    else:
        inst = installed("main")
        stamp.touch()
        if label == inst:
            return
        log(f"Update available: {label} (installed: {inst or 'unknown'})")
        notify_send(
            "Update available",
            f"LGPowerControl {label} is available (installed: {inst or 'unknown'}). "
            "Update with: sudo lgpowercontrol-update",
        )
