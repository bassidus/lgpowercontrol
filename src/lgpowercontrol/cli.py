# ON | OFF | SCREEN_OFF | STATUS. Exit: 0 ok, 1 error, 2 unreachable, 3 unpaired.
import argparse
import asyncio
import contextlib
import fcntl
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import websockets.exceptions
from bscpylgtv import WebOsClient
from bscpylgtv.exceptions import (
    PyLGTVCmdError,
    PyLGTVCmdException,
    PyLGTVPairException,
    PyLGTVServiceNotFoundError,
)

from lgpowercontrol.common import CONF_FILE, PAIRING_DB, TV_OFF_FLAG, Logger, load_conf

SOURCE = os.environ.get("LGPC_SOURCE", "")  # who invoked this, for log lines
log = Logger(SOURCE or "cli")

ON_LOCK = Path("/run/lgpowercontrol-on.lock")

# The commands that are not TV commands: name -> the line --help lists it under. main()'s dispatch
# reads the same table, so a command cannot be added without turning up in the help - which is how
# this drifted in the first place, with argparse only ever knowing about the four below it.
SUBCOMMANDS = {
    "authorize": "pair with the TV (a dialog appears on the screen)",
    "wol":       "Wake-on-LAN on this computer's wired adapter: --status, --enable, --disable",
    "log":       "show the journal: log [N], -f to follow, --enable/--disable to turn it on or off",
    "update":    "update to the latest release (needs root)",
    "uninstall": "remove the installation, its services and the TV pairing (needs root)",
}

CONF = {}
RETRIES = 3

# Wake budget, with the 1s sleep in the loop below. What has to fit: the network coming back
# after resume, plus the TV itself - about 4s from Always Ready, 5s from deep standby, 10s
# without it once a packet lands, and a lost packet costs a whole resend cycle on top.
# Never shrink either number, nor the interval; test_wake.BudgetTest pins both and says why.
WAKE_ATTEMPTS = 15

# Separate budget: the wake loop has already proven the TV awake and responding, so this only
# covers webOS finishing the input switch.
SET_INPUT_ATTEMPTS = 5

# The only states that mean the TV is awake. Everything else - "Active Standby" from power_off,
# "Suspend" from deep standby, or a state we have never seen - means it is not.
AWAKE_STATES = ("Active", "Screen Off", "Screen Saver")

# asyncio.TimeoutError is listed only for 3.10 - from 3.11 it is OSError. Anything not in here
# is logged as an internal error, so a bug in this program never reads as network trouble.
NETWORK_ERRORS = (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException)


# Wakes the TV; unrelated to admin.py's `wol` subcommand. Sent twice because each covers what
# the other misses: broadcast for on-subnet, where a sleeping TV won't ARP-reply, unicast for
# cross-VLAN (#12), where webOS does answer ARP in standby.
def send_wol() -> None:
    try:
        mac = bytes.fromhex(CONF.get("LGTV_MAC", "").replace(":", "").replace("-", ""))
    except ValueError:
        return
    if len(mac) != 6:
        return
    packet = b"\xff" * 6 + mac * 16
    targets = [(("255.255.255.255", 9), True)]
    if CONF.get("LGTV_IP"):  # an empty host would go to 0.0.0.0 rather than being skipped
        targets.append(((CONF["LGTV_IP"], 9), False))
    for dest, broadcast in targets:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                if broadcast:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(packet, dest)
        except OSError:
            pass  # transient (ENETUNREACH mid-resume); the wake loop resends every second


