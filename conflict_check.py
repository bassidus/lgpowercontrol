#!/usr/bin/env python3
# Pre-install gate: refuses to install next to LG_Buddy, and clears leftovers from older
# versions of this project that the current uninstaller knows nothing about.
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # a root-owned __pycache__ here would need sudo to remove
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lgpowercontrol.common import require_root  # noqa: E402

SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
USER_UNIT_DIR   = Path("/etc/systemd/user")
DISPATCHER_DIR  = Path("/etc/NetworkManager/dispatcher.d")
SLEEP_DIR       = Path("/usr/lib/systemd/system-sleep")
LOCAL_BIN_DIR   = Path("/usr/local/bin")

# Nothing the current version owns may appear in the tables below: install() tears its own
# installation down through uninstall(quiet=True), and anything listed here gets deleted
# before that runs. These names are all from releases that no longer exist.
LEGACY_SYSTEM_UNITS = [
    "lgtvpc-boot.service",                  # v3.0, the brief lgtvpc rename
    "lgtvpc-shutdown.service",
    "lgtvpc-monitor.service",
    "lgtvpc-sleep.service",
    "lgpowercontrol-resume.service",        # v1.x, replaced by the sleep hook
    "lgtv-btw-boot.service",                # pre-v1
    "lgtv-btw-shutdown.service",
    "lgtv-power-on-at-boot.service",        # pre-v1, the very first naming
    "lgtv-power-off-at-shutdown.service",
]

LEGACY_USER_UNITS = [
    "lgpowercontrol-update-check.service",  # v2.11-v3.2 self-update, dropped in v4.0
    "lgpowercontrol-update-check.timer",
    "lgtvpc-notify.service",
    "lgtvpc-update-check.service",
    "lgtvpc-update-check.timer",
]

LEGACY_UNIT_NAMES = set(LEGACY_SYSTEM_UNITS) | set(LEGACY_USER_UNITS)

LEGACY_SYSTEM_PATHS = [
    Path("/opt/lgtvpc"),  # whole tree: venv, conf, pairing db
    *(SYSTEM_UNIT_DIR / name for name in LEGACY_SYSTEM_UNITS),
    *(USER_UNIT_DIR / name for name in LEGACY_USER_UNITS),
    DISPATCHER_DIR / "90-lgtvpc",
    DISPATCHER_DIR / "pre-down.d" / "90-lgtvpc",
    DISPATCHER_DIR / "pre-down.d" / "lgpowercontrol-sleep.sh",  # v1.x
    SLEEP_DIR / "lgtvpc",
    Path("/etc/sudoers.d/lgpowercontrol-etherwake"),  # v1.x, Fedora/ether-wake only
    # copied, not symlinked, by the pre-/opt versions - so it outlives every venv deletion
    LOCAL_BIN_DIR / "bscpylgtvcommand",
    Path("/run/lgtvpc-tv-off"),
    Path("/run/lgtvpc-sleep"),
    Path("/run/lgtvpc-hook-sleep"),
    Path("/run/lgtvpc-on.lock"),
]

# Paths relative to a user's home. The pre-/opt versions installed per-user, so every real
# user on the machine is checked, not just the one running the installer.
LEGACY_USER_PATHS = [
    ".local/lgtv_control",       # pre-v1, whole trees
    ".local/lgtv-btw",
    ".local/lgpowercontrol",     # v1.x
    ".config/autostart/lgtv-btw-dbus-events.desktop",
    ".config/autostart/lgpowercontrol-dbus-events.desktop",
    ".config/autostart/lgpowercontrol-monitor.desktop",
    ".cache/lgpowercontrol-update-check",  # self-update stamps
    ".cache/lgtvpc-update-check",
]

# Same idea, but the name is generic enough that it could belong to something else entirely,
# so it only counts when the file itself mentions this project.
AMBIGUOUS_USER_PATH = ".config/autostart/listen-for-lock-unlock-events.desktop"

# Hand-enabled copies of our user units; the installers only ever wrote to /etc/systemd/user.
LEGACY_USER_UNIT_GLOBS = ["lgpowercontrol-*.service", "lgpowercontrol-*.timer",
                          "lgtvpc-*.service", "lgtvpc-*.timer"]

# Detection only - another project's files are never touched.
LG_BUDDY_PATHS = [
    Path("/usr/bin/lg-buddy"),
    Path("/usr/bin/LG_Buddy_PIP"),  # its bscpylgtv venv, oddly placed inside /usr/bin
    Path("/usr/lib/lg-buddy"),
    SYSTEM_UNIT_DIR / "LG_Buddy.service",
    SYSTEM_UNIT_DIR / "LG_Buddy_lifecycle.service",
    SYSTEM_UNIT_DIR / "LG_Buddy_wake.service",   # older LG_Buddy
    SYSTEM_UNIT_DIR / "LG_Buddy_sleep.service",
    DISPATCHER_DIR / "pre-down.d" / "LG_Buddy_lifecycle",
    DISPATCHER_DIR / "pre-down.d" / "LG_Buddy_sleep",
    SLEEP_DIR / "LG_Buddy_sleep_hook",
    Path("/etc/tmpfiles.d/lg_buddy.conf"),
    Path("/usr/share/applications/LG_Buddy_Brightness.desktop"),
]

