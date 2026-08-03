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

import websockets.exceptions
from bscpylgtv import WebOsClient
from bscpylgtv.exceptions import PyLGTVCmdError, PyLGTVCmdException, PyLGTVPairException

from lgpowercontrol.common import CONF_FILE, ON_LOCK, PAIRING_DB, TV_OFF_FLAG, Logger, load_conf

SOURCE = os.environ.get("LGPC_SOURCE", "")  # who invoked this, for log lines
log = Logger(SOURCE or "cli")

CONF = {}
RETRIES = 3

# Everything else escaping the library is logged as an internal error, never mistaken for network trouble.
NET_EXCS = (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException)


# broadcast: reliable on-subnet path even if the TV won't ARP-reply asleep.
# unicast: covers cross-VLAN (#12). Each is a harmless no-op in the other's setup.
def send_wol() -> None:
    try:
        mac = bytes.fromhex(CONF.get("LGTV_MAC", "").replace(":", "").replace("-", ""))
    except ValueError:
        return
    if len(mac) != 6:
        return
    packet = b"\xff" * 6 + mac * 16
    for dest, broadcast in ((("255.255.255.255", 9), True), ((CONF.get("LGTV_IP", ""), 9), False)):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                if broadcast:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(packet, dest)
        except OSError:
            pass


# Returns (rc, result, err). rc 102 = turn_screen_on refused with -102, ambiguous
# by design (screen already on vs TV asleep) - caller checks get_power_state.
def tv(command: str, *args, retries: int | None = None):
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
    except PyLGTVCmdError as exc:
        payload = exc.args[0]["payload"]  # guaranteed dict+payload by the raise site in webos_client.py
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
    except NET_EXCS as exc:
        err = f"unreachable: {type(exc).__name__}: {exc}"
        rc = 2
    except Exception as exc:  # a bug in this program, not a TV/network state
        err = f"internal error: {type(exc).__name__}: {exc}"
        rc = 1
    log(f"{command}: {err}")
    return rc, None, err


def main() -> int:
    parser = argparse.ArgumentParser(prog="lgpowercontrol")
    parser.add_argument(
        "--retries", type=int, default=3, metavar="N",
        help="TV connect attempts per command (default 3; the sleep hook "
             "passes 1 so a dead network cannot hold up suspend - the wake "
             "loop's own probes always use 1)",
    )
    parser.add_argument("command", choices=("ON", "OFF", "SCREEN_OFF", "STATUS"))
    args = parser.parse_args()

    global RETRIES, CONF
    RETRIES = max(1, args.retries)
    CONF = load_conf(CONF_FILE)
    log.configure(CONF)

    if args.command == "ON":
        # 0600: a world-readable lock file would let any user hold it forever, neutralizing ON.
        lockf = os.fdopen(os.open(ON_LOCK, os.O_WRONLY | os.O_CREAT, 0o600), "w")
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0  # concurrent ON already running - dedupe

        if not SOURCE:
            log("Turning TV on")
        TV_OFF_FLAG.unlink(missing_ok=True)

        try:
            if subprocess.run(["nm-online", "-q", "-t", "15"]).returncode != 0:
                log("Network still down after 15s; trying anyway")
        except OSError:
            pass  # no NetworkManager - nothing to wait for

        send_wol()

        # 15x1s budget for network-up + TV wake; keep interval at 1s, budget barely
        # fits real wakes already (don't relitigate - see CLAUDE.md).
        rc = 1
        state = ""
        for attempt in range(1, 16):
            time.sleep(1)  # also avoids a "No Signal" flash before the source is ready
            rc = 1

            prc, result, _ = tv("get_power_state", retries=1)
            if prc != 0:
                log(f"get_power_state failed (attempt {attempt}/15)")
                send_wol()
                continue
            state = result.get("state", "")
            processing = result.get("processing", "")

            if processing:
                log(f"TV mid-transition: {state} ({processing}) - waiting (attempt {attempt}/15)")
                continue

            if state in ("Active", "Screen Off", "Screen Saver"):
                rc, _, _ = tv("turn_screen_on", retries=1)
                if rc == 102:  # screen already on; state above proves TV awake
                    rc = 0
                if rc == 0:
                    log(f"TV awake ({state}), screen turned on (attempt {attempt}/15)")
                    break
                log(f"turn_screen_on failed with TV awake ({state}) (attempt {attempt}/15)")
            else:
                log(f"TV in standby ({state or '?'}) - resending WoL (attempt {attempt}/15)")
                send_wol()

        if rc != 0:
            log(f"Giving up - TV unreachable (last state: {state or 'unknown'})")
            return rc

        if not CONF.get("HDMI_INPUT"):
            return 0

        hdmi = f"HDMI_{CONF['HDMI_INPUT']}"
        log(f"Setting input to {hdmi}")  # may still be booting, so retry
        for attempt in range(1, 16):
            if tv("set_input", hdmi, retries=1)[0] == 0:
                return 0
            log(f"set_input failed (attempt {attempt}/15)")
            time.sleep(1)
        log("Giving up - could not set input")
        return 1

    if args.command == "OFF":
        if not SOURCE:
            log("Turning TV off")
        rc, _, _ = tv("power_off")
        if rc != 0:
            return rc
        TV_OFF_FLAG.touch()  # lets the suspend hook skip a redundant power_off (see nm_dispatcher.py)
        return 0

    if args.command == "SCREEN_OFF":
        if not SOURCE:
            log("Turning screen off")
        return tv("turn_screen_off")[0]

    rc, result, err = tv("get_power_state")
    if rc != 0:
        print(err, file=sys.stderr)
        return rc
    print(f"state={result.get('state', '')}")
    if "processing" in result:
        print(f"processing={result['processing']}")
    return 0
