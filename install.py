#!/usr/bin/env python3
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# Run as root from the user's own clone (e.g. ~/downloads/lgpowercontrol): a
# __pycache__/*.pyc left behind there would be root-owned and unremovable by
# the user without sudo.
sys.dont_write_bytecode = True

from lgtvpc_common import (  # noqa: E402
    CONF_FILE,
    INSTALL_DIR,
    NIC_WOL_MARKER,
    PAIRING_DB,
    load_conf,
    require_root,
)

VENV_DIR = INSTALL_DIR / "bscpylgtv"

INSTALL_FILES = [
    "VERSION",
    "lgtvpc.conf",
    "lgtvpc_common.py",
    "scripts/lgtvpc",
    "scripts/monitor.py",
    "scripts/notify.py",
    "scripts/update-check.py",
    "scripts/update.py",
    "scripts/authorize.py",
    "scripts/lgtvpc-wol.py",
    "scripts/sleep-listener.py",
]
SYSTEM_UNITS = [
    "systemd/lgtvpc-shutdown.service",
    "systemd/lgtvpc-boot.service",
    "systemd/lgtvpc-monitor.service",
]
USER_UNITS = [
    "systemd/lgtvpc-notify.service",
    "systemd/lgtvpc-update-check.service",
    "systemd/lgtvpc-update-check.timer",
]
EXEC_FILES = [
    "lgtvpc",
    "monitor.py",
    "notify.py",
    "update-check.py",
    "update.py",
    "authorize.py",
    "lgtvpc-wol.py",
    "sleep-listener.py",
]


def copy_v(src: str, dst_dir: Path) -> None:
    dest = shutil.copy(src, dst_dir)
    print(f"'{src}' -> '{dest}'")


def setup_nm_dispatcher() -> None:
    dispatcher_dir = Path("/etc/NetworkManager/dispatcher.d")
    if not dispatcher_dir.is_dir():
        return

    pre_down_dir = dispatcher_dir / "pre-down.d"
    pre_down_dir.mkdir(parents=True, exist_ok=True)
    copy_v("scripts/90-lgtvpc", dispatcher_dir)
    (dispatcher_dir / "90-lgtvpc").chmod(0o755)

    link = pre_down_dir / "90-lgtvpc"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to("../90-lgtvpc")
    print(f"'../90-lgtvpc' -> '{link}'")


def setup_sleep_hook() -> bool:
    """Installs the systemd-sleep hook; returns True when the read-only-/usr
    fallback (the lgtvpc-sleep.service listener) is used instead and must be
    enabled after daemon-reload."""
    sleep_dir = Path("/usr/lib/systemd/system-sleep")
    dest = sleep_dir / "lgtvpc"
    try:
        sleep_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy("scripts/sleep.py", dest)
    except OSError:
        # /usr is read-only on immutable-OS distros (e.g. Bazzite). The
        # sleep-listener service does the same job from /etc, which stays
        # writable there.
        print(
            "\033[33m/usr is read-only (immutable OS) - "
            "using the lgtvpc-sleep.service listener instead of a systemd-sleep hook.\033[0m"
        )
        copy_v("systemd/lgtvpc-sleep.service", Path("/etc/systemd/system"))
        return True

    dest.chmod(0o755)
    print(f"Installed: {dest}")
    return False


