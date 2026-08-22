# The `wol`, `authorize` and `log` subcommands - small, independent commands sharing only a home.
#
# The Wake-on-LAN part is about *this computer's* network card, not the TV - that is cli.py's
# send_wol(). It configures the card so NetworkManager leaves it alone at suspend; the packet it
# enables is one another machine could send to wake this computer.
import argparse
import os
import pwd
import re
import shutil
import subprocess
import sys

from lgpowercontrol.common import (
    CONF_FILE,
    LGPC_BIN,
    PAIRING_DB,
    connection_for,
    load_conf,
    nic_wol_setting,
    nmcli,
    sole_wired_connection,
    wired_devices,
)
from lgpowercontrol.units import SYSTEM_UNIT_DIR, USER_UNIT_DIR


# The down/up is not optional: `nmcli modify` only edits the saved profile, and NetworkManager
# pushes wake-on-lan to the card on reactivation, never on a plain edit.
#
# Turning it off is "none" (no flags), never "default". Per nm-settings, "default" means "use
# global settings" and "ignore" means "disable management of Wake-on-LAN in NetworkManager" -
# both leave whatever the card already runs in place. Measured on p600s: with "default" the
# profile read back as disabled while `ethtool eno1` still said `Wake-on: g`, so NM kept skipping
# the device at suspend and the pre-down dispatcher never fired. Only "none" writes the card.
def set_nic_wol(connection: str, value: str) -> None:
    nmcli("connection", "modify", connection, "802-3-ethernet.wake-on-lan", value, check=True)
    nmcli("connection", "down", connection, check=True)
    nmcli("connection", "up", connection, check=True)


