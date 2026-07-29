#!/usr/bin/env python3
import glob
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

# Run as root from the user's own clone (e.g. ~/downloads/lgpowercontrol): a
# __pycache__/*.pyc left behind there would be root-owned and unremovable by
# the user without sudo.
sys.dont_write_bytecode = True

from lgtvpc_common import INSTALL_DIR, NIC_WOL_MARKER, require_root  # noqa: E402

LEGACY_SERVICES = [
    "lgtv-power-on-at-boot.service",
    "lgtv-power-off-at-shutdown.service",
    "lgtv-btw-boot.service",
    "lgtv-btw-shutdown.service",
    "lgpowercontrol-sleep.service",
    "lgpowercontrol-resume.service",
]


def user_home() -> str:
    try:
        return pwd.getpwnam(os.environ.get("SUDO_USER", "root")).pw_dir
    except KeyError:
        return ""


def cleanup_legacy() -> None:
    """Removes artefacts from older LGPowerControl versions."""
    home = Path(user_home())

    for svc in LEGACY_SERVICES:
        path = Path(f"/etc/systemd/system/{svc}")
        if not path.is_file():
            continue
        print(f"Removing legacy service: {svc}")
        subprocess.run(["systemctl", "disable", "--now", svc], stderr=subprocess.DEVNULL)
        path.unlink()

    legacy_files = [
        home / ".config/autostart/lgpowercontrol-dbus-events.desktop",
        home / ".config/autostart/lgpowercontrol-monitor.desktop",
        Path("/etc/sudoers.d/lgpowercontrol-etherwake"),
        Path("/usr/local/bin/bscpylgtvcommand"),
        INSTALL_DIR / "lgpowercontrol-dbus-events.sh",
    ]
    for f in legacy_files:
        if not f.is_file():
            continue
        print(f"Removing legacy file: {f}")
        f.unlink()

    for d in (home / ".local/lgtv-btw", home / ".local/lgpowercontrol"):
        if not d.is_dir():
            continue
        print(f"Removing legacy directory: {d}")
        shutil.rmtree(d)


def remove_installation(prefix: str, opt_dir: Path) -> None:
    """Disables/removes one installation's units, hooks and files.

    Parametrized on prefix/opt_dir so it can also tear down a pre-rename
    'lgpowercontrol' install left over from before the 2026-07-28 rename to
    'lgtvpc' (see main()) - not just the current installation.
    """
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
        ["systemctl", "--global", "disable", f"{prefix}-notify.service"],
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["systemctl", "--global", "disable", f"{prefix}-update-check.timer"],
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
    for f in glob.glob(f"/etc/systemd/system/{prefix}*"):
        os.remove(f)
    for f in glob.glob(f"/etc/systemd/user/{prefix}*"):
        os.remove(f)
    for f in (
        Path(f"/etc/NetworkManager/dispatcher.d/pre-down.d/90-{prefix}"),
        Path(f"/etc/NetworkManager/dispatcher.d/90-{prefix}"),
        Path(f"/usr/lib/systemd/system-sleep/{prefix}"),
    ):
        if f.exists() or f.is_symlink():
            f.unlink()


def main() -> None:
    require_root()

    quiet = len(sys.argv) > 1 and sys.argv[1] == "--quiet"

    # Revert the NIC Wake-on-LAN the installer enabled - but not on the
    # --quiet reinstall path (install.py wipes and reinstalls; the user's
    # choice must survive an update). Best effort: a failure (device gone,
    # renamed connection) shouldn't block the uninstall.
    if not quiet and NIC_WOL_MARKER.is_file():
        print("Reverting the Wake-on-LAN setting the installer enabled")
        subprocess.run([str(INSTALL_DIR / "lgtvpc-wol.py"), "--disable"])

    remove_installation("lgtvpc", INSTALL_DIR)
    cleanup_legacy()

    # One-time migration: a pre-2026-07-28 install used the 'lgpowercontrol'
    # name throughout. Harmless no-op once it's gone.
    legacy_dir = Path("/opt/lgpowercontrol")
    if legacy_dir.is_dir():
        print("Removing pre-rename installation (lgpowercontrol -> lgtvpc)")
        remove_installation("lgpowercontrol", legacy_dir)

    subprocess.run(["systemctl", "daemon-reload"])

    if not quiet:
        print("LGPowerControl uninstalled.")


if __name__ == "__main__":
    main()
