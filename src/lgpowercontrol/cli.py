# ON | OFF | SCREEN_OFF | STATUS. Exit: 0 ok, 1 error, 2 unreachable, 3 unpaired.
import argparse
import asyncio
import contextlib
import fcntl
import os
import subprocess
import socket
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

CONF = {}
RETRIES = 3

# Wake budget, with the 1s sleep in the loop below. Raised 10 -> 15 once because real wakes
# barely fit; never shrink either number. (A shorter interval was tried and halved the budget.)
# What has to fit: the network coming back after resume, plus the TV itself, which takes about
# 4s from Always Ready, 5s from deep standby and 10s without Always Ready once a packet lands -
# and a lost packet costs a whole resend cycle on top.
WAKE_ATTEMPTS = 15

# Separate budget on purpose: the wake loop has already proven the TV awake and responding, so
# this only covers webOS finishing the input switch. Was 15 - a leftover 15s window from before
# that loop verified anything (see 290ee32/55500b4); nothing has ever needed the extra tries.
SET_INPUT_ATTEMPTS = 5

# asyncio.TimeoutError is listed only for 3.10 - from 3.11 it is OSError. Anything not in here
# is logged as an internal error, so a bug in this program never reads as network trouble.
NETWORK_ERRORS = (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException)


# Wakes the TV. Unrelated to admin.py's `wol` subcommand, which configures Wake-on-LAN on this
# computer's own network card. Sent twice: broadcast is the reliable on-subnet path even when a
# sleeping TV won't ARP-reply, unicast covers cross-VLAN (#12) where WebOS does answer ARP in
# standby. Each copy is a harmless no-op in the other's setup.
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
def tv_cmd(command: str, *args, retries: int | None = None) -> tuple[int, Any, str]:
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
    # A TV that hangs up mid-command leaves request() waiting on a future that bscpylgtv then
    # cancels, and CancelledError subclasses BaseException - so it slips past the catch-all
    # below, out of main(), and lands as a traceback with rc 1, reading as a program bug rather
    # than the network event it is. It has to be named explicitly to be caught at all. Safe to
    # treat as unreachable: asyncio.run turns a Ctrl-C into KeyboardInterrupt, never into this,
    # so no user interrupt can be swallowed here. The exception carries no message of its own.
    except asyncio.CancelledError:
        err = "unreachable: TV closed the connection mid-command"
        rc = 2
    except Exception as exc:  # a bug in this program, not a TV/network state
        err = f"internal error: {type(exc).__name__}: {exc}"
        rc = 1
    log(f"{command}: {err}")
    return rc, None, err


# The HDMI input this computer is on, as a webOS app id ("2" -> com.webos.app.hdmi2), or None
# when unset or malformed. A mismatch is what makes the guards below stand down, so a typo would
# otherwise disable power-off for good, silently and permanently - anything that isn't a plain
# input number of 1 or higher therefore reads as "not configured", the same treatment a bad value
# gets in conf_int(). There is no HDMI 0, so a zero is a typo like any other, not a disable - the
# key is disabled by leaving it empty. load_conf keeps whitespace inside the quotes, hence strip.
def shared_tv_app_id() -> str | None:
    hdmi = CONF.get("POWER_OFF_ONLY_ON_HDMI", "").strip()
    if not hdmi.isdigit() or int(hdmi) < 1:
        if hdmi:
            log(f"POWER_OFF_ONLY_ON_HDMI={hdmi!r} is not an input number - ignoring it")
        return None
    return f"com.webos.app.hdmi{hdmi}"


# The automatic off events that can be switched off one by one, and the conf key that does it.
# A hand-typed `lgpowercontrol OFF` carries no source and is never gated - typing the command is
# the request itself. The idle escalation (dpms-monitor) is deliberately absent: turning the TV
# off before it sits on a static image is what this program exists for, and a shared TV is
# already covered there by POWER_OFF_ONLY_ON_HDMI, which stands the escalation down whenever the
# other source is the one showing.
OFF_EVENT_KEYS = {
    "shutdown":       "POWER_OFF_AT_SHUTDOWN",
    "nm-dispatcher":  "POWER_OFF_AT_SUSPEND",
    "sleep-hook":     "POWER_OFF_AT_SUSPEND",
    "sleep-listener": "POWER_OFF_AT_SUSPEND",
}


