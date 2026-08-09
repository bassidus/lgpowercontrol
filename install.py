#!/usr/bin/env python3
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

sys.dont_write_bytecode = True  # a root-owned __pycache__ here would need sudo to remove
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lgpowercontrol.common import (  # noqa: E402
    BIN_DIR,
    CONF_FILE,
    INSTALL_DIR,
    LGPC_BIN,
    LIB_DIR,
    PAIRING_DB,
    confirm,
    load_conf,
    nic_wol_setting,
    require_root,
    sole_wired_connection,
    wired_devices,
)
from lgpowercontrol.units import build_units  # noqa: E402

from conflict_check import run_conflict_check  # noqa: E402

DISPATCHER_DIR = Path("/etc/NetworkManager/dispatcher.d")
SLEEP_DIR      = Path("/usr/lib/systemd/system-sleep")
LOCAL_BIN_DIR  = Path("/usr/local/bin")

UNITS = build_units(BIN_DIR)

# One wrapper per entry point, replacing what pip's console_scripts used to generate. sys.path is
# baked into the script rather than set as Environment= in the units: NetworkManager and
# systemd-sleep exec the dispatcher and the hook directly, with none of our environment.
# /usr/bin/python3 over 'env python3' so the interpreter can't be picked from an inherited PATH.
# sys.exit() around the call rather than a bare call: cli.main() returns the exit code its callers
# read, and a bare call discards it, so every command looks like it succeeded. That silently broke
# authorize(), which branches on STATUS's rc to tell "unpaired" from "unreachable" - it reported
# success against a TV that was never there, and never reached the branch that wipes a rejected
# pairing key. Entry points that return None still exit 0, as before.
WRAPPER = """\
#!/usr/bin/python3
import sys
sys.path.insert(0, "{lib_dir}")
from lgpowercontrol.{module} import {func}
sys.exit({func}())
"""

# script name -> (module, function). No uninstall counterpart is needed: these live under
# INSTALL_DIR, which uninstall() removes wholesale.
ENTRY_POINTS = {
    "lgpowercontrol":                ("cli",     "main"),
    "lgpowercontrol-monitor":        ("monitor", "main"),
    "lgpowercontrol-notify":         ("notify",  "main"),
    "lgpowercontrol-sleep-listener": ("suspend", "listener"),
    "lgpowercontrol-sleep-hook":     ("suspend", "hook"),
    "lgpowercontrol-nm-dispatcher":  ("suspend", "dispatcher"),
}

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
# systemd-run are all denied with no AVC logged (they are dontaudit'ed). preparing_for_sleep() then
# reads as False and TV-off at suspend silently never happens - no error, nothing in the journal.
# Relabelling the /opt target to NetworkManager_dispatcher_script_t works too, but needs semanage
# installed plus an fcontext -d at uninstall; the shim needs neither and is identical where there
# is no SELinux. Labelling the target bin_t does NOT work - it stays in the confined domain, so
# don't "simplify" this to a semanage call with bin_t. Measured on Bazzite; the policy is Fedora's,
# so it covers Workstation and Silverblue as well. pre-down.d/ keeps its relative symlink to this
# file, which inherits the right label through it.
DISPATCHER_SHIM = DISPATCHER_DIR / "90-lgpowercontrol"
DISPATCHER_SHIM_TEXT = f'#!/bin/sh\nexec {BIN_DIR}/lgpowercontrol-nm-dispatcher "$@"\n'

# Everything install() creates outside INSTALL_DIR, which uninstall() removes wholesale.
TEARDOWN_PATHS = [link for _, link in LINKS] + [DISPATCHER_SHIM]


def copy_verbose(src: str, dst_dir: Path) -> None:
    dest = shutil.copy(src, dst_dir)
    print(f"'{src}' -> '{dest}'")

def link_verbose(target: Path | str, link: Path) -> None:
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    print(f"'{target}' -> '{link}'")

def write_unit(key: str) -> None:
    unit = UNITS[key]
    unit.path.write_text(unit.text)
    print(f"Wrote {unit.path}")

def write_wrappers() -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name, (module, func) in ENTRY_POINTS.items():
        path = BIN_DIR / name
        path.write_text(WRAPPER.format(lib_dir=LIB_DIR, module=module, func=func))
        path.chmod(0o755)
        print(f"Wrote {path}")

