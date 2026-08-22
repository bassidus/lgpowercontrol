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

from conflict_check import run_conflict_check  # noqa: E402
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

# The teardown side, and the layout of everything installed outside INSTALL_DIR, live in the
# package so that an installation can remove and update itself without a clone. One table still
# drives both sides; it is just imported now rather than declared here.
from lgpowercontrol.uninstall import (  # noqa: E402
    DISPATCHER_DIR,
    DISPATCHER_SHIM,
    DISPATCHER_SHIM_TEXT,
    LOCAL_BIN_LINK,
    PREDOWN_LINK,
    SLEEP_DIR,
    SLEEP_HOOK_LINK,
    UNITS,
    uninstall,
)
from lgpowercontrol.update import main as run_update  # noqa: E402

# One wrapper per entry point, replacing pip's console_scripts. sys.path is baked into the script
# rather than set as Environment= in the units: NetworkManager and systemd-sleep exec the
# dispatcher and the hook directly, with none of our environment. /usr/bin/python3 over
# 'env python3' so the interpreter can't come from an inherited PATH. sys.exit() around the call
# because cli.main() returns the exit code its callers read; a bare call discards it and every
# command looks like it succeeded. Entry points returning None still exit 0.
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


# Hands the conf and the pairing key to the user who ran the installer, so `authorize` and editing
# the conf need no sudo. Only those two data files change hands: bin/ and lib/ stay root-owned,
# because root executes them at boot, at suspend and from the monitor service.
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
# 0644 on the pairing key is deliberate: any local user can then control the TV, which matches how
# this is used - one person at their own desktop. The group below is the user's primary one, which
# is per-user wherever useradd sets USERGROUPS_ENAB (measured yes on Ubuntu 22.04 and openSUSE
# Tumbleweed); a shared primary group instead widens directory write to its members, with bin/ and
# lib/ still out of reach. Nothing read from the conf ever reaches an exec as root - every value is
# consumed as data - so a future key naming a path or a command would make this a root escalation.
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


def install(force: bool = False) -> None:
    require_root()
    os.chdir(Path(__file__).resolve().parent)  # everything below uses repo-relative paths

    # Before the TV check below: no point asking the user to switch the TV on only to abort.
    if not force:
        run_conflict_check()

    # The repo copy is the file the user edits; it is copied over the installed one further down,
    # so a reinstall lands on exactly what the repo says and carries nothing over from the old one.
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

    # pip's temp dir and the mkstemp'd pairing db both carry /tmp's user_tmp_t SELinux label into
    # INSTALL_DIR (copy2 preserves xattrs, and a cross-device move is a copy). Confined domains
    # cannot read user_tmp_t, so the NM dispatcher died on "No module named lgpowercontrol" while
    # the same wrapper run by hand as root worked. No restorecon binary means no SELinux.
    if shutil.which("restorecon"):
        subprocess.run(["restorecon", "-R", str(INSTALL_DIR)], check=False)

    # No dispatcher dir means no NetworkManager (systemd-networkd only), where TV-off at
    # suspend is unsupported by design - the sleep hook below still covers the wake side.
    # The flag carries that all the way down to the Wake-on-LAN section, which is moot without it.
    have_dispatcher = DISPATCHER_DIR.is_dir()
    if have_dispatcher:
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
            subprocess.run(cmd, stderr=subprocess.DEVNULL, check=False)

    print()
    # Not check=True: a traceback is the wrong ending for an install where every file is already
    # in place. Only the pairing is missing, and that is a one-liner to finish by hand.
    pairing_rc = subprocess.run([str(LGPC_BIN), "authorize"], check=False).returncode

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

    # Without the dispatcher, say so once and skip the Wake-on-LAN section rather than pitching it
    # and then refusing: its headline benefit below *is* the pre-down race, and `wol` drives nmcli,
    # which is absent on the systemd-networkd machines this branch describes. Worded as "not found"
    # because what is tested is the dispatcher directory, which a half-removed NM also lacks.
    if not have_dispatcher:
        print("\n\033[33mNetworkManager was not found, so turning the TV off at suspend is\n"
              "unavailable on this system. Waking the TV at resume works as usual.\033[0m")
    else:
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
                    result = subprocess.run([str(LGPC_BIN), "wol", "--enable", "--interface", device], check=False)
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
    elif "--update" in sys.argv[1:]:
        # Fetches the current release and installs *that*, keeping this installation's settings
        # and pairing - it deliberately does not install this clone, which is what a plain run
        # above is for. The same thing as `lgpowercontrol update`, for a clone whose installation
        # is too old to have the subcommand. Remaining arguments (--branch, --repo, --force) go
        # to its own parser.
        sys.exit(run_update([arg for arg in sys.argv[1:] if arg != "--update"]))
    else:
        # --force skips the conflict check entirely, LG_Buddy included; an escape hatch for
        # deliberately running two installations side by side.
        install(force="--force" in sys.argv[1:])
