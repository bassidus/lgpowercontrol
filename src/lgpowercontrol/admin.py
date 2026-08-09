# The `wol` and `authorize` subcommands - two small, independent commands sharing only a home.
#
# Everything here is about Wake-on-LAN on *this computer's* network card, which has nothing to
# do with waking the TV. That is cli.py's send_wol(), which sends a magic packet to the TV.
# These functions configure the card so NetworkManager leaves it alone at suspend; the packet
# they enable is one some other machine could send to wake this computer.
import argparse
import os
import shutil
import subprocess
import sys

from lgpowercontrol.common import (
    CONF_FILE,
    LGPC_BIN,
    PAIRING_DB,
    connection_for,
    nic_wol_setting,
    nmcli,
    sole_wired_connection,
    wired_devices,
)


# The down/up is not optional: `nmcli modify` only edits the saved profile, and NetworkManager
# pushes wake-on-lan to the card on reactivation, never on a plain edit.
#
# Turning it off is "none" (no flags), never "default". Per nm-settings, "default" means "use
# global settings" and "ignore" means "disable management of Wake-on-LAN in NetworkManager" -
# both leave whatever the card already runs in place, so neither turns anything off. --disable
# set "default" until this was measured on p600s: the profile read back as disabled while
# `ethtool eno1` still said `Wake-on: g`, NetworkManager therefore kept skipping the device at
# suspend, and the pre-down dispatcher never fired at all. Only "none" makes NM write the card.
def set_nic_wol(connection: str, value: str) -> None:
    nmcli("connection", "modify", connection, "802-3-ethernet.wake-on-lan", value, check=True)
    nmcli("connection", "down", connection, check=True)
    nmcli("connection", "up", connection, check=True)


# Enabling WoL on the computer's own NIC makes NM skip the device at suspend entirely, which
# sidesteps the pre-down race (NM's parallel DHCP-cancel and IP-flush can finish before the
# dispatcher's power_off lands). This is the documented fix when TV-off at suspend misses.
#
# No require_root() here, deliberately: modifying a system connection is polkit's decision, not
# a file permission, so asking for root ourselves would only hide polkit's answer behind a worse
# error. Several distros ship a rule granting a local, active session silently (measured: Arch
# for wheel, Ubuntu for sudo/netdev); openSUSE ships none and falls back to NetworkManager's
# upstream auth_admin_keep, which prompts for a password when a polkit agent is running and
# fails without one. Over SSH no rule applies at all - the session is not local - so this is
# a desktop command by nature. Either way nmcli(check=True) surfaces NM's own message, which
# says more than "run this with sudo" would.
def nic_wol(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enable or disable Wake-on-LAN on the wired adapter, so "
                     "NetworkManager can turn the TV off race-free at suspend."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="Enable Wake-on-LAN (magic packet)")
    group.add_argument("--disable", action="store_true", help="Turn Wake-on-LAN off on the card")
    group.add_argument("--status", action="store_true", help="Show the current Wake-on-LAN setting")
    parser.add_argument(
        "--interface", metavar="IFACE",
        help="Wired network device to use (e.g. eno1). Auto-detected if omitted.",
    )
    args = parser.parse_args(argv)

    # Checked here rather than left to the lookups below, which cannot tell this apart from an
    # empty result: common.nmcli() maps its FileNotFoundError to the same "" that a machine with
    # no ethernet card returns, so every path below blamed the card. Measured on Ubuntu 22.04 with
    # network-manager purged - enp1s0 was up with an address and --status still answered "No wired
    # (ethernet) network device found". After parse_args(), so --help still works without nmcli.
    if not shutil.which("nmcli"):
        sys.exit("NetworkManager was not found. Wake-on-LAN on this computer's network card is\n"
                 "configured through it, so this command is unavailable on this system.\n"
                 "Turning the TV off at suspend is unavailable here too; waking it still works.")

    interface = args.interface
    if interface:
        connection = connection_for(interface)
        if not connection:
            sys.exit(f"{interface} has no active NetworkManager connection.")
    else:
        sole_wired = sole_wired_connection()
        if not sole_wired:
            devices = wired_devices()
            if not devices:
                sys.exit("No wired (ethernet) network device found. Specify one with --interface.")
            if len(devices) > 1:
                sys.exit(
                    "Multiple wired network devices found: " + ", ".join(devices) +
                    "\nSpecify which one with --interface."
                )
            sys.exit(f"{devices[0]} has no active NetworkManager connection.")
        interface, connection = sole_wired

    if args.enable:
        set_nic_wol(connection, "magic")
        print(f"Wake-on-LAN enabled on {interface} ({connection}).")
    elif args.disable:
        set_nic_wol(connection, "none")
        print(f"Wake-on-LAN disabled on {interface} ({connection}).")
    else:
        # Three cases, not two. A profile left on "default" or "ignore" was reported as disabled
        # here, which is how the card on p600s stayed on for weeks while both this command and the
        # profile agreed it was off. Anything unrecognised joins them: some flag combination is
        # set, and this command only ever writes "magic" or "none", so it was set elsewhere.
        setting = nic_wol_setting(connection)
        if setting == "magic":
            print(f"Wake-on-LAN is enabled on {interface} ({connection}).")
        elif not setting:
            print(f"Wake-on-LAN is disabled on {interface} ({connection}).")
        else:
            print(f"Wake-on-LAN on {interface} ({connection}) is left to the card's own setting\n"
                  f"(NetworkManager profile: {setting}), so it may still be on. Check it with:\n"
                  f"  sudo ethtool {interface} | grep Wake-on\n"
                  "Turn it off with: lgpowercontrol wol --disable")

    return 0

# STATUS both triggers the pairing dialog and validates the key. Only rc 3 (denied/unpaired)
# means the key itself is broken - rc 2 (unreachable) must NOT wipe a valid key.
def authorize(argv: list[str] | None = None) -> int:
    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed.")

    # Directory, not just the key file: sqlite writes a -journal alongside the db, and the rc 3
    # branch below unlinks the key outright. The installer hands both to the user who ran it, so
    # this normally passes without sudo; it fails when the install came from a root login, which
    # is exactly when the message below is the right one. os.access is always true for root.
    if not os.access(PAIRING_DB.parent, os.W_OK):
        sys.exit(f"{PAIRING_DB.parent} is not writable by you. Run: sudo lgpowercontrol authorize")

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

        # EOFError: this loop only became reachable once the wrappers stopped discarding the exit
        # code, and with no terminal to answer the retry there is nothing to wait for.
        try:
            input("Press Enter to show a new dialog on the TV (Ctrl+C to abort): ")
        except EOFError:
            sys.exit("\nNo terminal to retry from - aborting.")

    print("TV authorization OK!")
    return 0