# The installed conf is a fresh copy of the repo one, so the only values written back here are
# the ones the installer works out on its own. A key the template lacks is silently ignored.
def set_conf_value(key: str, value: str) -> None:
    content = CONF_FILE.read_text()
    # repl as a function: a '\' in the value must not be parsed as a group reference
    content = re.sub(rf'(?m)^{re.escape(key)}=.*', lambda _: f'{key}="{value}"', content, count=1)
    CONF_FILE.write_text(content)


# Hands the conf and the pairing key to the user who ran the installer, so `authorize` and
# editing the conf need no sudo. (`wol` needs none either, but that is polkit's call, not ours -
# see admin.py.) Only those two data files change hands: bin/ and lib/ stay root-owned, because
# root executes them at boot, at suspend and from the monitor service.
#
# INSTALL_DIR itself must stay root-owned, and the sticky bit is the whole safety argument.
# Directory write permission governs the *namespace*, not the contents, so a user who could
# rename entries here would not need to touch root-owned bin/ at all - `mv bin bin.old; mkdir bin`
# and root execs their file at the next boot. The sticky bit restricts renames and unlinks to the
# owner of each entry, which leaves the user their two files (plus sqlite's -journal and vim's
# temp file, both of which need directory write) and nothing else. Never chown INSTALL_DIR to the
# user as a "simplification": man 7 inode exempts the directory's owner from the sticky bit, so
# that one change silently gives back everything this protects.
#
# 0644 on the pairing key is deliberate: any local user can then control the TV, which matches
# how this is used - one person at their own desktop. The group below is the user's primary one,
# which is a per-user group wherever useradd sets USERGROUPS_ENAB (measured yes on Ubuntu 22.04
# and openSUSE Tumbleweed). Where an admin instead hands out a shared primary group, every member
# can write in this directory too - the same blast radius as the world-readable key, and the
# sticky bit still keeps bin/ and lib/ out of reach either way, so it stays a deliberate trade
# rather than a hole. Note also that nothing read from the conf
# ever reaches an exec as root; every value is consumed as data (a socket address, a hex MAC, a
# string in a websocket payload, ints, a bool). That is what makes a user-writable conf safe, so
# a future key naming a path or a command would turn this into a root escalation.
def set_ownership() -> None:
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:  # installed from a root login - no user to hand anything to
        print(f"\nSUDO_USER is not set, so {INSTALL_DIR} stays root-owned.\n"
              "authorize, wol and editing the conf will need sudo.")
        return
    try:
        user = pwd.getpwnam(sudo_user)
    except KeyError:
        return

    os.chown(INSTALL_DIR, 0, user.pw_gid)  # root-owned on purpose - see above
    INSTALL_DIR.chmod(0o1775)
    for path in (CONF_FILE, PAIRING_DB):
        if path.is_file():  # the pairing db is absent when authorize failed
            os.chown(path, user.pw_uid, user.pw_gid)
            path.chmod(0o644)
    print(f"\n{INSTALL_DIR} handed to {sudo_user}: authorize and conf edits need no sudo.")


def uninstall(quiet: bool = False) -> None:
    require_root()

    # One call per unit, not one call listing all four: sleep.service only exists on immutable
    # /usr (the listener fallback), and systemctl disable --now fails *atomically* when any named
    # unit is missing - rc 1, nothing disabled, nothing stopped, and the error goes to the
    # stderr hidden below. On every ordinary distro that left dangling *.target.wants symlinks
    # and the monitor still running. Worst on the update path, where install() calls this first:
    # enable --now does not restart an already-active unit, so the monitor kept serving the
    # pre-update code until the next reboot. Bazzite was the only platform where this worked.
    for unit in (
        "lgpowercontrol-boot.service", "lgpowercontrol-shutdown.service",
        "lgpowercontrol-monitor.service", "lgpowercontrol-sleep.service",
    ):
        subprocess.run(["systemctl", "disable", "--now", unit], stderr=subprocess.DEVNULL)
    subprocess.run(
        ["systemctl", "--global", "disable", "lgpowercontrol-notify.service"],
        stderr=subprocess.DEVNULL,
    )

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        subprocess.run(
            ["systemctl", f"--machine={sudo_user}@", "--user", "stop", "lgpowercontrol-notify.service"],
            stderr=subprocess.DEVNULL,
        )

    shutil.rmtree(INSTALL_DIR, ignore_errors=True)

    for unit in UNITS.values():
        unit.path.unlink(missing_ok=True)

    for path in TEARDOWN_PATHS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Read-only /usr (Bazzite): unlinking the sleep hook raises EROFS, and it raises it
            # even when the file was never there, so missing_ok= does not cover this. Unhandled,
            # it took down both the uninstall and the install that calls this first.
            pass

    subprocess.run(["systemctl", "daemon-reload"])

    if not quiet:
        print("LGPowerControl uninstalled.")


