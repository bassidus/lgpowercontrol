# Updates to the latest release, or (--dev) the dev branch HEAD. Settings/pairing survive.
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from lgpowercontrol.common import REPO, CONF_FILE, COMMIT_FILE, VERSION_FILE, github_api, require_root


def confirm(prompt: str) -> bool:
    answer = input(prompt).strip().lower()
    return not answer or answer.startswith("y")


def main() -> None:
    branch = ""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--dev":
            branch = "dev"
        else:
            sys.exit("Usage: lgpowercontrol-update [--dev]")

    require_root()
    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed. Run install.py instead.")

    installed = "none"
    if os.access(VERSION_FILE, os.R_OK):
        installed = VERSION_FILE.read_text().strip()

    sha = ""
    if branch:  # VERSION lags on dev, so show the latest commit instead of an up-to-date check
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

        if not confirm(f"Install {branch} @ {sha[:7]}? [Y/n] "):
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

        if not confirm(f"Update to {latest}? [Y/n] "):
            return

        url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"

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

    if branch:  # lets notify's update-check compare against dev; a release install clears it anyway
        COMMIT_FILE.write_text(sha)
