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

from lgpowercontrol.common import (  # noqa: E402
    CONF_FILE,
    INSTALL_DIR,
    LGPC,
    PAIRING_DB,
    VENV_DIR,
    confirm,
    load_conf,
    require_root,
    sole_wired_connection,
    wired_devices,
    wol_setting,
)
from lgpowercontrol.units import build_units  # noqa: E402

BIN = VENV_DIR / "bin"
DISPATCHER_D = Path("/etc/NetworkManager/dispatcher.d")
SLEEP_D = Path("/usr/lib/systemd/system-sleep")
LOCAL_BIN = Path("/usr/local/bin")
UNITS = build_units(BIN)

# (target, link) - install() creates these (some conditionally); uninstall() tears them all down.
DISPATCHER_LINK = (BIN / "lgpowercontrol-nm-dispatcher", DISPATCHER_D / "90-lgpowercontrol")
PREDOWN_LINK = ("../90-lgpowercontrol", DISPATCHER_D / "pre-down.d" / "90-lgpowercontrol")
SLEEP_HOOK_LINK = (BIN / "lgpowercontrol-sleep-hook", SLEEP_D / "lgpowercontrol")
LOCAL_BIN_LINK = (BIN / "lgpowercontrol", LOCAL_BIN / "lgpowercontrol")
LINKS = [DISPATCHER_LINK, PREDOWN_LINK, SLEEP_HOOK_LINK, LOCAL_BIN_LINK]


def copy_verbose(src: str, dst_dir: Path) -> None:
    dest = shutil.copy(src, dst_dir)
    print(f"'{src}' -> '{dest}'")

def link_verbose(target: Path | str, link: Path) -> None:
    link.unlink(missing_ok=True)
    link.symlink_to(target)
    print(f"'{target}' -> '{link}'")

def write_unit(name: str) -> None:
    target_dir, content = UNITS[name]
    path = target_dir / name
    path.write_text(content)
    print(f"Wrote {path}")

def apply_conf_values(values: dict[str, str]) -> None:
    content = CONF_FILE.read_text()
    for key, value in values.items():
        # repl as a function: a '\' in the value must not be parsed as a group reference
        content = re.sub(rf'(?m)^{key}=.*', lambda _: f'{key}="{value}"', content, count=1)
    CONF_FILE.write_text(content)


def uninstall(quiet: bool = False) -> None:
    require_root()

    # not on --quiet (reinstall path): the user's WoL choice must survive an update
    if not quiet and LGPC.is_file():
        sole = sole_wired_connection()
        if sole:
            device, con = sole
            if wol_setting(con) == "magic":
                if confirm(f"Wake-on-LAN is enabled on {device}. Disable it? [y/N] ", default=False):
                    subprocess.run([str(LGPC), "wol", "--disable"])

    subprocess.run(
        [
            "systemctl", "disable", "--now",
            "lgpowercontrol-boot.service", "lgpowercontrol-shutdown.service",
            "lgpowercontrol-monitor.service", "lgpowercontrol-sleep.service",
        ],
        stderr=subprocess.DEVNULL,
    )
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

    for name, (target_dir, _) in UNITS.items():
        (target_dir / name).unlink(missing_ok=True)

    for _, link in LINKS:
        link.unlink(missing_ok=True)

    # glob rather than a literal list: also clears -wol/-authorize/-update from an older
    # install, which would otherwise dangle, pointing into a now-deleted venv.
    for f in LOCAL_BIN.glob("lgpowercontrol*"):
        f.unlink()

    subprocess.run(["systemctl", "daemon-reload"])

    if not quiet:
        print("LGPowerControl uninstalled.")


def install() -> None:
    require_root()
    os.chdir(Path(__file__).resolve().parent)

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

    if shutil.which("apt-get"): # Debian/Ubuntu split venv out of python3; installing is a no-op if already present.
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
    keydb_path = None
    if PAIRING_DB.is_file():
        fd, keydb_path = tempfile.mkstemp()
        os.close(fd)
        shutil.copy(PAIRING_DB, keydb_path)

    uninstall(quiet=True)
    venv.create(VENV_DIR, with_pip=True)  # creates /opt/lgpowercontrol too
    pip = str(VENV_DIR / "bin" / "pip")
    subprocess.run([pip, "install", "--quiet", "."], check=True)
    for artifact in ("build", "src/lgpowercontrol.egg-info"):  # root-owned build artifacts pip leaves in the repo
        shutil.rmtree(artifact, ignore_errors=True)
    subprocess.run([pip, "uninstall", "--quiet", "-y", "pip"], check=True)  # ~15MB -> ~2MB

    if keydb_path:
        shutil.move(keydb_path, PAIRING_DB)

    copy_verbose("lgpowercontrol.conf", INSTALL_DIR)

    for name in UNITS:
        if name != "lgpowercontrol-sleep.service":  # written conditionally below instead
            write_unit(name)

    apply_conf_values({"LGTV_MAC": lgtv_mac})

    if DISPATCHER_D.is_dir():
        (DISPATCHER_D / "pre-down.d").mkdir(parents=True, exist_ok=True)
        link_verbose(*DISPATCHER_LINK)
        link_verbose(*PREDOWN_LINK)

    try:
        SLEEP_D.mkdir(parents=True, exist_ok=True)
        link_verbose(*SLEEP_HOOK_LINK)
    except OSError:  # /usr read-only (e.g. Bazzite) - fall back to the /etc listener service
        write_unit("lgpowercontrol-sleep.service")
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

    # user unit: notify needs the desktop session, so it is enabled per session, not system-wide
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
    subprocess.run([str(LGPC), "authorize"], check=True)

    # after authorize: enabling reactivates the connection, dropping network briefly
    print("\nWake-on-LAN on your computer's network card:\n\n"
          "  + Makes turning the TV off at suspend more reliable\n"
          "  + Lets other machines on your network wake this computer\n"
          "  - The network card stays powered during suspend (slightly higher power draw)\n"
          "  - Extremely rarely, stray network traffic can wake this computer unexpectedly\n\n"
          "Reversible anytime with: sudo lgpowercontrol wol --disable")

    sole = sole_wired_connection()
    if not sole:
        devices = wired_devices()
        if not devices:
            print("\nNo wired network device found - skipping the Wake-on-LAN question\n"
                  "(it is an Ethernet feature; on Wi-Fi, TV-off at suspend can occasionally miss).")
        elif len(devices) > 1:
            print("\nSeveral wired network devices found (" + ", ".join(devices) + ") - skipping the\n"
                  "Wake-on-LAN question. Enable it on the right one with:\n"
                  "  sudo lgpowercontrol wol --enable --interface <device>")
        else:
            print(f"\n{devices[0]} has no active network connection - skipping the Wake-on-LAN\n"
                  "question. Enable it later with: sudo lgpowercontrol wol --enable")
    else:
        device, con = sole
        if wol_setting(con) != "magic":
            if confirm(f"\nEnable it on {device}? [Y/n] "):
                result = subprocess.run([str(LGPC), "wol", "--enable", "--interface", device])
                if result.returncode != 0:
                    print("\033[33mEnabling Wake-on-LAN failed; TV-off at suspend keeps working via the dispatcher.\033[0m")
            else:
                print("You can enable it later with: sudo lgpowercontrol wol --enable")
        else:  # already enabled (or updates re-running) - skip the question
            print(f"\nWake-on-LAN is already enabled on {device} - no action needed.")

    print()
    print("Installation complete!")


if __name__ == "__main__":
    if "--uninstall" in sys.argv[1:]:
        uninstall(quiet="--quiet" in sys.argv[1:])
    else:
        install()
