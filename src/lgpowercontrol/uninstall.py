# The `uninstall` subcommand, plus the layout it shares with install.py: everything this project
# puts outside INSTALL_DIR, which is the part a teardown has to know by name.
#
# It lives in the package rather than in install.py so that an installation can remove itself with
# `sudo lgpowercontrol uninstall`, needing no clone - the same reason update.py lives here.
# install.py imports these names back, so one table still drives both sides.
import argparse
import os
import shutil
import subprocess
from pathlib import Path

from lgpowercontrol.common import BIN_DIR, INSTALL_DIR, require_root
from lgpowercontrol.units import build_units

DISPATCHER_DIR = Path("/etc/NetworkManager/dispatcher.d")
SLEEP_DIR      = Path("/usr/lib/systemd/system-sleep")
LOCAL_BIN_DIR  = Path("/usr/local/bin")

UNITS = build_units(BIN_DIR)

# (target, link) - install() creates these (some conditionally); uninstall() tears them all down
# via TEARDOWN_PATHS below. One list drives both, so the two can't drift apart.
# The dispatcher must be reachable from both dirs: dispatcher.d/ gets 'up' (via the shim below),
# pre-down.d/ gets 'pre-down'.
PREDOWN_LINK = ("../90-lgpowercontrol", DISPATCHER_DIR / "pre-down.d" / "90-lgpowercontrol")
SLEEP_HOOK_LINK = (BIN_DIR / "lgpowercontrol-sleep-hook", SLEEP_DIR / "lgpowercontrol")
LOCAL_BIN_LINK = (BIN_DIR / "lgpowercontrol", LOCAL_BIN_DIR / "lgpowercontrol")
LINKS = [PREDOWN_LINK, SLEEP_HOOK_LINK, LOCAL_BIN_LINK]

# dispatcher.d/ gets a real two-line file, not a symlink into INSTALL_DIR, and this is load-bearing
# on any SELinux distro. SELinux labels an exec by the target file, and only a file labelled
# NetworkManager_dispatcher_script_t - which dispatcher.d/ hands out by inheritance - transitions
# into the permissive NetworkManager_dispatcher_custom_t domain. A symlink to INSTALL_DIR (usr_t)
# leaves the script in the confined NetworkManager_dispatcher_t instead, where logind, /run and
# systemd-run are all denied with no AVC logged (they are dontaudit'ed): preparing_for_sleep()
# reads as False and TV-off at suspend silently never happens. Relabelling the /opt target works
# too but needs semanage plus an fcontext -d at uninstall; the shim needs neither. Labelling the
# target bin_t does NOT work - it stays in the confined domain, so don't "simplify" this to a
# semanage call with bin_t. Measured on Bazzite; the policy is Fedora's, so it covers Workstation
# and Silverblue too. pre-down.d/ keeps its relative symlink to this file, inheriting the label.
DISPATCHER_SHIM = DISPATCHER_DIR / "90-lgpowercontrol"
DISPATCHER_SHIM_TEXT = f'#!/bin/sh\nexec {BIN_DIR}/lgpowercontrol-nm-dispatcher "$@"\n'

# Everything install() creates outside INSTALL_DIR, which uninstall() removes wholesale.
TEARDOWN_PATHS = [link for _, link in LINKS] + [DISPATCHER_SHIM]


def uninstall(quiet: bool = False) -> None:
    require_root()

    # One call per unit, not one listing all four: sleep.service only exists on immutable /usr
    # (the listener fallback), and systemctl disable --now fails *atomically* when any named unit
    # is missing - rc 1, nothing disabled, nothing stopped, with the error hidden by the stderr
    # below. On the update path, where install() calls this first, that leaves dangling
    # *.target.wants symlinks and the old monitor serving until the next reboot.
    for unit in (
        "lgpowercontrol-boot.service", "lgpowercontrol-shutdown.service",
        "lgpowercontrol-monitor.service", "lgpowercontrol-sleep.service",
    ):
        subprocess.run(["systemctl", "disable", "--now", unit], stderr=subprocess.DEVNULL, check=False)
    subprocess.run(
        ["systemctl", "--global", "disable", "lgpowercontrol-notify.service"],
        stderr=subprocess.DEVNULL,
        check=False,
    )

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        subprocess.run(
            ["systemctl", f"--machine={sudo_user}@", "--user", "stop", "lgpowercontrol-notify.service"],
            stderr=subprocess.DEVNULL,
            check=False,
        )

    # Run as `lgpowercontrol uninstall`, this deletes the very library directory the running
    # process imported this module from. Nothing below may need a fresh import - already-imported
    # modules live on in memory, but a first import from here on would find no file. Everything
    # after this line uses os, shutil and subprocess, all imported at module load.
    shutil.rmtree(INSTALL_DIR, ignore_errors=True)

    for unit in UNITS.values():
        unit.path.unlink(missing_ok=True)

    for path in TEARDOWN_PATHS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Read-only /usr (Bazzite): unlinking the sleep hook raises EROFS, and it raises it
            # even when the file was never there, so missing_ok= does not cover this.
            pass

    subprocess.run(["systemctl", "daemon-reload"], check=False)

    if not quiet:
        print("LGPowerControl uninstalled.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Remove {INSTALL_DIR}, the installed services and the TV pairing.")
    parser.add_argument("--quiet", action="store_true", help="say nothing on success")
    args = parser.parse_args(argv)
    uninstall(quiet=args.quiet)
    return 0
