# Wake-on-LAN control (wol) and TV pairing (authorize) - two small, independent commands
# that share nothing but a home. Note that `nmcli modify` alone does not enable WoL on the
# card - NetworkManager only pushes the setting down on reactivation, never on a plain edit.
import argparse
import os
import subprocess
import sys

from lgpowercontrol.common import (
    CONF_FILE,
    LGPC_BIN,
    PAIRING_DB,
    connection_for,
    nmcli,
    require_root,
    sole_wired_connection,
    wired_devices,
    wol_setting,
)


def set_wol(con: str, value: str) -> None:
    nmcli("connection", "modify", con, "802-3-ethernet.wake-on-lan", value, check=True)
    nmcli("connection", "down", con, check=True)  # reactivate: pushes the setting to the card now
    nmcli("connection", "up", con, check=True)


# Enables/disables Wake-on-LAN on the wired adapter, so NM skips it at suspend (race-free TV-off).
def wol(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enable or disable Wake-on-LAN on the wired adapter, so "
                     "NetworkManager can turn the TV off race-free at suspend."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Enable Wake-on-LAN (magic packet)")
    group.add_argument("--disable", action="store_true", help="Disable Wake-on-LAN (restore default)")
    group.add_argument("--status", action="store_true", help="Show the current Wake-on-LAN setting")
    parser.add_argument(
        "--interface", metavar="IFACE",
        help="Wired network device to use (e.g. eno1). Auto-detected if omitted.",
    )
    args = parser.parse_args(argv)

    if not args.status:
        require_root()

    interface = args.interface
    if interface:
        con = connection_for(interface)
        if not con:
            sys.exit(f"{interface} has no active NetworkManager connection.")
    else:
        sole = sole_wired_connection()
        if not sole:
            devices = wired_devices()
            if not devices:
                sys.exit("No wired (ethernet) network device found. Specify one with --interface.")
            if len(devices) > 1:
                sys.exit(
                    "Multiple wired network devices found: " + ", ".join(devices) +
                    "\nSpecify which one with --interface."
                )
            sys.exit(f"{devices[0]} has no active NetworkManager connection.")
        interface, con = sole

    if args.enable:
        set_wol(con, "magic")
        print(f"Wake-on-LAN enabled on {interface} ({con}).")
        print("Note: this also lets any machine on your network wake this computer with a magic packet.")
    elif args.disable:
        set_wol(con, "default")
        print(f"Wake-on-LAN disabled on {interface} ({con}).")
    else:
        state = "enabled" if wol_setting(con) == "magic" else "disabled"
        print(f"Wake-on-LAN is {state} on {interface} ({con}).")

    return 0

# STATUS both triggers the pairing dialog and validates the key. Only rc 3 (denied/unpaired)
# means the key itself is broken - rc 2 (unreachable) must NOT wipe a valid key.
def authorize(argv: list[str] | None = None) -> int:
    require_root()

    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed.")

    if not PAIRING_DB.is_file():
        print("TV Authorization - A dialog will appear on your TV screen - accept it with the remote.")

    while True:
        rc = subprocess.run([LGPC_BIN, "STATUS"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc == 0: 
            break
        if rc == 3:
            PAIRING_DB.unlink(missing_ok=True)
            print("Authorization failed or was denied on the TV.")
        else:
            print(f"Could not reach the TV (exit code {rc}). Make sure it's on and connected.")

        input("Press Enter to show a new dialog on the TV (Ctrl+C to abort): ")
        
    print("TV authorization OK!")
    return 0
