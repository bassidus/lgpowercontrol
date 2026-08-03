# Enables/disables Wake-on-LAN on the wired adapter, so NM skips it at suspend (race-free
# TV-off). See CLAUDE.md for why a plain nmcli modify alone doesn't take effect.
import argparse
import subprocess
import sys

from lgpowercontrol.common import connection_for, require_root, wired_devices, wol_setting


# hard-fails, unlike lgpowercontrol.common.nmcli() - a silent no-op would be worse than an error here
def nmcli_checked(*args: str) -> None:
    result = subprocess.run(["nmcli", *args], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(result.stderr.strip() or f"Command failed: nmcli {' '.join(args)}")


def set_wol(con: str, value: str) -> None:
    nmcli_checked("connection", "modify", con, "802-3-ethernet.wake-on-lan", value)
    nmcli_checked("connection", "down", con)  # reactivate: pushes the setting to the card now
    nmcli_checked("connection", "up", con)


def main() -> None:
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
    args = parser.parse_args()

    if not args.status:
        require_root()

    interface = args.interface
    if not interface:
        devices = wired_devices()
        if not devices:
            sys.exit("No wired (ethernet) network device found. Specify one with --interface.")
        if len(devices) > 1:
            sys.exit(
                "Multiple wired network devices found: " + ", ".join(devices) +
                "\nSpecify which one with --interface."
            )
        interface = devices[0]

    con = connection_for(interface)
    if not con:
        sys.exit(f"{interface} has no active NetworkManager connection.")

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
