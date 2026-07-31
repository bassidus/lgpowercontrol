#!/usr/bin/env python3
# Compares the installed version with the latest GitHub release and offers
# to download and install it. Settings and TV pairing survive the update.
# --dev installs the latest state of the dev branch instead.
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from lgtvpc_common import REPO, CONF_FILE, COMMIT_FILE, VERSION_FILE, github_api, require_root


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read()


def confirm(prompt: str) -> bool:
    return input(prompt).strip().lower().startswith("y")


def extract(archive: bytes, dest: str) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
        # filter="data" (safe extraction, strips absolute paths/permissions)
        # needs Python 3.12+; on 3.10/3.11 (still-supported distro pythons)
        # extractall() has no filter argument at all.
        if sys.version_info >= (3, 12):
            tf.extractall(dest, filter="data")
        else:
            tf.extractall(dest)


def main() -> None:
    branch = ""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--dev":
            branch = "dev"
        else:
            sys.exit("Usage: update.py [--dev]")

    require_root()
    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed. Run install.py instead.")

    installed = "none"
    if os.access(VERSION_FILE, os.R_OK):
        installed = VERSION_FILE.read_text().strip()

    sha = ""
    if branch:
        # The VERSION file on dev often lags behind the code, so skip the
        # up-to-date check and show the latest commit instead.
        try:
            commit = github_api(f"commits/{branch}")
        except (OSError, ValueError) as exc:
            sys.exit(f"Could not determine the latest {branch} commit: {exc}")
        sha = commit.get("sha", "")
        if not sha:
            sys.exit(f"Could not determine the latest {branch} commit. Aborting.")
        message = commit.get("commit", {}).get("message", "")
        subject = message.splitlines()[0] if message else ""

        print(f"Installed version: {installed}")
        print(f"Latest on {branch}:     {sha[:7]} \"{subject}\"")

        if not confirm(f"Install {branch} @ {sha[:7]}? [y/N] "):
            return

        url = f"https://github.com/{REPO}/archive/refs/heads/{branch}.tar.gz"
    else:
        try:
            release = github_api("releases/latest")
        except (OSError, ValueError) as exc:
            sys.exit(f"Could not determine the latest release: {exc}")
        tag = release.get("tag_name", "")
        if not tag:
            sys.exit("Could not determine the latest release. Aborting.")
        latest = tag.removeprefix("v")

        print(f"Installed version: {installed}")
        print(f"Latest release:    {latest}")

        if installed == latest:
            print("Already up to date.")
            return

        if not confirm(f"Update to {latest}? [y/N] "):
            return

        url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"

    try:
        archive = fetch(url)
    except OSError as exc:
        sys.exit(f"Download failed: {exc}")

    with tempfile.TemporaryDirectory() as tmp:
        extract(archive, tmp)

        # GitHub tarballs contain exactly one top-level directory.
        extracted = os.path.join(tmp, os.listdir(tmp)[0])

        # Keep the current settings; options added in newer versions fall back
        # to their defaults.
        shutil.copy(CONF_FILE, extracted)

        subprocess.run(["./install.py"], cwd=extracted, check=True)

    # Record the installed dev commit so the notify service's update check can
    # compare against it. install.py wipes /opt, so a stale COMMIT from an
    # earlier dev install disappears on its own when a release is installed.
    if branch:
        COMMIT_FILE.write_text(sha)


if __name__ == "__main__":
    main()