# Returns (rc, result, err). rc 102 = turn_screen_on refused with -102, ambiguous
# by design (screen already on vs TV asleep) - caller checks get_power_state.
#
# quiet=True keeps the failure out of the journal here so the caller can put the same text on its
# own line instead. It is for the retry loops below, which log one line per attempt anyway: a
# resume with no network wrote fifteen "get_power_state: unreachable ..." lines interleaved with
# fifteen "get_power_state failed (attempt n/15)" lines, saying one thing in thirty. A caller that
# passes quiet must log err itself - dropping it would hide why a command failed.
def tv_cmd(command: str, *args, retries: int | None = None,
           quiet: bool = False) -> tuple[int, Any, str]:
    if retries is None:
        retries = RETRIES
    try:
        async def _call():
            client = await WebOsClient.create(
                ip=CONF.get("LGTV_IP", ""),
                key_file_path=str(PAIRING_DB),
                connect_retry_attempts=retries,
                ping_interval=None,
                states=None,
            )
            await client.connect()
            try:
                return await getattr(client, command)(*args)
            finally:
                await client.disconnect()

        with contextlib.redirect_stdout(sys.stderr):  # library logs retries to stdout
            return 0, asyncio.run(_call()), ""
    # Subclasses PyLGTVCmdError but is raised with a plain string, not the response dict, so it
    # must be caught first - the payload lookup below would TypeError, and a TypeError raised
    # inside an except block escapes the whole try, uncatchable by the handlers under it.
    except PyLGTVServiceNotFoundError as exc:
        err = str(exc.args[0])  # endpoint missing on this webOS version
        rc = 1
    except PyLGTVCmdError as exc:
        payload = exc.args[0]["payload"]  # raised with the response dict, see webos_client.py
        code, text = str(payload.get("errorCode", "")), str(payload.get("errorText", ""))
        if command == "turn_screen_on" and code == "-102":
            return 102, None, ""
        err = f"{code} {text}".strip() or str(getattr(exc, "message", exc))
        rc = 1
    except PyLGTVPairException as exc:
        err = f"not paired: {getattr(exc, 'message', exc)}"
        rc = 3
    except PyLGTVCmdException as exc:
        err = str(getattr(exc, "message", exc))
        rc = 1
    except NETWORK_ERRORS as exc:
        err = f"unreachable: {type(exc).__name__}: {exc}"
        rc = 2
    # A TV that hangs up mid-command leaves request() waiting on a future bscpylgtv then cancels.
    # CancelledError subclasses BaseException, so it slips past the catch-all below and out of
    # main() as a traceback - it has to be named explicitly to be caught at all. No user interrupt
    # is swallowed here: asyncio.run turns Ctrl-C into KeyboardInterrupt, never into this.
    except asyncio.CancelledError:
        err = "unreachable: TV closed the connection mid-command"
        rc = 2
    except Exception as exc:  # noqa: BLE001 - deliberate: a bug here becomes rc 1, not a traceback
        err = f"internal error: {type(exc).__name__}: {exc}"
        rc = 1
    if not quiet:
        log(f"{command}: {err}")
    return rc, None, err


# The HDMI input this computer is on, as a webOS app id ("2" -> com.webos.app.hdmi2), when the TV
# is shared with another device - otherwise None, and every shared-TV behavior stands down.
#
# SHARED_TV is on only when it says 1, the opposite reading disabled_off_event() gives its keys.
# A missing key is one the user deleted, and the safe reading of it is the one that leaves the TV
# turning off; anything else than 1 or 0 reads as 0 for the same reason. HDMI_INPUT supplies the
# number, so the two can no longer disagree; anything but a plain input number of 1 or higher
# reads as "not configured", zero included, because a typo would otherwise disable power-off
# silently and for good. load_conf keeps whitespace, hence strip.
def shared_tv_app_id() -> str | None:
    shared = CONF.get("SHARED_TV", "").strip()
    if shared != "1":
        if shared not in ("", "0"):
            log(f"SHARED_TV={shared!r} is not 1 or 0 - reading it as 0")
        return None
    hdmi = CONF.get("HDMI_INPUT", "").strip()
    if not hdmi.isdigit() or int(hdmi) < 1:
        log(f"SHARED_TV is set but HDMI_INPUT={hdmi!r} is not an input number - standing down")
        return None
    return f"com.webos.app.hdmi{hdmi}"


# The automatic off events that can be switched off one by one, and the conf key that does it.
# A hand-typed `lgpowercontrol OFF` carries no source and is never gated - typing the command is
# the request itself. The idle escalation (dpms-monitor) is absent on purpose: keeping the TV off
# a static image is what this program exists for, and SHARED_TV covers a shared TV.
OFF_EVENT_KEYS = {
    "shutdown":       "POWER_OFF_AT_SHUTDOWN",
    "nm-dispatcher":  "POWER_OFF_AT_SUSPEND",
    "sleep-hook":     "POWER_OFF_AT_SUSPEND",
    "sleep-listener": "POWER_OFF_AT_SUSPEND",
}


# The conf key that switched this off event off, or None to proceed. Only an explicit 0 disables,
# the same reading OFF_WARNING_SECONDS gets: a typo and a missing key both leave today's behavior
# in place. That fails towards a TV that turns off when the user wanted it left on, rather than
# one that silently never turns off again.
def disabled_off_event() -> str | None:
    key = OFF_EVENT_KEYS.get(SOURCE, "")
    return key if key and CONF.get(key, "").strip() == "0" else None


