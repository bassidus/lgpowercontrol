#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # a root-owned __pycache__ here would need sudo to remove
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lgpowercontrol.common import (  # noqa: E402
    INSTALL_DIR,
    WOL,
    connection_for,
    require_root,
    wired_devices,
    wol_setting,
)


def remove_installation() -> None:
    subprocess.run(
        [
            "systemctl",
            "disable",
            "--now",
            "lgpowercontrol-boot.service",
            "lgpowercontrol-shutdown.service",
            "lgpowercontrol-monitor.service",
            "lgpowercontrol-sleep.service",
        ],
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["systemctl", "--global", "disable", "lgpowercontrol-notify.service", "lgpowercontrol-update-check.timer"],
        stderr=subprocess.DEVNULL,
    )

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        machine = f"--machine={sudo_user}@"
        subprocess.run(
            ["systemctl", machine, "--user", "stop", "lgpowercontrol-notify.service"],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", machine, "--user", "stop", "lgpowercontrol-update-check.timer"],
            stderr=subprocess.DEVNULL,
        )

    shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    for unit_dir in (Path("/etc/systemd/system"), Path("/etc/systemd/user")):
        for f in unit_dir.glob("lgpowercontrol*"):
            f.unlink()
    for f in (
        Path("/etc/NetworkManager/dispatcher.d/pre-down.d/90-lgpowercontrol"),
        Path("/etc/NetworkManager/dispatcher.d/90-lgpowercontrol"),
        Path("/usr/lib/systemd/system-sleep/lgpowercontrol"),
        Path("/usr/local/bin/lgpowercontrol"),
        Path("/usr/local/bin/lgpowercontrol-wol"),
        Path("/usr/local/bin/lgpowercontrol-authorize"),
        Path("/usr/local/bin/lgpowercontrol-update"),
    ):
        f.unlink(missing_ok=True)


def main() -> None:
    require_root()

    quiet = len(sys.argv) > 1 and sys.argv[1] == "--quiet"

    # not on --quiet (reinstall path): the user's WoL choice must survive an update
    if not quiet:
        devices = wired_devices()
        if len(devices) == 1 and WOL.is_file():
            device = devices[0]
            con = connection_for(device)
            if con and wol_setting(con) == "magic":
                try:
                    answer = input(f"Wake-on-LAN is enabled on {device}. Disable it? [y/N] ").strip().lower()
                except EOFError:
                    answer = "n"
                if answer in ("y", "yes"):
                    subprocess.run([str(WOL), "--disable"])

    remove_installation()

    subprocess.run(["systemctl", "daemon-reload"])

    if not quiet:
        print("LGPowerControl uninstalled.")


if __name__ == "__main__":
    main()