# Enabling WoL on the computer's own NIC makes NM skip the device at suspend, which sidesteps the
# pre-down race (see suspend.py). This is the documented fix when TV-off at suspend misses.
#
# No require_root() here, deliberately: modifying a system connection is polkit's decision, not a
# file permission, so asking for root ourselves would hide polkit's answer behind a worse error.
# Several distros ship a rule granting a local, active session silently (measured: Arch for wheel,
# Ubuntu for sudo/netdev); openSUSE falls back to NM's upstream auth_admin_keep, which prompts with
# a polkit agent running and fails without one. Over SSH no rule applies - the session is not local
# - so this is a desktop command by nature. nmcli(check=True) surfaces NM's own message.
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

    # Checked here rather than left to the lookups below: common.nmcli() maps its FileNotFoundError
    # to the same "" a machine with no ethernet card returns, so every path below blames the card
    # (measured on Ubuntu 22.04 with network-manager purged - enp1s0 was up with an address and
    # --status still said "No wired network device found"). After parse_args() so --help still works.
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
        # Three cases, not two: "default"/"ignore" leave the card's own setting in place, so
        # reporting them as disabled is how p600s stayed on for weeks. Anything unrecognised joins
        # them - this command only ever writes "magic" or "none", so it was set elsewhere.
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
    # branch below unlinks the key outright. Fails when the install came from a root login, which
    # is exactly when the message below is the right one. os.access is always true for root.
    if not os.access(PAIRING_DB.parent, os.W_OK):
        sys.exit(f"{PAIRING_DB.parent} is not writable by you. Run: sudo lgpowercontrol authorize")

    if not PAIRING_DB.is_file():
        print("TV Authorization - A dialog will appear on your TV screen - accept it with the remote.")

    while True:
        rc = subprocess.run(
            [LGPC_BIN, "STATUS"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        ).returncode
        if rc == 0:
            break
        if rc == 3:
            PAIRING_DB.unlink(missing_ok=True)
            print("Authorization failed or was denied on the TV.")
        else:
            print(f"Could not reach the TV (exit code {rc}). Make sure it's on and connected.")

        # EOFError: with no terminal to answer the retry, there is nothing to wait for.
        try:
            input("Press Enter to show a new dialog on the TV (Ctrl+C to abort): ")
        except EOFError:
            sys.exit("\nNo terminal to retry from - aborting.")

    print("TV authorization OK!")
    return 0


# The syslog tag every module in this package logs under - common.Logger opens it, the tag inside
# each line says which one. Not configurable, so `log` and the journalctl line in the README can
# never point at different messages.
JOURNAL_TAG = "lgpowercontrol"
LOG_LINES = 50

# Matches the value on the LOGGING line and nothing else, so the trailing comment in the shipped
# conf survives the edit. install.py's set_conf_value() rewrites the whole line instead, which is
# right there - it only writes keys the installer worked out for itself - and wrong here, where
# that comment is what tells the next reader what 1 and 0 mean.
LOGGING_VALUE = re.compile(r'(?m)^([ \t]*LOGGING=)(?:"[^"\n]*"|[^\s#]*)')


# The configured value with its padding removed, or None when the conf cannot be read at all.
# Kept apart from "is it on" because the two answers differ: anything but 1 is off, but only the
# raw text can say whether that is a deliberate 0 or a typo worth naming.
def logging_value() -> str | None:
    try:
        return load_conf(CONF_FILE).get("LOGGING", "").strip()
    except OSError:
        return None


# (unit name, whether it lives in the user's session) for the long-running services that read
# LOGGING once at startup. Which of them exists differs per install - the sleep listener is the
# immutable-OS fallback, absent wherever the sleep hook was installed instead - so the list comes
# from what is on disk rather than from the full table in units.py.
def installed_services() -> list[tuple[str, bool]]:
    services = [(f"lgpowercontrol-{name}.service", False) for name in ("monitor", "sleep")
                if (SYSTEM_UNIT_DIR / f"lgpowercontrol-{name}.service").is_file()]
    if (USER_UNIT_DIR / "lgpowercontrol-notify.service").is_file():
        services.append(("lgpowercontrol-notify.service", True))
    return services


# What polkit's refusal looks like in systemctl's stderr, whatever wording surrounds it. Matching
# on text is fragile by nature, so it only ever decides how the failure is *worded*: an unmatched
# refusal still gets reported, just in systemctl's own words.
AUTH_REFUSED = "requires interactive authentication"


def manual_restart(name: str, user_scope: bool) -> str:
    return f"  systemctl --user restart {name}" if user_scope else f"  sudo systemctl restart {name}"


# The notify service runs under the invoking user's own systemd, not root's. A plain
# `systemctl --user` from a sudo'd process reaches no user manager at all - measured on Fedora:
# "Failed to connect to user scope bus ... $XDG_RUNTIME_DIR not defined" - which would be
# reported below as a service that refused to restart rather than as the wrong question asked.
# runuser puts the call back in the user's own session; without SUDO_USER there is no session to
# aim at, and None says so.
def systemctl(*args: str, user_scope: bool):
    # --no-ask-password so a refusal stays a refusal. Restarting a system unit as a normal user is
    # polkit's decision (auth_admin_keep, measured on CachyOS), and without this systemctl hands
    # the question to whatever agent the session registered - in Plasma a password dialog on the
    # desktop, for a command the user typed in a terminal. Basse chose the sudo route instead,
    # 2026-08-22; the refusal below says so in words.
    cmd = ["systemctl", "--no-ask-password", *(["--user"] if user_scope else []), *args]
    if user_scope and os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user:
            return None
        try:
            uid = pwd.getpwnam(sudo_user).pw_uid
        except KeyError:
            return None
        cmd = ["runuser", "-u", sudo_user, "--",
               "env", f"XDG_RUNTIME_DIR=/run/user/{uid}", *cmd]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None


# Restarts the services that are actually running, so the new setting takes effect without the
# user being handed homework. is-active first, because `restart` would *start* a service that is
# deliberately stopped, and because it is what makes the report below say what really happened.
#
# Restarting the monitor resets its screen-off timer, so an escalation that was counting down
# starts over. That is acceptable for a command typed by someone sitting at the machine, and it
# is the reason nothing else in this program restarts it.
#
# Failure is normal rather than exceptional: without root, restarting a system unit needs an
# authentication this deliberately does not ask for. Every failure is reported - never swallowed,
# or the user is left believing a service picked up a setting it never saw. flag is the --enable
# or --disable that got here, so the way out can be the whole command again under sudo.
def restart_services(flag: str) -> None:
    restarted, failed = [], []
    for name, user_scope in installed_services():
        active = systemctl("is-active", "--quiet", name, user_scope=user_scope)
        if active is None or active.returncode != 0:
            continue  # not running: it reads the new value the next time it starts
        result = systemctl("restart", name, user_scope=user_scope)
        if result is not None and result.returncode == 0:
            restarted.append(name)
        else:
            failed.append((name, user_scope, result.stderr.strip() if result else ""))

    if restarted:
        print("Restarted " + ", ".join(restarted) + ".")
    if not failed:
        return

    # The refusal we asked for gets our own three words. systemctl spends three lines on it -
    # "Failed to restart <unit>: Access denied as the requested operation requires interactive
    # authentication. However, interactive authentication has not been enabled by the calling
    # program." plus a "See system logs" line - which repeats the unit name, repeats "could not
    # restart", and explains an internals decision this program made deliberately, so it reads
    # like a bug. Every other failure keeps systemctl's own first line: it is then the only thing
    # anyone knows about what went wrong.
    refused = [name for name, _, error in failed if AUTH_REFUSED in error]
    if refused:
        print("Could not restart " + ", ".join(refused) + ": authentication required.")
    for name, _, error in failed:
        if AUTH_REFUSED not in error:
            print(f"Could not restart {name}" + (f": {error.splitlines()[0]}" if error else ""))
    # One way out rather than one per unit: re-running under sudo covers the system units and the
    # user-scope one alike, the latter through runuser above. As root, sudo is not the answer -
    # something else refused - so there the per-unit command is what is left to offer.
    if os.geteuid() != 0:
        print(f"Nothing else is wrong - restarting a system service needs root. Run:\n"
              f"  sudo lgpowercontrol log {flag}")
    else:
        print("They keep the old setting until you restart them yourself:")
        for name, user_scope, _ in failed:
            print(manual_restart(name, user_scope))


def set_logging(enable: bool) -> int:
    flag = "--enable" if enable else "--disable"
    try:
        content = CONF_FILE.read_text()
    except OSError as exc:
        sys.exit(f"Could not read {CONF_FILE}: {exc}\nIs LGPowerControl installed?")

    value = "1" if enable else "0"
    # Every occurrence, not the first: load_conf lets the last definition of a key win, so a conf
    # with the line twice would read back unchanged while this said it had been turned on.
    content, replaced = LOGGING_VALUE.subn(lambda match: f'{match.group(1)}"{value}"', content)
    if not replaced:  # the key was deleted from the conf - put it back rather than do nothing
        content += ("" if content.endswith("\n") else "\n") + f'LOGGING="{value}"\n'
    try:
        # write_text truncates in place, which keeps the owner and mode install.py handed the
        # user. Never replace this with a rename-over: that takes the conf away from them.
        CONF_FILE.write_text(content)
    except OSError as exc:
        sys.exit(f"Could not write {CONF_FILE}: {exc}\nTry: sudo lgpowercontrol log {flag}")

    print(f"Logging {'enabled' if enable else 'disabled'} in {CONF_FILE}.")
    restart_services(flag)
    return 0


# The one sentence that says where logging stands. Shared by --status and by the line printed
# under the log itself, so the two can never come to word it differently.
#
# Only 1 enables, so anything else is off - and naming an unexpected value is the difference
# between answering the question and repeating it back.
def logging_summary(value: str) -> str:
    if value == "1":
        return f'Logging is on (LOGGING="1" in {CONF_FILE}).'
    if value in ("", "0"):
        return f'Logging is off (LOGGING="{value}" in {CONF_FILE}).'
    return f'Logging is off: LOGGING={value!r} in {CONF_FILE} is not "1".'


def show_logging_status() -> int:
    value = logging_value()
    if value is None:
        sys.exit(f"Could not read {CONF_FILE}.\nIs LGPowerControl installed?")
    print(logging_summary(value))
    print(f"Read it with: lgpowercontrol log [N]   # last {LOG_LINES} lines by default"
          if value == "1" else "Turn it on with: lgpowercontrol log --enable")
    return 0


# Under the log rather than over it: the question a reader has once they have read the last lines
# is whether anything is still being written, and the answer belongs where their eyes already are
# - scrolling back to the top of a screenful to find it out is the thing this avoids.
def print_logging_footer(value: str | None) -> None:
    if value is None:
        return  # no conf to report on, and the lines above are then all there is to say
    print()
    print(logging_summary(value))
    if value != "1":
        print("Nothing new is being written. Turn it on with: lgpowercontrol log --enable")


# Why an empty journal is not an answer in itself. Both causes read as "this program logs nothing",
# and both are things the user can act on - which is the whole reason this command exists rather
# than a line in the README telling people to run journalctl.
def explain_empty_log(value: str | None) -> None:
    if value not in ("1", None):
        print(f"Nothing logged: logging is off in {CONF_FILE}.\n"
              "Turn it on with `lgpowercontrol log --enable`, reproduce the problem, "
              "then look again.")
        return
    print("No log lines yet.")
    if os.geteuid() != 0:
        # Every service here runs as root (the notify one under the user's own systemd, but the
        # lines that matter at suspend and at boot are root's). Whether a plain user may read
        # those is a per-distro ACL on the journal, so this is offered, not diagnosed.
        print("The services log as root, and only members of the journal's reader group see other\n"
              "users' messages. If yours is not one of them, try: sudo lgpowercontrol log")


def show_log(lines: int, follow: bool) -> int:
    if not shutil.which("journalctl"):
        sys.exit("journalctl was not found. This command reads the systemd journal, so it is\n"
                 "unavailable here; nothing else about LGPowerControl depends on it.")
    value = logging_value()
    # -q drops the "-- No entries --" placeholder, which would otherwise count as output below.
    cmd = ["journalctl", "-q", "-t", JOURNAL_TAG, "-n", str(lines)]
    if follow:
        # Following has no end to put a footer under, so the state goes first - and it is worth
        # more here than anywhere: waiting for lines that a disabled LOGGING will never produce
        # is the one way to watch this command do nothing and conclude the program is broken.
        if value is not None and value != "1":
            print(logging_summary(value) + "\nNothing new will appear until you turn it on: "
                  "lgpowercontrol log --enable\n")
        # exec, not run: -f ends on Ctrl-C, and journalctl should own the terminal for that
        # rather than have a Python parent in between turning it into a traceback.
        try:
            os.execvp(cmd[0], [*cmd, "-f"])  # never returns
        except OSError as exc:  # only a race with the which() above can reach this
            sys.exit(f"Could not start journalctl: {exc}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        return result.returncode
    if not result.stdout.strip():
        explain_empty_log(value)
    else:
        print_logging_footer(value)
    return 0


# The journal front door. A wrapper over journalctl alone would not earn a subcommand; what does
# are the two questions journalctl cannot answer, both of which show up as an empty log - see
# explain_empty_log(). --enable/--disable are here for the same reason and no wider one: LOGGING is
# the single conf key that is toggled temporarily, while reading the log, and it is the answer this
# command gives most often. Every other setting stays where the README puts it, in the conf.
def log_cmd(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lgpowercontrol log",
        description="Show LGPowerControl's journal, or turn logging on and off.",
    )
    parser.add_argument(
        "lines", nargs="?", type=int, metavar="N",
        help=f"How many lines to show (default {LOG_LINES})",
    )
    parser.add_argument(
        "-f", "--follow", action="store_true",
        help="Keep printing new lines as they arrive (Ctrl-C to stop)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help='Turn logging on (LOGGING="1")')
    group.add_argument("--disable", action="store_true", help='Turn logging off (LOGGING="0")')
    group.add_argument("--status", action="store_true", help="Show whether logging is on")
    args = parser.parse_args(argv)

    if args.enable or args.disable or args.status:
        # Refused rather than ignored: `log 100 --disable` reads like "show me 100 lines and then
        # turn it off", and quietly doing one half of that is the worse answer.
        if args.lines is not None or args.follow:
            parser.error("--enable, --disable and --status take no other arguments")
        return show_logging_status() if args.status else set_logging(args.enable)

    return show_log(max(1, args.lines if args.lines is not None else LOG_LINES), args.follow)