# Returns None to proceed with the off command, or an exit code to return immediately.
def check_power_off_guard() -> int | None:
    target_app = shared_tv_app_id()
    if target_app is None:
        return None  # no guard configured

    rc, result, _ = tv_cmd("get_current_app", retries=1)

    if rc == 2:
        log("TV unreachable, skipping off command")
        return 2  # propagate so monitor.py still logs it

    if rc != 0:
        log("Cannot check current app, proceeding with off command")
        return None  # fail-open on non-network errors

    if result == target_app:
        return None  # on the right input, proceed

    # Measured on a C3 (journal, 2026-08-12): a TV that has gone to standby on its own still
    # answers getForegroundAppInfo, with an empty appId - which used to print as "TV on , not
    # on com.webos.app.hdmi1" and read like a bug. Skipping stays the right outcome: nothing in
    # the foreground means the TV is already down, so a power_off would be a no-op at best and
    # one more error line at worst. bscpylgtv returns res.get("appId"), so None is the same case.
    if not result:
        log("TV reports no foreground app - it is already in standby, skipping off command")
        return 0

    log(f"TV on {result}, not on {target_app} - skipping off command")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].lower() in SUBCOMMANDS:
        # lazy imports: the ON/OFF path is suspend-critical and must not pay for what these pull
        # in. Bound to `admin` and not to `log`, which is this module's Logger - a local of that
        # name here would shadow it for the whole of main(), silently.
        from lgpowercontrol import admin, uninstall, update
        handler = {"wol": admin.nic_wol, "authorize": admin.authorize, "log": admin.log_cmd,
                   "update": update.main, "uninstall": uninstall.main}[sys.argv[1].lower()]
        return handler(sys.argv[2:])

    global RETRIES, CONF

    parser = argparse.ArgumentParser(
        prog="lgpowercontrol",
        description="Control an LG TV over the network.",
        epilog="other commands, each with a --help of its own:\n"
               + "\n".join(f"  {name:<11}{text}" for name, text in SUBCOMMANDS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # The sleep hook passes 1 so a dead network cannot hold up suspend; the wake loop's own
    # probes always pass 1 regardless, since retrying there is the loop's job.
    parser.add_argument(
        "--retries", type=int, default=RETRIES, metavar="N",
        help="TV connect attempts per command (default 3)",
    )
    # str.upper runs before the choices check, so the commands can be typed in any case.
    parser.add_argument(
        "command", type=str.upper, choices=("ON", "OFF", "SCREEN_OFF", "STATUS"),
        help="the TV command to run",
    )
    # Typed on its own, the program prints its whole help rather than the usage line argparse
    # gives a missing argument: the four commands above are half of what it can do, and the
    # epilog is the other half. Still exit 2 - nothing was carried out.
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        return 2

    args = parser.parse_args()

    RETRIES = max(1, args.retries)
    CONF = load_conf(CONF_FILE)

    if args.command == "ON":
        # At resume the watcher and the dispatcher both fire ON; this flock drops the loser.
        # lock_file must stay bound for the whole ON branch - closing it releases the flock and
        # silently kills the dedupe. Never wrap it in `with` or move it into a helper. 0600 so no
        # other local user can hold the lock; /run is root-only, so a hand-typed ON runs unlocked.
        try:
            lock_file = os.fdopen(os.open(ON_LOCK, os.O_WRONLY | os.O_CREAT, 0o600), "w")
        except OSError:
            lock_file = None
        if lock_file is not None:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0  # concurrent ON already running - dedupe

        if not SOURCE:
            log("Turning TV on")
        with contextlib.suppress(OSError):  # not root: no flag to clear
            TV_OFF_FLAG.unlink(missing_ok=True)

        try:
            if subprocess.run(["nm-online", "-q", "-t", "15"], check=False).returncode != 0:
                log("Network still down after 15s; trying anyway")
        except OSError:
            pass  # no NetworkManager - nothing to wait for

        send_wol()

        # Waits for the network to come back and the TV to wake; see WAKE_ATTEMPTS.
        state = ""
        rc = 1  # non-zero until an attempt succeeds; also what "gave up" returns
        # Set by every branch below that means the TV was down when we got here, and therefore
        # that our own WoL is what brought it up. Read once, at the input switch at the end.
        # Both branches that set it are deliberately hard to trigger on a TV that was never
        # asleep: a wrong reading here takes the picture off whoever is watching the other
        # source, which is the one thing the shared-TV setting exists to prevent.
        woke_from_standby = False
        consecutive_unreachable = 0
        for attempt in range(1, WAKE_ATTEMPTS + 1):
            time.sleep(1)  # also avoids a "No Signal" flash before the source is ready
            rc = 1
            progress = f"{attempt}/{WAKE_ATTEMPTS}"

            state_rc, result, err = tv_cmd("get_power_state", retries=1, quiet=True)
            if state_rc != 0:
                # Two in a row before this counts as a TV that was down. One miss is the network,
                # not the TV: at boot this runs seconds after the link came up, and a single
                # failure used to latch the flag for the rest of the loop - so a TV that answered
                # "Active" a second later still had its input taken. A sleeping TV misses many
                # polls in a row and trips this on the second one.
                consecutive_unreachable += 1
                if consecutive_unreachable > 1:
                    woke_from_standby = True
                log(f"get_power_state failed (attempt {progress}): {err}")
                send_wol()
                continue
            consecutive_unreachable = 0
            state = result.get("state", "")
            processing = result.get("processing", "")

            # Mid-transition: the state value can't be trusted to say which standby the TV is
            # leaving, so wait for a plain state rather than act on this one.
            if processing:
                # Ours only when the TV is leaving a state it was asleep in. "We sent a packet a
                # moment ago, so this transition is ours" was too generous: ON sends the packet
                # before the first poll every time, including to a TV that is already on, and a
                # transition reported by an awake TV then claimed the input from it.
                if state not in AWAKE_STATES:
                    woke_from_standby = True
                log(f"TV mid-transition: {state} ({processing}) - waiting (attempt {progress})")
                continue

            # Anything outside AWAKE_STATES means the magic packet never landed - resending is
            # the safe treatment for a standby state and for an unknown one alike.
            if state in AWAKE_STATES:
                rc, _, err = tv_cmd("turn_screen_on", retries=1, quiet=True)
                if rc == 102:  # -102 is ambiguous; the state above is what proves the TV awake
                    rc = 0
                if rc == 0:
                    log(f"TV awake ({state}), screen turned on (attempt {progress})")
                    break
                log(f"turn_screen_on failed with TV awake ({state}) (attempt {progress}): {err}")
            else:
                woke_from_standby = True
                log(f"TV in standby ({state or '?'}) - resending WoL (attempt {progress})")
                send_wol()

        if rc != 0:
            log(f"Giving up - TV unreachable (last state: {state or 'unknown'})")
            return rc

        if not CONF.get("HDMI_INPUT"):
            return 0

        # Counterpart to the off guard: on a shared TV, switching inputs would yank the picture
        # off whoever is watching the other source. woke_from_standby settles which side owns it
        # at no extra round-trip - a TV we found asleep is one our own WoL woke, so it is ours.
        # Don't compare get_current_app instead: webOS restores the other source at the next wake
        # after the remote turned the TV off there, so that would skip the switch and leave us on
        # No Signal. Checked after HDMI_INPUT to keep the line out of a journal without it.
        #
        # The state is named because two very different situations reach this line and only one is
        # worth reading. "Screen Off" is our own SCREEN_OFF coming back a second later - the TV
        # never left AWAKE_STATES, so it was never ours to claim - while "Active" is the one that
        # means someone else is watching. The earlier wording said "TV was already on", which read
        # like a false claim right under a line saying the screen had just been turned on.
        if shared_tv_app_id() is not None and not woke_from_standby:
            log(f"TV was not in standby ({state}) - leaving its input alone (SHARED_TV)")
            return 0

        hdmi = f"HDMI_{CONF['HDMI_INPUT']}"
        log(f"Setting input to {hdmi}")  # the app layer can lag a wake from deep standby
        for attempt in range(1, SET_INPUT_ATTEMPTS + 1):
            input_rc, _, err = tv_cmd("set_input", hdmi, retries=1, quiet=True)
            if input_rc == 0:
                return 0
            log(f"set_input failed (attempt {attempt}/{SET_INPUT_ATTEMPTS}): {err}")
            time.sleep(1)
        log("Giving up - could not set input")
        return 1

    if args.command == "OFF":
        # Before the guard on purpose: a disabled event must not spend a round-trip asking the TV
        # anything, least of all inside the pre-down window.
        disabled_by = disabled_off_event()
        if disabled_by is not None:
            log(f"{disabled_by} is off - leaving the TV on")
            return 0
        guard_rc = check_power_off_guard()
        if guard_rc is not None:
            return guard_rc
        if not SOURCE:
            log("Turning TV off")
        rc, _, _ = tv_cmd("power_off")
        if rc != 0:
            return rc
        # lets the suspend path skip a redundant power_off (see suspend.py)
        with contextlib.suppress(OSError):  # not root: just no hint, the TV is off either way
            TV_OFF_FLAG.touch()
        return 0

    if args.command == "SCREEN_OFF":
        guard_rc = check_power_off_guard()
        if guard_rc is not None:
            return guard_rc
        if not SOURCE:
            log("Turning screen off")
        return tv_cmd("turn_screen_off")[0]

    rc, result, err = tv_cmd("get_power_state")
    if rc != 0:
        print(err, file=sys.stderr)
        return rc
    print(f"state={result.get('state', '')}")
    if "processing" in result:
        print(f"processing={result['processing']}")
    return 0
