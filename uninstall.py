#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # a root-owned __pycache__ here would need sudo to remove
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import legacy_migration  # noqa: E402
from lgpowercontrol.common import INSTALL_DIR, NIC_WOL_MARKER, WOL, require_root  # noqa: E402


# prefix/opt_dir parametrized so legacy_migration can reuse it for installs made under an older name.
def remove_installation(prefix: str, opt_dir: Path) -> None:
    subprocess.run(
        [
            "systemctl",
            "disable",
            "--now",
            f"{prefix}-boot.service",
            f"{prefix}-shutdown.service",
            f"{prefix}-monitor.service",
            f"{prefix}-sleep.service",
        ],
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["systemctl", "--global", "disable", f"{prefix}-notify.service", f"{prefix}-update-check.timer"],
        stderr=subprocess.DEVNULL,
    )

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        machine = f"--machine={sudo_user}@"
        subprocess.run(
            ["systemctl", machine, "--user", "stop", f"{prefix}-notify.service"],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["systemctl", machine, "--user", "stop", f"{prefix}-update-check.timer"],
            stderr=subprocess.DEVNULL,
        )

    shutil.rmtree(opt_dir, ignore_errors=True)
    for unit_dir in (Path("/etc/systemd/system"), Path("/etc/systemd/user")):
        for f in unit_dir.glob(f"{prefix}*"):
            f.unlink()
    for f in (
        Path(f"/etc/NetworkManager/dispatcher.d/pre-down.d/90-{prefix}"),
        Path(f"/etc/NetworkManager/dispatcher.d/90-{prefix}"),
        Path(f"/usr/lib/systemd/system-sleep/{prefix}"),
        Path(f"/usr/local/bin/{prefix}"),
        Path(f"/usr/local/bin/{prefix}-wol"),
        Path(f"/usr/local/bin/{prefix}-authorize"),
        Path(f"/usr/local/bin/{prefix}-update"),
    ):
        f.unlink(missing_ok=True)


def main() -> None:
    require_root()

    quiet = len(sys.argv) > 1 and sys.argv[1] == "--quiet"

    # not on --quiet (reinstall path): the user's WoL choice must survive an update
    if not quiet:
        if NIC_WOL_MARKER.is_file() and WOL.is_file():
            print("Reverting the Wake-on-LAN setting the installer enabled")
            subprocess.run([str(WOL), "--disable"])
        legacy_migration.revert_nic_wol()

    remove_installation("lgpowercontrol", INSTALL_DIR)
    legacy_migration.remove(remove_installation)

    subprocess.run(["systemctl", "daemon-reload"])

    if not quiet:
        print("LGPowerControl uninstalled.")


if __name__ == "__main__":
    main()
