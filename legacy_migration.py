# Everything needed to carry an install made under an older name forward, or tear it down.
# Two schemes are covered: the pre-Python (<= 2.13) bash layout, and the v3.0 'lgtvpc'
# naming that shipped briefly before being reverted (see CLAUDE.md sections 7 and 8).
# Nothing here serves a current install, so the whole file can be deleted once the old
# ones have died out - along with its handful of call sites in install.py/uninstall.py.
#
# Imported by install.py and uninstall.py after they put the repo's src/ on sys.path.
import os
import pwd
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from lgpowercontrol.common import NIC_WOL_MARKER, PAIRING_DB, load_conf

# v3.0 installed here before the name was reverted
V3_DIR = Path("/opt/lgtvpc")

# unit names from schemes older than the Python rewrite
SERVICES = [
    "lgtv-power-on-at-boot.service",
    "lgtv-power-off-at-shutdown.service",
    "lgtv-btw-boot.service",
    "lgtv-btw-shutdown.service",
]


# Settings from a v3.0 install, or None if there is none. Its update.py copies its own
# conf into the extracted tree; a fresh clone has none, but /opt/lgtvpc still holds one.
def conf_values() -> dict[str, str] | None:
    for candidate in (Path("lgtvpc.conf"), V3_DIR / "lgtvpc.conf"):
        if candidate.is_file():
            print(f"Carrying over settings from {candidate} (v3.0 'lgtvpc' install)")
            return load_conf(candidate)
    return None


# A v3.0 pairing key, so migrating users are not forced to re-pair with the TV.
def pairing_db() -> Path | None:
    db = V3_DIR / PAIRING_DB.name
    return db if db.is_file() else None


# Whether a v3.0 install had the installer's NIC Wake-on-LAN setting enabled.
def nic_wol_enabled() -> bool:
    return (V3_DIR / NIC_WOL_MARKER.name).is_file()


# Undoes the NIC Wake-on-LAN setting a v3.0 install enabled, using its own wol command.
def revert_nic_wol() -> None:
    wol_bin = V3_DIR / "bscpylgtv" / "bin" / "lgtvpc-wol"
    if (V3_DIR / NIC_WOL_MARKER.name).is_file() and wol_bin.is_file():
        print("Reverting the Wake-on-LAN setting the installer enabled")
        subprocess.run([str(wol_bin), "--disable"])


# Tears down every older install. remove_installation is passed in rather than owned here:
# uninstall.py knows how to remove an installation, this module only knows which ones are old.
def remove(remove_installation: Callable[[str, Path], None]) -> None:
    try:
        home = Path(pwd.getpwnam(os.environ.get("SUDO_USER", "root")).pw_dir)
    except KeyError:
        home = None

    for svc in SERVICES:
        path = Path(f"/etc/systemd/system/{svc}")
        if not path.is_file():
            continue
        print(f"Removing legacy service: {svc}")
        subprocess.run(["systemctl", "disable", "--now", svc], stderr=subprocess.DEVNULL)
        path.unlink()

    legacy_files = [
        Path("/etc/sudoers.d/lgpowercontrol-etherwake"),
        Path("/usr/local/bin/bscpylgtvcommand"),
    ]
    legacy_dirs = []
    if home:
        legacy_files += [
            home / ".config/autostart/lgpowercontrol-dbus-events.desktop",
            home / ".config/autostart/lgpowercontrol-monitor.desktop",
        ]
        legacy_dirs = [home / ".local/lgtv-btw", home / ".local/lgpowercontrol"]
    for f in legacy_files:
        if not f.is_file():
            continue
        print(f"Removing legacy file: {f}")
        f.unlink()

    for d in legacy_dirs:
        if not d.is_dir():
            continue
        print(f"Removing legacy directory: {d}")
        shutil.rmtree(d)

    if V3_DIR.is_dir():  # one-time migration, harmless no-op once it's gone
        print("Removing v3.0 installation (lgtvpc -> lgpowercontrol)")
        remove_installation("lgtvpc", V3_DIR)