def nmcli_get(*args: str) -> str:
    result = subprocess.run(["nmcli", "-g", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def wired_connection() -> tuple[str, str] | None:
    """The computer's single wired device and its active connection, or None
    when there is no clear-cut wired setup to offer NIC Wake-on-LAN on
    (Wi-Fi only, no NetworkManager, several wired devices, device without an
    active connection)."""
    out = nmcli_get("DEVICE,TYPE", "device", "status")
    devices = [line.split(":")[0] for line in out.splitlines() if line.endswith(":ethernet")]
    if len(devices) != 1:
        return None
    con = nmcli_get("GENERAL.CONNECTION", "device", "show", devices[0])
    if not con or con == "--":
        return None
    return devices[0], con


def offer_nic_wol() -> None:
    """Asks whether to enable Wake-on-LAN on the computer's wired adapter, so
    NetworkManager leaves the device up at suspend and TV-off runs race-free
    (see the README's suspend troubleshooting section).

    Must run after everything that talks to the TV: enabling reactivates the
    network connection, which drops the network for a moment."""
    wired = wired_connection()
    if not wired:
        print(
            "No wired network device found - skipping the Wake-on-LAN question\n"
            "(it is an Ethernet feature; on Wi-Fi, TV-off at suspend can occasionally miss)."
        )
        return
    device, con = wired

    # Already on (an earlier install, or done by hand): nothing to ask.
    # Updates re-run the installer, so this keeps them prompt-free.
    if nmcli_get("802-3-ethernet.wake-on-lan", "connection", "show", con) == "magic":
        return

    print(f"""
Enable Wake-on-LAN on your computer's network card ({device})?

  + Makes turning the TV off at suspend fully reliable (avoids a known race)
  + Lets other machines on your network wake this computer
  - The network card stays powered during suspend (slightly higher power draw)
  - Rarely, stray network traffic can wake the computer unexpectedly

Reversible anytime with: sudo /opt/lgtvpc/lgtvpc-wol.py --disable""")
    try:
        answer = input("Enable it? [Y/n] ").strip().lower()
    except EOFError:
        answer = "n"
    if answer in ("", "y", "yes"):
        # The network drops for a moment here while NM reactivates the
        # connection to push the setting down to the card.
        result = subprocess.run([str(INSTALL_DIR / "lgtvpc-wol.py"), "--enable", "--interface", device])
        if result.returncode == 0:
            NIC_WOL_MARKER.touch()
        else:
            print("\033[33mEnabling Wake-on-LAN failed; TV-off at suspend keeps working via the dispatcher.\033[0m")
    else:
        print("You can enable it later with: sudo /opt/lgtvpc/lgtvpc-wol.py --enable")


def patch_conf_mac(mac: str) -> None:
    content = CONF_FILE.read_text()
    content = re.sub(r'(?m)^LGTV_MAC=""', f'LGTV_MAC="{mac}"', content, count=1)
    CONF_FILE.write_text(content)


def probe_port(ip: str, port: int, timeout: float = 2) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_mac(ip: str) -> str | None:
    """Look up ip's MAC address in the kernel's ARP cache."""
    with open("/proc/net/arp") as f:
        next(f)  # header line
        for line in f:
            fields = line.split()
            if fields[0] == ip and fields[3] != "00:00:00:00:00:00":
                return fields[3]
    return None


def main() -> None:
    require_root()
    os.chdir(Path(__file__).resolve().parent)

    conf = load_conf("lgtvpc.conf")
    lgtv_ip = conf.get("LGTV_IP", "")
    if not lgtv_ip:
        sys.exit(
            "LGTV_IP is not set. Edit lgtvpc.conf and enter your TV's IP address,\n"
            "then run the installer again."
        )

    if not probe_port(lgtv_ip, 3001):
        sys.exit(
            f"{lgtv_ip} is unreachable on port 3001. Make sure the TV is on. Aborting installation"
        )

    # Debian/Ubuntu split venv out of the python3 package; installing is a no-op
    # when already present, and apt resolves the right versioned package.
    if shutil.which("apt"):
        subprocess.run(["apt-get", "install", "-y", "python3-venv"], check=True)

    lgtv_mac = conf.get("LGTV_MAC", "")
    if not lgtv_mac:
        lgtv_mac = find_mac(lgtv_ip)
        if not lgtv_mac:
            sys.exit(f"Could not detect MAC for {lgtv_ip}. Set LGTV_MAC in lgtvpc.conf")
        print(f"Detected TV MAC address: {lgtv_mac}")

    # Preserve the TV pairing database and the NIC-WoL marker across
    # reinstalls and updates.
    keydb_path = None
    if PAIRING_DB.is_file():
        fd, keydb_path = tempfile.mkstemp()
        os.close(fd)
        shutil.copy(PAIRING_DB, keydb_path)
    had_nic_wol_marker = NIC_WOL_MARKER.is_file()

    # Fresh start: remove any existing installation and legacy leftovers.
    subprocess.run(["./uninstall.py", "--quiet"], check=True)

    # Creates /opt/lgtvpc too.
    venv.create(VENV_DIR, with_pip=True)
    subprocess.run([f"{VENV_DIR}/bin/pip", "install", "--quiet", "bscpylgtv"], check=True)
    # pip is only needed during install; removing it shrinks the venv from ~15 MB to ~2 MB.
    subprocess.run([f"{VENV_DIR}/bin/pip", "uninstall", "--quiet", "-y", "pip"], check=True)

    # Restore the TV pairing database and the NIC-WoL marker.
    if keydb_path:
        shutil.move(keydb_path, PAIRING_DB)
    if had_nic_wol_marker:
        NIC_WOL_MARKER.touch()

    for f in INSTALL_FILES:
        copy_v(f, INSTALL_DIR)
    for f in SYSTEM_UNITS:
        copy_v(f, Path("/etc/systemd/system"))
    for f in USER_UNITS:
        copy_v(f, Path("/etc/systemd/user"))

    setup_nm_dispatcher()
    use_listener = setup_sleep_hook()

    patch_conf_mac(lgtv_mac)

    for f in EXEC_FILES:
        (INSTALL_DIR / f).chmod(0o755)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "lgtvpc-boot.service", "lgtvpc-shutdown.service"],
        check=True,
    )
    subprocess.run(["systemctl", "enable", "--now", "lgtvpc-monitor.service"], check=True)
    if use_listener:
        subprocess.run(["systemctl", "enable", "--now", "lgtvpc-sleep.service"], check=True)

    # The notify service must run inside the desktop session, so it's a user unit.
    # The update-check timer is also per-user (the notification needs the user's
    # D-Bus session) but runs independent of the desktop session's lifetime.
    subprocess.run(["systemctl", "--global", "enable", "lgtvpc-notify.service"], check=True)
    subprocess.run(
        ["systemctl", "--global", "enable", "lgtvpc-update-check.timer"], check=True
    )

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        machine = f"--machine={sudo_user}@"
        for cmd in (
            ["systemctl", machine, "--user", "daemon-reload"],
            ["systemctl", machine, "--user", "start", "lgtvpc-notify.service"],
            ["systemctl", machine, "--user", "start", "lgtvpc-update-check.timer"],
        ):
            subprocess.run(cmd, stderr=subprocess.DEVNULL)

    print()
    subprocess.run([str(INSTALL_DIR / "authorize.py")], check=True)

    offer_nic_wol()

    print()
    print("Installation complete!")


if __name__ == "__main__":
    main()
