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

sys.dont_write_bytecode = True  # a root-owned __pycache__ here would need sudo to remove

from lgtvpc_common import (  # noqa: E402
    CONF_FILE,
    INSTALL_DIR,
    NIC_WOL_MARKER,
    PAIRING_DB,
    connection_for,
    load_conf,
    require_root,
    wired_devices,
    wol_setting,
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


def copy_verbose(src: str, dst_dir: Path) -> None:
    dest = shutil.copy(src, dst_dir)
    print(f"'{src}' -> '{dest}'")


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

    try:
        with socket.create_connection((lgtv_ip, 3001), timeout=2):
            reachable = True
    except OSError:
        reachable = False
    if not reachable:
        sys.exit(f"{lgtv_ip} is unreachable on port 3001. Make sure the TV is on. Aborting installation")

    # Debian/Ubuntu split venv out of python3; installing is a no-op if already present.
    if shutil.which("apt"):
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
            sys.exit(f"Could not detect MAC for {lgtv_ip}. Set LGTV_MAC in lgtvpc.conf")
        print(f"Detected TV MAC address: {lgtv_mac}")

    # migrate a pre-rename (<=2.13) pairing DB too, so we don't force re-pairing
    keydb_path = None
    src_db = PAIRING_DB if PAIRING_DB.is_file() else Path("/opt/lgpowercontrol") / PAIRING_DB.name
    if src_db.is_file():
        fd, keydb_path = tempfile.mkstemp()
        os.close(fd)
        shutil.copy(src_db, keydb_path)
    had_marker = NIC_WOL_MARKER.is_file()

    subprocess.run(["./uninstall.py", "--quiet"], check=True)

    venv.create(VENV_DIR, with_pip=True)  # creates /opt/lgtvpc too
    subprocess.run([f"{VENV_DIR}/bin/pip", "install", "--quiet", "bscpylgtv"], check=True)
    subprocess.run([f"{VENV_DIR}/bin/pip", "uninstall", "--quiet", "-y", "pip"], check=True)  # ~15MB -> ~2MB

    if keydb_path:
        shutil.move(keydb_path, PAIRING_DB)
    if had_marker:
        NIC_WOL_MARKER.touch()

    for f in INSTALL_FILES:
        copy_verbose(f, INSTALL_DIR)
    for f in SYSTEM_UNITS:
        copy_verbose(f, Path("/etc/systemd/system"))
    for f in USER_UNITS:
        copy_verbose(f, Path("/etc/systemd/user"))

    disp_dir = Path("/etc/NetworkManager/dispatcher.d")
    if disp_dir.is_dir():
        predown_dir = disp_dir / "pre-down.d"
        predown_dir.mkdir(parents=True, exist_ok=True)
        copy_verbose("scripts/90-lgtvpc", disp_dir)
        (disp_dir / "90-lgtvpc").chmod(0o755)

        link = predown_dir / "90-lgtvpc"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to("../90-lgtvpc")
        print(f"'../90-lgtvpc' -> '{link}'")

    sleep_dir = Path("/usr/lib/systemd/system-sleep")
    dest = sleep_dir / "lgtvpc"
    try:
        sleep_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy("scripts/sleep.py", dest)
    except OSError:  # /usr read-only (e.g. Bazzite) - fall back to the /etc listener service
        copy_verbose("systemd/lgtvpc-sleep.service", Path("/etc/systemd/system"))
        use_lstn = True
    else:
        dest.chmod(0o755)
        print(f"Installed: {dest}")
        use_lstn = False

    content = CONF_FILE.read_text()
    content = re.sub(r'(?m)^LGTV_MAC=""', f'LGTV_MAC="{lgtv_mac}"', content, count=1)
    CONF_FILE.write_text(content)

    for f in INSTALL_FILES:  # everything under scripts/ is executable, the rest isn't
        if f.startswith("scripts/"):
            (INSTALL_DIR / Path(f).name).chmod(0o755)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "lgtvpc-boot.service", "lgtvpc-shutdown.service"],
        check=True,
    )
    subprocess.run(["systemctl", "enable", "--now", "lgtvpc-monitor.service"], check=True)
    if use_lstn:
        subprocess.run(["systemctl", "enable", "--now", "lgtvpc-sleep.service"], check=True)

    # user units: notify needs the desktop session, update-check just the user D-Bus bus
    subprocess.run(["systemctl", "--global", "enable", "lgtvpc-notify.service"], check=True)
    subprocess.run(["systemctl", "--global", "enable", "lgtvpc-update-check.timer"], check=True)

    sudo_usr = os.environ.get("SUDO_USER")
    if sudo_usr:
        machine = f"--machine={sudo_usr}@"
        for cmd in (
            ["systemctl", machine, "--user", "daemon-reload"],
            ["systemctl", machine, "--user", "start", "lgtvpc-notify.service"],
            ["systemctl", machine, "--user", "start", "lgtvpc-update-check.timer"],
        ):
            subprocess.run(cmd, stderr=subprocess.DEVNULL)

    print()
    subprocess.run([str(INSTALL_DIR / "authorize.py")], check=True)

    # after authorize.py: enabling reactivates the connection, dropping network briefly
    devices = wired_devices()
    if not devices:
        print("No wired network device found - skipping the Wake-on-LAN question\n"
              "(it is an Ethernet feature; on Wi-Fi, TV-off at suspend can occasionally miss).")
    elif len(devices) > 1:
        print("Several wired network devices found (" + ", ".join(devices) + ") - skipping the\n"
              "Wake-on-LAN question. Enable it on the right one with:\n"
              "  sudo /opt/lgtvpc/lgtvpc-wol.py --enable --interface <device>")
    else:
        device = devices[0]
        con = connection_for(device)
        if not con:
            print(f"{device} has no active network connection - skipping the Wake-on-LAN\n"
                  "question. Enable it later with: sudo /opt/lgtvpc/lgtvpc-wol.py --enable")
        elif wol_setting(con) != "magic":  # already enabled (or updates re-running) - skip the question
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
                result = subprocess.run([str(INSTALL_DIR / "lgtvpc-wol.py"), "--enable", "--interface", device])
                if result.returncode == 0:
                    NIC_WOL_MARKER.touch()
                else:
                    print("\033[33mEnabling Wake-on-LAN failed; TV-off at suspend keeps working via the dispatcher.\033[0m")
            else:
                print("You can enable it later with: sudo /opt/lgtvpc/lgtvpc-wol.py --enable")

    print()
    print("Installation complete!")


if __name__ == "__main__":
    main()