# The conf key that switched this off event off, or None to proceed. Only an explicit 0 disables,
# the same reading OFF_WARNING_SECONDS gives it: a typo, a missing key or a conf from a release
# before these keys existed all leave today's behavior in place. That is the safe direction here -
# the failure mode is a TV that turns off when the user wanted it left on, which one press on the
# remote undoes, rather than a TV silently never turning off again. load_conf keeps whitespace
# inside the quotes, hence strip.
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

    log(f"TV on {result}, not on {target_app} - skipping off command")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("wol", "authorize"):
        # lazy import: the ON/OFF path is suspend-critical and must not pay for admin's imports
        from lgpowercontrol import admin
        handler = {"wol": admin.nic_wol, "authorize": admin.authorize}[sys.argv[1]]
        return handler(sys.argv[2:])

    global RETRIES, CONF

    parser = argparse.ArgumentParser(prog="lgpowercontrol")
    # The sleep hook passes 1 so a dead network cannot hold up suspend; the wake loop's own
    # probes always pass 1 regardless, since retrying there is the loop's job.
    parser.add_argument(
        "--retries", type=int, default=RETRIES, metavar="N",
        help="TV connect attempts per command (default 3)",
    )
    parser.add_argument("command", choices=("ON", "OFF", "SCREEN_OFF", "STATUS"))
    args = parser.parse_args()

    RETRIES = max(1, args.retries)
    CONF = load_conf(CONF_FILE)

    if args.command == "ON":
        # At resume the watcher and the dispatcher both fire ON; this flock drops the loser.
        # lock_file must stay bound for the whole ON branch - closing it releases the flock and
        # silently kills the dedupe. Never wrap it in `with` or move it into a helper.
        # 0600 so no other local user can hold the lock and block every future wake. /run is
        # root-only, so a hand-typed ON runs unlocked; only root callers can actually collide.
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
            if subprocess.run(["nm-online", "-q", "-t", "15"]).returncode != 0:
                log("Network still down after 15s; trying anyway")
        except OSError:
            pass  # no NetworkManager - nothing to wait for

        send_wol()

        # Waits for the network to come back and the TV to wake; see WAKE_ATTEMPTS.
        state = ""
        rc = 1  # non-zero until an attempt succeeds; also what "gave up" returns
        for attempt in range(1, WAKE_ATTEMPTS + 1):
            time.sleep(1)  # also avoids a "No Signal" flash before the source is ready
            rc = 1
            progress = f"{attempt}/{WAKE_ATTEMPTS}"

            state_rc, result, _ = tv_cmd("get_power_state", retries=1)
            if state_rc != 0:
                log(f"get_power_state failed (attempt {progress})")
                send_wol()
                continue
            state = result.get("state", "")
            processing = result.get("processing", "")

            # Mid-transition: the state value can't be trusted to say which standby the TV is
            # leaving, so wait for a plain state rather than act on this one.
            if processing:
                log(f"TV mid-transition: {state} ({processing}) - waiting (attempt {progress})")
                continue

            # The only three states that mean the TV is awake. Everything else ("Active Standby"
            # from power_off, "Suspend" from deep standby, or an unknown state) means the magic
            # packet never landed - resending is the safe treatment either way.
            if state in ("Active", "Screen Off", "Screen Saver"):
                rc, _, _ = tv_cmd("turn_screen_on", retries=1)
                if rc == 102:  # -102 is ambiguous; the state above is what proves the TV awake
                    rc = 0
                if rc == 0:
                    log(f"TV awake ({state}), screen turned on (attempt {progress})")
                    break
                log(f"turn_screen_on failed with TV awake ({state}) (attempt {progress})")
            else:
                log(f"TV in standby ({state or '?'}) - resending WoL (attempt {progress})")
                send_wol()

        if rc != 0:
            log(f"Giving up - TV unreachable (last state: {state or 'unknown'})")
            return rc

        if not CONF.get("HDMI_INPUT"):
            return 0

        # Counterpart to the guard on OFF/SCREEN_OFF: a shared TV may be showing the other source
        # right now, and switching inputs would yank the picture off whoever is watching it - the
        # off guard would otherwise leave the TV alone at suspend only for the wake to grab it
        # anyway. Deciding per wake who owns the input needs a get_current_app round-trip and a
        # rule for the case where we woke the TV ourselves; nobody has asked for that combination,
        # so the shared setting simply wins over HDMI_INPUT. This is also exactly the configuration
        # the feature was contributed and tested against (#15), where HDMI_INPUT is left empty.
        # Checked after HDMI_INPUT so the line stays out of the journal for that setup.
        if shared_tv_app_id() is not None:
            log("POWER_OFF_ONLY_ON_HDMI is set - not switching input, the TV may be in use")
            return 0

        hdmi = f"HDMI_{CONF['HDMI_INPUT']}"
        log(f"Setting input to {hdmi}")  # the app layer can lag a wake from deep standby
        for attempt in range(1, SET_INPUT_ATTEMPTS + 1):
            if tv_cmd("set_input", hdmi, retries=1)[0] == 0:
                return 0
            log(f"set_input failed (attempt {attempt}/{SET_INPUT_ATTEMPTS})")
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