LG_BUDDY_USER_PATHS = [
    ".config/lg-buddy/config.env",
    ".config/systemd/user/LG_Buddy_screen.service",
]


# Real login accounts with an existing home, as (home, uid) pairs.
def real_users() -> list[tuple[Path, int]]:
    users = []
    for entry in pwd.getpwall():
        home = Path(entry.pw_dir)
        if entry.pw_uid >= 1000 and entry.pw_uid != 65534 and home.is_dir():  # 65534 is nobody
            users.append((home, entry.pw_uid))
    return users


# exists() follows symlinks, so a link into an already-deleted tree would read as absent.
def present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def find_lg_buddy() -> list[Path]:
    found = [path for path in LG_BUDDY_PATHS if present(path)]
    for home, _ in real_users():
        found += [home / rel for rel in LG_BUDDY_USER_PATHS if present(home / rel)]
    return found


# systemctl disable does not reliably clear a wants-symlink whose unit file is already gone,
# so they are collected by name instead. Exact names only - a prefix match here would be one
# typo away from unlinking a unit that is still in use.
def enable_links() -> list[Path]:
    found = []
    for unit_dir in (SYSTEM_UNIT_DIR, USER_UNIT_DIR):
        found += [link for link in unit_dir.glob("*.target.wants/*") if link.name in LEGACY_UNIT_NAMES]
    for home, _ in real_users():
        user_dir = home / ".config/systemd/user"
        found += [link for link in user_dir.glob("*.target.wants/*") if link.name in LEGACY_UNIT_NAMES]
    return found


def find_legacy_leftovers() -> list[Path]:
    found = [path for path in LEGACY_SYSTEM_PATHS if present(path)]

    # Console scripts older versions symlinked onto PATH: all of lgtvpc's, and -wol/-authorize/
    # -update from v3.1/v3.2. Globs rather than literal names, to catch one forgotten here. The
    # hyphen in the second pattern spares the current /usr/local/bin/lgpowercontrol, which is
    # v4.0's own and belongs to uninstall(). is_symlink(): never touch someone else's real file.
    for pattern in ("lgtvpc*", "lgpowercontrol-*"):
        found += [link for link in LOCAL_BIN_DIR.glob(pattern) if link.is_symlink()]

    found += enable_links()

    for home, uid in real_users():
        found += [home / rel for rel in LEGACY_USER_PATHS if present(home / rel)]

        ambiguous = home / AMBIGUOUS_USER_PATH
        if ambiguous.is_file():
            try:
                text = ambiguous.read_text(errors="ignore").lower()
            except OSError:
                text = ""
            if "lgtv" in text or "lgpowercontrol" in text:
                found.append(ambiguous)

        user_unit_dir = home / ".config/systemd/user"
        for pattern in LEGACY_USER_UNIT_GLOBS:
            found += [unit for unit in user_unit_dir.glob(pattern) if unit.is_file()]

        # the notify daemon's pid stash, in $XDG_RUNTIME_DIR since v2.x and /tmp before that
        for name in ("lgpowercontrol", "lgtvpc"):
            runtime_id = Path(f"/run/user/{uid}/{name}-notify.id")
            if present(runtime_id):
                found.append(runtime_id)

    for name in ("lgpowercontrol", "lgtvpc"):
        tmp_id = Path(f"/tmp/{name}-notify.id")
        if present(tmp_id):
            found.append(tmp_id)

    return sorted(set(found))


def remove_legacy(paths: list[Path]) -> None:
    # Stops anything still running before its unit file disappears. Most of these are long
    # gone on any given machine, hence the silenced stderr.
    subprocess.run(["systemctl", "disable", "--now", *LEGACY_SYSTEM_UNITS], stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "--global", "disable", *LEGACY_USER_UNITS], stderr=subprocess.DEVNULL)

    for path in paths:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        print(f"Removed {path}")

    subprocess.run(["systemctl", "daemon-reload"])


def run_conflict_check() -> None:
    lg_buddy = find_lg_buddy()
    if lg_buddy:
        print("LG_Buddy is installed:\n")
        for path in lg_buddy:
            print(f"  {path}")
        print("\nIt controls the same TVs through the same library, and the two cannot share a\n"
              "machine: both hook the NetworkManager pre-down rail and both react to suspend, so\n"
              "the TV would receive two independent power-off sequences, and each keeps its own\n"
              "screen-state marker that the other cannot see, which makes the screen flap.")
        sys.exit("\nUninstall LG_Buddy first by running its uninstall.sh\n"
                 "(https://github.com/Staphylococcus/LG_Buddy), then run this installer again.\n\n"
                 "Installing alongside it anyway is possible with --force, but is strongly\n"
                 "discouraged: both installations keep running, and neither works reliably.")

    # No prompt: running the installer means replacing whatever came before, and leaving the
    # old units in place is not a supported state anyway - they still fire at boot and at
    # suspend, fighting the new installation for control of the TV.
    leftovers = find_legacy_leftovers()
    if leftovers:
        remove_legacy(leftovers)
        print()


if __name__ == "__main__":
    require_root()
    run_conflict_check()
