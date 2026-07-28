#!/usr/bin/env python3
"""Enables or disables Wake-on-LAN on the computer's wired network adapter.

With it enabled, NetworkManager skips the device entirely at suspend instead
of tearing it down - avoiding the pre-down race where power_off can fail with
"Network is unreachable" (see the README's "Turning off the TV during
suspend" section). `nmcli connection modify` alone only updates the saved
connection profile; NetworkManager doesn't push the setting down to the card
until the connection is reactivated - this script does both in one step.
"""
import argparse
import subprocess
import sys

from lgtvpc_common import require_root


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(result.stderr.strip() or f"Command failed: {' '.join(args)}")
    return result.stdout.strip()


def find_wired_interface() -> str:
    """Returns the computer's one wired (ethernet) network device."""
    out = run("nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status")
    devices = [line.split(":")[0] for line in out.splitlines() if line.endswith(":ethernet")]
    if not devices:
        sys.exit("No wired (ethernet) network device found. Specify one with --interface.")
    if len(devices) > 1:
        sys.exit(
            "Multiple wired network devices found: " + ", ".join(devices) +
            "\nSpecify which one with --interface."
        )
    return devices[0]


def connection_for(interface: str) -> str:
    con = run("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", interface)
    if not con or con == "--":
        sys.exit(f"{interface} has no active NetworkManager connection.")
    return con


def set_wol(con: str, value: str) -> None:
    run("nmcli", "connection", "modify", con, "802-3-ethernet.wake-on-lan", value)
    # Reactivate so NM pushes the new setting down to the card now, rather
    # than leaving it stuck in the saved profile until the next reconnect.
    run("nmcli", "connection", "down", con)
    run("nmcli", "connection", "up", con)


def main() -> None:
    require_root()

    parser = argparse.ArgumentParser(
        description="Enable or disable Wake-on-LAN on the wired adapter, so "
                     "NetworkManager can turn the TV off race-free at suspend."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Enable Wake-on-LAN (magic packet)")
    group.add_argument("--disable", action="store_true", help="Disable Wake-on-LAN (restore default)")
    parser.add_argument(
        "--interface", metavar="IFACE",
        help="Wired network device to use (e.g. eno1). Auto-detected if omitted.",
    )
    args = parser.parse_args()

    interface = args.interface or find_wired_interface()
    con = connection_for(interface)

    if args.enable:
        set_wol(con, "magic")
        print(f"Wake-on-LAN enabled on {interface} ({con}).")
        print("Note: this also lets any machine on your network wake this computer with a magic packet.")
    else:
        set_wol(con, "default")
        print(f"Wake-on-LAN disabled on {interface} ({con}).")


if __name__ == "__main__":
    main()
