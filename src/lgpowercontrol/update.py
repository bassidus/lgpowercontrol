# The `update` subcommand. Clones the current release, carries the installed settings and the TV
# pairing over to it, and runs *its* installer - so an update always installs with the new
# version's install.py, never with the one this copy was shipped with.
#
# It lives in the package rather than beside install.py so that an installation can update itself:
# `sudo lgpowercontrol update` needs no clone lying around, which is the only copy of this project
# a user is otherwise expected to keep. `./install.py --update` from a clone runs this same code,
# for an installation too old to have the subcommand.
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lgpowercontrol.common import (
    CONF_FILE,
    INSTALL_DIR,
    LIB_DIR,
    PAIRING_DB,
    confirm,
    load_conf,
    require_root,
)

REPO_URL = "https://github.com/bassidus/lgpowercontrol.git"
CONF_NAME = "lgpowercontrol.conf"

# One timestamped directory per run, holding both the backup and the clone. /var/tmp rather than
# /tmp so it survives a reboot, and rather than a fixed path so one update never overwrites the
# previous one's backup; systemd-tmpfiles clears /var/tmp after 30 days, so nothing accumulates.
WORK_ROOT = Path("/var/tmp")

YELLOW = "\033[33m"
RESET  = "\033[0m"


def warn(msg: str) -> None:
    print(f"{YELLOW}{msg}{RESET}")


def installed_version() -> str:
    for info in LIB_DIR.glob("lgpowercontrol-*.dist-info"):
        return info.name.removeprefix("lgpowercontrol-").removesuffix(".dist-info")
    return "unknown"


# Anchored at line start, so only [project].version matches and not, say, a version inside a
# dependency specifier further in.
def repo_version(repo: Path) -> str:
    try:
        text = (repo / "pyproject.toml").read_text()
    except OSError:
        return "unknown"
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


# Like install.py's set_conf_value, except that only the value token is replaced, not the rest of
# the line: install.py writes LGTV_MAC alone, whose line carries nothing else, while this one
# rewrites most of the file - and half those lines end in a trailing comment that documents the
# value ('SHARED_TV="0" # 1 = enabled | 0 = disabled'). Replacing the whole line drops it, so an
# updated install would end up with a conf less readable than a fresh one.
def set_conf_value(path: Path, key: str, value: str) -> None:
    content = path.read_text()
    # repl as a function: a '\' in the value must not be parsed as a group reference
    content = re.sub(rf'(?m)^({re.escape(key)}=)(?:"[^"]*"|\S*)',
                     lambda match: f'{match.group(1)}"{value}"', content, count=1)
    path.write_text(content)


# The installed values are written into the clone's conf *before* install.py runs, because
# install.py copies the repo conf over the installed one - that copy is the seam this hooks into,
# and it is why the README could say configuration is not preserved between releases.
#
# Only keys the new release still has are carried; a key that is gone would otherwise be written
# nowhere (set_conf_value's regex finds no line) and silently look carried. Returns the three
# groups so the caller can report them.
def merge_conf(old: dict[str, str], new_conf: Path) -> tuple[list[str], list[str], list[str]]:
    new = load_conf(new_conf)
    carried = [key for key in new if key in old]
    added   = [key for key in new if key not in old]
    removed = [key for key in old if key not in new]

    for key in carried:
        set_conf_value(new_conf, key, old[key])
    return carried, added, removed


def report_conf(old: dict[str, str], new_conf: Path, new_version: str) -> None:
    carried, added, removed = merge_conf(old, new_conf)
    print(f"\n{len(carried)} setting{'' if len(carried) == 1 else 's'} carried over.")

    if added:
        warn(f"\nWarning: {len(added)} setting{'' if len(added) == 1 else 's'} new in {new_version}, "
             "left at the default:")
        new = load_conf(new_conf)
        for key in added:
            print(f'  {key}="{new[key]}"')
        print("Read what they do in the new conf, then adjust them after the update.")

    if removed:
        warn(f"\nWarning: {len(removed)} setting{'' if len(removed) == 1 else 's'} no longer exist"
             f"{'s' if len(removed) == 1 else ''} in {new_version} and will be dropped:")
        for key in removed:
            print(f'  {key}="{old[key]}"')


def clone(repo: Path, url: str, branch: str | None) -> None:
    if not shutil.which("git"):
        sys.exit("git is not installed, so the new version cannot be fetched.")
    cmd = ["git", "clone", "--quiet", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(repo)]
    print(f"Cloning {url}" + (f" ({branch})" if branch else "") + " ...")
    if subprocess.run(cmd, check=False).returncode != 0:
        sys.exit("git clone failed. Check your network connection and try again.")


def update(url: str, branch: str | None, force: bool) -> int:
    require_root()

    if not CONF_FILE.is_file():
        sys.exit(f"No configuration found at {CONF_FILE}, so there is nothing to update.\n"
                 "Install first: see the README.")

    old_conf = load_conf(CONF_FILE)
    old_version = installed_version()
    print(f"Installed version: {old_version}")

    # No exist_ok: /var/tmp is world-writable, so a directory that is already there is not ours.
    work = WORK_ROOT / f"lgpowercontrol-update-{time.strftime('%Y%m%d-%H%M%S')}"
    backup, repo = work / "backup", work / "repo"
    backup.mkdir(parents=True)

    # install.py preserves the pairing db across the reinstall on its own; this copy is the spare
    # for the case where it does not get that far, and it is the only copy of the old conf once
    # the new one is written over it.
    shutil.copy2(CONF_FILE, backup)
    saved = [CONF_FILE.name]
    if PAIRING_DB.is_file():
        shutil.copy2(PAIRING_DB, backup)
        saved.append(PAIRING_DB.name)
    else:
        warn(f"\nNo pairing key at {PAIRING_DB} - you will need `lgpowercontrol authorize`\n"
             "after the update.")
    print(f"\nSaved {' and '.join(saved)} to {backup}")

    clone(repo, url, branch)
    new_conf = repo / CONF_NAME
    if not new_conf.is_file():
        sys.exit(f"The clone has no {CONF_NAME} - {url} does not look like this project.")

    new_version = repo_version(repo)
    print(f"New version:       {new_version}")
    if new_version == old_version:
        print("Same version as the installed one - reinstalling it.")

    report_conf(old_conf, new_conf, new_version)

    print(f"\nThe configuration to be installed is {new_conf}")
    if not confirm("Run the installer now? [Y/n] "):
        sys.exit(f"Stopped. Nothing has changed. Continue later with:\n"
                 f"  cd {repo} && sudo ./install.py")

    print()
    # Run from the clone, which is the whole point: when this runs as the installed
    # `lgpowercontrol update`, install.py deletes and rebuilds the very LIB_DIR this module was
    # imported from. Nothing below may need a fresh import - already-imported modules live on in
    # memory, but a first import during the rebuild would find no file. Keep the tail trivial.
    cmd = [sys.executable, str(repo / "install.py")] + (["--force"] if force else [])
    rc = subprocess.run(cmd, cwd=repo, check=False).returncode

    if rc != 0:
        warn(f"\nThe installer exited with {rc}. The previous configuration and pairing key\n"
             f"are still in {backup}")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Update an existing {INSTALL_DIR} installation, keeping its settings and pairing.")
    parser.add_argument("--branch", help="branch or tag to install (default: the repository's default)")
    parser.add_argument("--repo", default=REPO_URL, help=f"repository to clone (default: {REPO_URL})")
    parser.add_argument("--force", action="store_true",
                        help="pass --force to the installer, skipping its conflict check")
    args = parser.parse_args(argv)
    return update(args.repo, args.branch, args.force)
