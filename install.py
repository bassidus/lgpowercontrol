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
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import legacy_migration  # noqa: E402
from lgpowercontrol.common import (  # noqa: E402
    CONF_FILE,
    INSTALL_DIR,
    NIC_WOL_MARKER,
    PAIRING_DB,
    VENV_DIR,
    WOL,
    connection_for,
    load_conf,
    require_root,
    wired_devices,
    wol_setting,
)

INSTALL_FILES = [
    "VERSION",
    "lgpowercontrol.conf",
]
SYSTEM_UNITS = [
    "systemd/lgpowercontrol-shutdown.service",
    "systemd/lgpowercontrol-boot.service",
    "systemd/lgpowercontrol-monitor.service",
]
USER_UNITS = [
    "systemd/lgpowercontrol-notify.service",
    "systemd/lgpowercontrol-update-check.service",
    "systemd/lgpowercontrol-update-check.timer",
]

SHORTCUTS = ["lgpowercontrol", "lgpowercontrol-wol", "lgpowercontrol-authorize", "lgpowercontrol-update"]


def copy_verbose(src: str, dst_dir: Path) -> None:
    dest = shutil.copy(src, dst_dir)
    print(f"'{src}' -> '{dest}'")


def link_verbose(target: Path | str, link: Path) -> None:
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    print(f"'{target}' -> '{link}'")


def apply_conf_values(values: dict[str, str]) -> None:
    content = CONF_FILE.read_text()
    for key, value in values.items():
        # repl as a function: a '\' in the value must not be parsed as a group reference
        content = re.sub(rf'(?m)^{key}=.*', lambda _: f'{key}="{value}"', content, count=1)
    CONF_FILE.write_text(content)


def main() -> None:
    require_root()
    os.chdir(Path(__file__).resolve().parent)

    conf = load_conf("lgpowercontrol.conf")
    carried = legacy_migration.conf_values() if not conf.get("LGTV_IP") else None
    if carried:
        conf = carried

    lgtv_ip = conf.get("LGTV_IP", "")
    if not lgtv_ip:
        sys.exit(
            "LGTV_IP is not set. Edit lgpowercontrol.conf and enter your TV's IP address,\n"
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
            sys.exit(f"Could not detect MAC for {lgtv_ip}. Set LGTV_MAC in lgpowercontrol.conf")
        print(f"Detected TV MAC address: {lgtv_mac}")
        conf["LGTV_MAC"] = lgtv_mac

    # carried across the reinstall below, which wipes the install directory
    keydb_path = None
    src_db = PAIRING_DB if PAIRING_DB.is_file() else legacy_migration.pairing_db()
    if src_db:
        fd, keydb_path = tempfile.mkstemp()
        os.close(fd)
        shutil.copy(src_db, keydb_path)
    had_marker = NIC_WOL_MARKER.is_file() or legacy_migration.nic_wol_enabled()

    subprocess.run(["./uninstall.py", "--quiet"], check=True)

    venv.create(VENV_DIR, with_pip=True)  # creates /opt/lgpowercontrol too
    subprocess.run([f"{VENV_DIR}/bin/pip", "install", "--quiet", "."], check=True)
    for artifact in ("build", "src/lgpowercontrol.egg-info"):  # root-owned build artifacts pip leaves in the repo
        shutil.rmtree(artifact, ignore_errors=True)
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

    if carried:
        apply_conf_values(conf)
    else:
        apply_conf_values({"LGTV_MAC": lgtv_mac})

    disp_dir = Path("/etc/NetworkManager/dispatcher.d")
    if disp_dir.is_dir():
        predown_dir = disp_dir / "pre-down.d"
        predown_dir.mkdir(parents=True, exist_ok=True)

        link_verbose(VENV_DIR / "bin" / "lgpowercontrol-nm-dispatcher", disp_dir / "90-lgpowercontrol")
        link_verbose("../90-lgpowercontrol", predown_dir / "90-lgpowercontrol")

    sleep_dir = Path("/usr/lib/systemd/system-sleep")
    try:
        sleep_dir.mkdir(parents=True, exist_ok=True)
        link_verbose(VENV_DIR / "bin" / "lgpowercontrol-sleep-hook", sleep_dir / "lgpowercontrol")
    except OSError:  # /usr read-only (e.g. Bazzite) - fall back to the /etc listener service
        copy_verbose("systemd/lgpowercontrol-sleep.service", Path("/etc/systemd/system"))
        use_listener = True
    else:
        use_listener = False

    for name in SHORTCUTS: # The commands a user is expected to type by hand; symlinked onto PATH for convenience.
        link_verbose(VENV_DIR / "bin" / name, Path("/usr/local/bin") / name)

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "lgpowercontrol-boot.service", "lgpowercontrol-shutdown.service"],
        check=True,
    )
    subprocess.run(["systemctl", "enable", "--now", "lgpowercontrol-monitor.service"], check=True)
    if use_listener:
        subprocess.run(["systemctl", "enable", "--now", "lgpowercontrol-sleep.service"], check=True)

    # user units: notify needs the desktop session, update-check just the user D-Bus bus
    subprocess.run(["systemctl", "--global", "enable", "lgpowercontrol-notify.service"], check=True)
    subprocess.run(["systemctl", "--global", "enable", "lgpowercontrol-update-check.timer"], check=True)

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        machine = f"--machine={sudo_user}@"
        for cmd in (
            ["systemctl", machine, "--user", "daemon-reload"],
            ["systemctl", machine, "--user", "start", "lgpowercontrol-notify.service"],
            ["systemctl", machine, "--user", "start", "lgpowercontrol-update-check.timer"],
        ):
            subprocess.run(cmd, stderr=subprocess.DEVNULL)

    print()
    subprocess.run([str(VENV_DIR / "bin" / "lgpowercontrol-authorize")], check=True)

    # after authorize: enabling reactivates the connection, dropping network briefly
    devices = wired_devices()
    if not devices:
        print("No wired network device found - skipping the Wake-on-LAN question\n"
              "(it is an Ethernet feature; on Wi-Fi, TV-off at suspend can occasionally miss).")
    elif len(devices) > 1:
        print("Several wired network devices found (" + ", ".join(devices) + ") - skipping the\n"
              "Wake-on-LAN question. Enable it on the right one with:\n"
              "  sudo lgpowercontrol-wol --enable --interface <device>")
    else:
        device = devices[0]
        con = connection_for(device)
        if not con:
            print(f"{device} has no active network connection - skipping the Wake-on-LAN\n"
                  "question. Enable it later with: sudo lgpowercontrol-wol --enable")
        elif wol_setting(con) != "magic":  # already enabled (or updates re-running) - skip the question
            print(f"""
Enable Wake-on-LAN on your computer's network card ({device})?

  + Makes turning the TV off at suspend fully reliable (avoids a known race)
  + Lets other machines on your network wake this computer
  - The network card stays powered during suspend (slightly higher power draw)
  - Rarely, stray network traffic can wake the computer unexpectedly

Reversible anytime with: sudo lgpowercontrol-wol --disable""")
            try:
                answer = input("Enable it? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes"):
                result = subprocess.run([str(WOL), "--enable", "--interface", device])
                if result.returncode == 0:
                    NIC_WOL_MARKER.touch()
                else:
                    print("\033[33mEnabling Wake-on-LAN failed; TV-off at suspend keeps working via the dispatcher.\033[0m")
            else:
                print("You can enable it later with: sudo lgpowercontrol-wol --enable")

    print()
    print("Installation complete!")


if __name__ == "__main__":
    main()