def install(force: bool = False) -> None:
    require_root()
    os.chdir(Path(__file__).resolve().parent)  # everything below uses repo-relative paths

    # Before the TV check below: no point asking the user to switch the TV on only to abort.
    if not force:
        run_conflict_check()

    # The repo copy is the file the user edits; it is copied over the installed one further down,
    # so a reinstall always lands on exactly what the repo says. Nothing is carried over from the
    # old installation, which is what makes reinstalling a way to repair a mangled conf file.
    conf = load_conf("lgpowercontrol.conf")

    lgtv_ip = conf.get("LGTV_IP", "")
    if not lgtv_ip:
        sys.exit(
            "LGTV_IP is not set. Edit lgpowercontrol.conf and enter your TV's IP address,\n"
            "then run the installer again.")

    try:
        socket.create_connection((lgtv_ip, 3001), timeout=2).close()
    except OSError:
        sys.exit(f"{lgtv_ip} is unreachable on port 3001. Make sure the TV is on. Aborting installation")

    if shutil.which("apt-get"):  # Debian/Ubuntu split venv out of python3; a no-op if present
        subprocess.run(["apt-get", "install", "-y", "python3-venv"], check=True)

    lgtv_mac = conf.get("LGTV_MAC", "")
    if not lgtv_mac:
        with open("/proc/net/arp") as f:
            next(f)  # header line
            for line in f:
                fields = line.split()
                if fields[0] == lgtv_ip and fields[3] != "00:00:00:00:00:00":
                    lgtv_mac = fields[3]
                    break
        if not lgtv_mac:
            sys.exit(f"Could not detect MAC for {lgtv_ip}. Set LGTV_MAC in lgpowercontrol.conf")
        print(f"Detected TV MAC address: {lgtv_mac}")

    # carried across the reinstall below, which wipes the install directory
    saved_pairing_db = None
    if PAIRING_DB.is_file():
        fd, saved_pairing_db = tempfile.mkstemp()
        os.close(fd)
        shutil.copy(PAIRING_DB, saved_pairing_db)

    uninstall(quiet=True)
    LIB_DIR.mkdir(parents=True)  # creates /opt/lgpowercontrol too

    # A throwaway venv purely to obtain a pip; --target then puts the package and its dependencies
    # in a plain directory that any Python version can import. Going through a venv rather than a
    # system pip keeps the installer's requirements at python3 + venv, exactly as before.
    with tempfile.TemporaryDirectory() as tmp:
        venv.create(tmp, with_pip=True)
        pip = str(Path(tmp) / "bin" / "pip")
        subprocess.run([pip, "install", "--quiet", "--target", str(LIB_DIR), "."], check=True)

    for artifact in ("build", "src/lgpowercontrol.egg-info"):  # root-owned build artifacts pip leaves in the repo
        shutil.rmtree(artifact, ignore_errors=True)
    shutil.rmtree(LIB_DIR / "bin", ignore_errors=True)  # pip's console scripts, shebanged to the temp venv

    write_wrappers()

    if saved_pairing_db:
        shutil.move(saved_pairing_db, PAIRING_DB)

    copy_verbose("lgpowercontrol.conf", INSTALL_DIR)

    for key in UNITS:
        if key != "sleep":  # written conditionally below instead
            write_unit(key)

    set_conf_value("LGTV_MAC", lgtv_mac)

    # pip installs through a temp directory and the saved pairing db comes from mkstemp, and both
    # carry /tmp's user_tmp_t SELinux label along into INSTALL_DIR (shutil.copy2 preserves xattrs,
    # and a cross-device move is a copy). Confined domains cannot read user_tmp_t, so the NM
    # dispatcher died on "No module named lgpowercontrol" while the same wrapper run by hand as
    # root worked. Resetting the tree to the labels its path implies is the fix. No restorecon
    # binary means no SELinux, so nothing to do.
    if shutil.which("restorecon"):
        subprocess.run(["restorecon", "-R", str(INSTALL_DIR)])

    # No dispatcher dir means no NetworkManager (systemd-networkd only), where TV-off at
    # suspend is unsupported by design - the sleep hook below still covers the wake side.
    if DISPATCHER_DIR.is_dir():
        (DISPATCHER_DIR / "pre-down.d").mkdir(parents=True, exist_ok=True)
        DISPATCHER_SHIM.write_text(DISPATCHER_SHIM_TEXT)  # a file, not a symlink - see above
        DISPATCHER_SHIM.chmod(0o755)
        print(f"Wrote {DISPATCHER_SHIM}")
        link_verbose(*PREDOWN_LINK)

    try:
        SLEEP_DIR.mkdir(parents=True, exist_ok=True)
        link_verbose(*SLEEP_HOOK_LINK)
    except OSError:  # /usr read-only (e.g. Bazzite) - fall back to the /etc listener service
        write_unit("sleep")
        use_listener = True
    else:
        use_listener = False

    # The command a user is expected to type by hand; symlinked onto PATH for convenience.
    link_verbose(*LOCAL_BIN_LINK)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "lgpowercontrol-boot.service", "lgpowercontrol-shutdown.service"],
        check=True,
    )
    subprocess.run(["systemctl", "enable", "--now", "lgpowercontrol-monitor.service"], check=True)
    if use_listener:
        subprocess.run(["systemctl", "enable", "--now", "lgpowercontrol-sleep.service"], check=True)

    # notify needs the desktop session, so it is a user unit enabled per session
    subprocess.run(["systemctl", "--global", "enable", "lgpowercontrol-notify.service"], check=True)

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        machine = f"--machine={sudo_user}@"
        for cmd in (
            ["systemctl", machine, "--user", "daemon-reload"],
            ["systemctl", machine, "--user", "start", "lgpowercontrol-notify.service"],
        ):
            subprocess.run(cmd, stderr=subprocess.DEVNULL)

    print()
    # Not check=True: this could not fail while the wrappers were discarding exit codes, and now
    # that it can, a traceback is the wrong ending for an install where every file is already in
    # place. Only the pairing is missing, and that is a one-liner to finish by hand.
    pairing_rc = subprocess.run([str(LGPC_BIN), "authorize"]).returncode

    # After authorize rather than before: it runs as root here and creates the pairing db, so
    # anything done earlier would be undone. Before the exit below, so a failed pairing still
    # leaves an installation the user owns - the retry it suggests depends on that.
    set_ownership()

    if pairing_rc != 0:
        sys.exit(
            "\nPairing did not complete - everything else is installed. Finish with:\n"
            "  lgpowercontrol authorize\n"
            "\nThen, optionally, Wake-on-LAN on this computer's network card (see README):\n"
            "  lgpowercontrol wol --enable"
        )

    # after authorize: enabling reactivates the connection, which drops the network briefly
    print("\nWake-on-LAN on your computer's network card:\n\n"
          "  + Makes turning the TV off at suspend more reliable\n"
          "  + Lets other machines on your network wake this computer\n"
          "  - The network card stays powered during suspend (slightly higher power draw)\n"
          "  - Extremely rarely, stray network traffic can wake this computer unexpectedly\n\n"
          "Reversible anytime with: lgpowercontrol wol --disable")

    sole_wired = sole_wired_connection()
    if not sole_wired:
        devices = wired_devices()
        if not devices:
            print("\nNo wired network device found - skipping the Wake-on-LAN question\n"
                  "(it is an Ethernet feature; on Wi-Fi, TV-off at suspend can occasionally miss).")
        elif len(devices) > 1:
            print("\nSeveral wired network devices found (" + ", ".join(devices) + ") - skipping the\n"
                  "Wake-on-LAN question. Enable it on the right one with:\n"
                  "  lgpowercontrol wol --enable --interface <device>")
        else:
            print(f"\n{devices[0]} has no active network connection - skipping the Wake-on-LAN\n"
                  "question. Enable it later with: lgpowercontrol wol --enable")
    else:
        device, connection = sole_wired
        if nic_wol_setting(connection) != "magic":
            if confirm(f"\nEnable it on {device}? [Y/n] "):
                result = subprocess.run([str(LGPC_BIN), "wol", "--enable", "--interface", device])
                if result.returncode != 0:
                    print("\033[33mEnabling Wake-on-LAN failed; TV-off at suspend keeps working via the dispatcher.\033[0m")
            else:
                print("You can enable it later with: lgpowercontrol wol --enable")
        else:  # already enabled (or updates re-running) - skip the question
            print(f"\nWake-on-LAN is already enabled on {device} - no action needed.")

    print()
    print("Installation complete!")


if __name__ == "__main__":
    if "--uninstall" in sys.argv[1:]:
        uninstall(quiet="--quiet" in sys.argv[1:])
    else:
        # --force skips the conflict check entirely, LG_Buddy included; an escape hatch for
        # deliberately running two installations side by side.
        install(force="--force" in sys.argv[1:])
