#!/usr/bin/python3
"""virtual-webos-tv - a test double for an LG webOS TV's SSAP control surface.

Stages 1 and 2: TLS transport, the pairing handshake, the command set lgpowercontrol uses, a
power state machine with the -102 ambiguity, a Wake-on-LAN listener, and fault injection.
Stage 3 (latency and packet-loss knobs) is planned, not built.

Every response form and every endpoint here is derived mechanically from the installed
bscpylgtv 0.5.2 under /opt/lgpowercontrol/lib, or from an observation on real hardware recorded
in CLAUDE.md. Anything that could not be derived either way carries a GUESSED comment, and the
list of those is the honest measure of what a green run is worth - see CLAUDE.md section 6.
"""

import argparse
import asyncio
import json
import random
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CERT_DIR = REPO_DIR / "cert"

# websockets is not installed system-wide; lgpowercontrol ships its own copy (17.0.1, verified
# 2026-08-09). Appended rather than inserted so a system install would still win if one appears.
LGPC_LIB = "/opt/lgpowercontrol/lib"
if LGPC_LIB not in sys.path:
    sys.path.append(LGPC_LIB)

try:
    import websockets.exceptions
    # websockets 17.0.1: `websockets.serve` already resolves to this module. Importing it
    # explicitly means a future drop of the legacy shim cannot silently change what we bind.
    from websockets.asyncio.server import serve
except ModuleNotFoundError:  # pragma: no cover - environment problem, not a test outcome
    sys.exit(f"websockets not importable; expected it under {LGPC_LIB}")


# --- The protocol surface, copied verbatim from bscpylgtv/endpoints.py --------------------

GET_CURRENT_APP_INFO = "com.webos.applicationManager/getForegroundAppInfo"
GET_POWER_STATE = "com.webos.service.tvpower/power/getPowerState"
TURN_OFF_SCREEN = "com.webos.service.tvpower/power/turnOffScreen"
TURN_ON_SCREEN = "com.webos.service.tvpower/power/turnOnScreen"
POWER_OFF = "system/turnOff"
SET_INPUT = "tv/switchInput"
GET_INPUTS = "tv/getExternalInputList"

# turn_screen_on/off(webos_ver="") resolve to the two above, not the WO4 variants:
#   epName = f"TURN_OFF_SCREEN_WO{webos_ver}" if webos_ver else "TURN_OFF_SCREEN"
# lgpowercontrol always calls them with no version, so the WO4 URIs are deliberately absent -
# a request for one gets the 404 every unimplemented endpoint gets, which is the truth.

# Short names for the command line. A name that is not in this map is rejected by argparse
# instead of quietly never matching.
ENDPOINT_ALIASES = {
    "current_app": GET_CURRENT_APP_INFO,
    "power_state": GET_POWER_STATE,
    "power_off": POWER_OFF,
    "screen_off": TURN_OFF_SCREEN,
    "screen_on": TURN_ON_SCREEN,
    "set_input": SET_INPUT,
    "get_inputs": GET_INPUTS,
}


# --- The power state machine ---------------------------------------------------------------
#
# State strings observed on real hardware (OLED42C35LA, 2026-08-09) and nailed to that list:
# is_on() in webos_client.py and the wake loop in cli.py both branch on these exact values, so
# an invented one is the failure mode CLAUDE.md section 6 warns about.

ACTIVE = "Active"
SCREEN_OFF = "Screen Off"
SCREEN_SAVER = "Screen Saver"
ACTIVE_STANDBY = "Active Standby"
SUSPEND = "Suspend"

POWER_STATES = (ACTIVE, ACTIVE_STANDBY, SUSPEND, SCREEN_OFF, SCREEN_SAVER)

# The three the wake loop accepts as "awake"; also exactly the three where is_on() is true.
AWAKE_STATES = (ACTIVE, SCREEN_OFF, SCREEN_SAVER)
STANDBY_STATES = (ACTIVE_STANDBY, SUSPEND)

# "under uppvaknande = state + processing: 'Screen On'" (CLAUDE.md section 5). The wake loop
# only checks that `processing` is non-empty and waits, deliberately not trusting the state
# next to it - so which state we report alongside this does not reach any assertion.
PROCESSING_SCREEN_ON = "Screen On"

# Observed wake durations: ~4s from Always Ready, ~5s from deep standby (CLAUDE.md section 5).
WAKE_SECONDS = {ACTIVE_STANDBY: 4.0, SUSPEND: 5.0}

# GUESSED, and it matters. Whether a real TV still answers the WebSocket in deep standby has
# never been checked - it depends on the Always Ready setting and on how the TV parks its
# network stack. The wake loop handles both (a failed get_power_state and a read of "Suspend"
# both resend WoL), so the honest move is to make it a knob and test both, not to pick one.
DEFAULT_OFFLINE_STATES = (SUSPEND,)


# --- Response forms, derived from request() in webos_client.py lines 696-712 ---------------
#
# The client does, in order: reject a response with no "payload" key at all, compute
# returnValue = payload.get("returnValue") or payload.get("subscribed"), then branch on
# type == "error". So an error response still needs a payload key, or the client raises
# PyLGTVCmdException from the line above the error branch instead of the error you aimed for.

def response_ok(uid, **payload):
    """-> request() returns the payload, tv_cmd() gives rc 0."""
    return {"id": uid, "type": "response", "payload": {"returnValue": True, **payload}}


def error_response(uid, error, code, text):
    """-> PyLGTVCmdError raised with the whole response dict, tv_cmd() gives rc 1 (or 102).

    lgpowercontrol reads exc.args[0]["payload"], so the code has to sit in payload.errorCode.
    Any `error` value other than the 404 string takes this branch, so the branch is derived;
    the strings are not.
    """
    return {"id": uid, "type": "error", "error": error,
            "payload": {"returnValue": False, "errorCode": code, "errorText": text}}


def error_not_found(uid):
    """-> PyLGTVServiceNotFoundError, tv_cmd() gives rc 1. The string is matched exactly."""
    return {"id": uid, "type": "error", "error": "404 no such service or method", "payload": {}}


def error_screen_state(uid):
    """turn_screen_on refused. -102 -> tv_cmd() returns 102, which the wake loop treats as 0.

    The errorCode is derived: cli.py compares str(payload["errorCode"]) to "-102", and the whole
    ambiguity ("screen already on" vs "TV asleep") is documented there. The `error` string and
    the errorText are GUESSED - no -102 response has ever been captured off real hardware, only
    its effect. If one is ever captured, replace these two strings and nothing else.
    """
    return error_response(uid, "-102 Invalid Input", -102, "Invalid Input")


def fault_error(uid):
    """Generic non-404 error for injection. GUESSED strings; nothing branches on them."""
    return error_response(uid, "500 Internal Error", -1000, "internal error")


def fault_no_payload(uid):
    """-> PyLGTVCmdException, tv_cmd() gives rc 1. The trap: no "payload" key at all."""
    return {"id": uid, "type": "error", "error": "500 Internal Error"}


def fault_return_false(uid):
    """-> PyLGTVCmdException, tv_cmd() gives rc 1.

    Not the branch you would expect: `returnValue = payload.get("returnValue") or
    payload.get("subscribed")` turns False into None, so this lands on "Invalid request response"
    (returnValue is None), not "Request failed with response" (not returnValue). Verified against
    the real stack 2026-08-09. That last branch needs a falsy-but-not-None value - a "subscribed"
    of 0 - and nothing here produces one.
    """
    return {"id": uid, "type": "response", "payload": {"returnValue": False}}


FAULTS = {
    "not-found": error_not_found,
    "error": fault_error,
    "no-payload": fault_no_payload,
    "return-false": fault_return_false,
    "screen-102": error_screen_state,
}


# --- Wake-on-LAN ---------------------------------------------------------------------------

def parse_magic_packet(data):
    """Return the target MAC as "aa:bb:..", or None if this is not a magic packet.

    send_wol() in cli.py builds b"\\xff" * 6 + mac * 16; anything else is not addressed to us.
    """
    if len(data) < 102 or data[:6] != b"\xff" * 6:
        return None
    mac = data[6:12]
    if data[6:102] != mac * 16:
        return None
    return mac.hex(":")


class WolProtocol(asyncio.DatagramProtocol):
    def __init__(self, tv):
        self.tv = tv

    def datagram_received(self, data, addr):
        mac = parse_magic_packet(data)
        if mac is None:
            self.tv.journal("wol-malformed", size=len(data), source=addr[0])
            return
        self.tv.receive_wol(mac, addr[0])


# --- Journal ------------------------------------------------------------------------------
#
# The rig cannot tell "the guard stood down" from "the guard proceeded" by exit code alone -
# both are 0 - so it asks the TV what it was actually told to do, and what state that left it
# in. One JSON object per line, flushed on write so a reader polling the file sees events live.

START = time.monotonic()


class Journal:
    def __init__(self, path, pretty=False):
        self.file = open(path, "w", buffering=1) if path else None
        self.pretty = pretty

    def __call__(self, event, **fields):
        record = {"t": round(time.monotonic() - START, 4), "event": event, **fields}
        if self.file:
            self.file.write(json.dumps(record) + "\n")
        # The file always gets JSON so the rigs are unaffected by how the terminal looks.
        line = format_event(record) if self.pretty else json.dumps(record)
        if line is not None:
            print(line, file=sys.stderr, flush=True)

    def close(self):
        if self.file:
            self.file.close()


# --- Human-readable view --------------------------------------------------------------------
#
# Only for a terminal. Every event still reaches --journal as JSON; this decides what is worth
# a line when a person is watching the TV live, and what is noise.

def short_uri(uri):
    return uri.rsplit("/", 1)[-1] or uri


def describe_payload(payload):
    """The few fields worth seeing inline. Everything else is in the journal."""
    if not payload:
        return ""
    interesting = ("appId", "state", "processing", "inputId", "errorCode", "errorText")
    parts = [f"{key}={payload[key]}" for key in interesting if key in payload]
    if "devices" in payload:
        parts.append(f"devices={len(payload['devices'])}")
    return "  ".join(parts)


def format_event(record):
    """Return the line to print, or None to keep this event out of the terminal."""
    when = f"{record['t']:7.2f}s"
    conn = record.get("conn")
    tag = f"conn {conn}" if conn else ""
    event = record["event"]

    def line(body, marker=" "):
        return f"{when}  {tag:<7} {marker} {body}"

    if event == "ready":
        faults = record.get("faults") or {}
        extra = f"  faults={','.join(faults.values())}" if faults else ""
        offline = ", ".join(record.get("offline_states") or []) or "none"
        return (f"{when}  virtual TV ready\n"
                f"          state={record['state']}  app={record['app_id']}  "
                f"mac={record['mac']}  offline in: {offline}{extra}")
    if event == "listening":
        return f"{when}  listening on wss://{record['host']}:{record['port']}"
    if event == "offline":
        return f"{when}  offline - refusing TCP while in {record['state']}"
    if event == "wol-listening":
        return f"{when}  wake-on-lan on udp {record['host']}:{record['port']} for {record['mac']}"
    if event == "wol-bind-denied":
        return (f"{when}  wake-on-lan unavailable: udp {record['port']} needs root\n"
                f"          type 'wol' to wake the TV by hand instead")
    if event == "wol":
        return f"{when}  {'wol':<7} * magic packet from {record['source']}"
    if event in ("wol-ignored", "wol-other-mac", "wol-malformed"):
        return f"{when}  {'wol':<7} * ignored ({event.removeprefix('wol-')})"
    if event == "state":
        processing = f" [{record['processing']}]" if record.get("processing") else ""
        if record["was"] == record["to"]:
            # Only the processing flag moved - "X -> X" would read as a stutter.
            body = f"{record['to']}{processing}"
        else:
            was = f" [{record['was_processing']}]" if record.get("was_processing") else ""
            body = f"{record['was']}{was} -> {record['to']}{processing}"
        return f"{when}  {'state':<7} = {body}  ({record['cause']})"
    if event == "connect":
        return line("connected")
    if event == "disconnect":
        return line("disconnected")
    if event == "register":
        known = "with a stored key" if record.get("client_key") else "no key yet"
        return line(f"register  ({known})", ">")
    if event == "registered":
        return line("registered", "<")
    if event == "pairing-refused":
        return line("pairing refused", "<")
    if event == "request":
        payload = describe_payload(record.get("payload"))
        return line(f"{short_uri(record['uri'])}{'  ' + payload if payload else ''}", ">")
    if event == "response":
        if record.get("type") == "error":
            body = f"error  {record.get('error', '')}  {describe_payload(record.get('payload'))}"
        else:
            body = f"ok  {describe_payload(record.get('payload'))}".rstrip()
        return line(body.rstrip(), "<")
    if event == "fault":
        return line(f"injecting {record['fault']} on {short_uri(record['uri'])}", "!")
    if event == "drop":
        return line("response dropped - the client has no timeout, so it now hangs", "x")
    if event == "hang-up":
        return line("hanging up mid-command", "x")
    if event == "unimplemented":
        return line(f"unknown endpoint {record['uri']}", "?")
    if event == "input-not-ready":
        return line(f"app layer not ready for {record['input']}", "!")
    if event == "input":
        return f"{when}  {'input':<7} = {record['input']} -> {record['app_id']}"
    if event == "delay":
        return f"{when}  {'delay':<7} . {record['kind']} {record['seconds']}s"
    if event == "control":
        return f"{when}  {'control':<7} $ {record['command']}"
    if event in ("control-error", "control-eof", "status", "protocol-error"):
        return f"{when}  {'control':<7} . {record.get('note', record.get('text', event))}"
    if event == "stopped":
        return f"{when}  stopped after {record['connections']} connections"
    # handshake fires once per connection attempt and says nothing that `connect` does not,
    # except when a client gives up during it - and then the retry count is what matters,
    # which the journal has. Kept out of the terminal on purpose.
    return None


# --- Timing and loss (stage 3) --------------------------------------------------------------
#
# Calibrated against the real TV (OLED42C35LA, 2026-08-09, CLAUDE.md section 5): one session
# cost 71 ms, of which connect was 57 and the command itself 7.7. The handshake is where the
# time goes, which is why the OFF guard's extra session costs ~67 ms rather than ~8.
CALIBRATED_HANDSHAKE_MS = 57.0
CALIBRATED_COMMAND_MS = 7.7

# Where the delays land relative to the client's only timeout, which is the whole point of
# splitting them into three knobs:
#
#   handshake  inside asyncio.wait_for(websockets.connect(...), timeout=timeout_connect).
#              Exceeding timeout_connect (2s) produces a real TimeoutError and a retry.
#   pairing    the two ws.recv() calls after the handshake. NOT under any timeout.
#   command    request() awaits its future. NOT under any timeout either.
#
# So only the first knob can ever produce an error. The other two produce a hang, and with
# ping_interval=None - which lgpowercontrol always passes - the client has no keepalive that
# would ever notice. Verified by grepping every wait_for in webos_client.py, 2026-08-09.


class Timing:
    def __init__(self, args, journal):
        self.journal = journal
        self.handshake = args.latency_handshake / 1000.0
        self.pairing = args.latency_pairing / 1000.0
        self.command = args.latency_command / 1000.0
        self.jitter = args.jitter / 1000.0
        self.loss = args.loss
        self.close = args.close
        self.random = random.Random(args.seed)

    def delay(self, base):
        """Jitter is symmetric and clamped at zero, so a large jitter skews the mean upward."""
        if base and self.jitter:
            base += self.random.uniform(-self.jitter, self.jitter)
        return max(0.0, base)

    def roll(self, probability):
        # Only draw when the knob is actually in use, so turning one on cannot shift the
        # sequence the other one sees. Both are drawn in a fixed order by respond().
        if probability <= 0:
            return False
        return self.random.random() < probability

    async def sleep(self, base, kind):
        seconds = self.delay(base)
        if seconds:
            self.journal("delay", kind=kind, seconds=round(seconds, 4))
            await asyncio.sleep(seconds)


# --- The TV -------------------------------------------------------------------------------

class VirtualWebOsTv:
    def __init__(self, args, journal):
        self.journal = journal
        self.state = args.power_state
        self.app_id = args.app_id
        self.processing = None
        self.mac = args.mac.lower().replace("-", ":")
        self.offline_states = args.offline_states
        self.wake_seconds = args.wake_seconds
        self.input_lag = args.input_lag_seconds
        self.ignore_wol = args.ignore_wol
        self.faults = dict(args.errors)
        self.refuse_pairing = args.refuse_pairing
        self.timing = Timing(args, journal)

        self.connections = 0
        self.handshakes = 0
        self.waking = None
        self.input_ready_at = 0.0
        # Set whenever reachability may have changed, so the transport supervisor re-evaluates.
        self.transport_dirty = asyncio.Event()

    # -- state ---------------------------------------------------------------------------

    def reachable(self):
        """Whether the TV answers TCP at all.

        A waking TV is reachable regardless: the network came back first - that is what
        received the magic packet - and the wake loop must be able to see `processing`.
        """
        if self.processing is not None:
            return True
        return self.state not in self.offline_states

    def transition(self, state, cause, processing=None):
        if (state, processing) == (self.state, self.processing):
            return
        self.journal("state", to=state, processing=processing,
                     was=self.state, was_processing=self.processing, cause=cause)
        self.state = state
        self.processing = processing
        self.transport_dirty.set()

    def receive_wol(self, mac, source):
        if self.ignore_wol:
            self.journal("wol-ignored", mac=mac, source=source)
            return
        if mac != self.mac:
            self.journal("wol-other-mac", mac=mac, source=source)
            return
        self.journal("wol", mac=mac, source=source)
        if self.state in STANDBY_STATES and self.waking is None:
            self.waking = asyncio.create_task(self.wake())

    async def wake(self):
        """Standby -> processing -> Active, on the observed timings."""
        from_state = self.state
        delay = self.wake_seconds
        if delay is None:
            delay = WAKE_SECONDS[from_state]
        self.transition(from_state, cause="wol-wake-start", processing=PROCESSING_SCREEN_ON)
        await asyncio.sleep(delay)
        self.transition(ACTIVE, cause="wol-wake-done")
        # "the app layer can lag a wake from deep standby" (cli.py, above SET_INPUT_ATTEMPTS) -
        # that comment is why the retry loop exists, so the lag is modelled rather than invented.
        self.input_ready_at = time.monotonic() + self.input_lag
        self.waking = None

    # -- transport -----------------------------------------------------------------------

    async def send(self, ws, message):
        await ws.send(json.dumps(message))

    async def respond(self, ws, conn, uri, message):
        """Answer a command, subject to the stage 3 latency and loss knobs.

        Both dice are rolled before the delay so that changing a latency setting cannot change
        which requests a given --seed drops.
        """
        hang_up = self.timing.roll(self.timing.close)
        drop = self.timing.roll(self.timing.loss)
        await self.timing.sleep(self.timing.command, kind="command")

        if hang_up:
            # A link that died mid-exchange. This one the client survives: the socket closing
            # cancels the pending future, which surfaces as a WebSocketException -> rc 2.
            self.journal("hang-up", conn=conn, uri=uri)
            await ws.close()
            return
        if drop:
            # A lost response. request() awaits its future with no timeout and ping_interval is
            # None, so the client waits here forever - this is a hang, not an error. Keeping
            # the socket open is the point; closing it would let the client recover.
            self.journal("drop", conn=conn, uri=uri,
                         note="no timeout on request(); the client now waits indefinitely")
            return
        await self.send(ws, message)
        # Logged after the send so the journal shows what actually went out, and so a person
        # watching sees the answer next to the question rather than having to infer it.
        self.journal("response", conn=conn, uri=uri, type=message.get("type"),
                     error=message.get("error"), payload=message.get("payload"))

    async def handler(self, ws):
        self.connections += 1
        conn = self.connections
        self.journal("connect", conn=conn)
        try:
            if not await self.register(ws, conn):
                return
            async for raw in ws:
                await self.dispatch(ws, conn, json.loads(raw))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.journal("disconnect", conn=conn)

    async def register(self, ws, conn):
        """The three-message pairing, from connect_handler() in webos_client.py lines 226-238.

        The client recv()s twice whenever the first reply is type "response" with
        payload.pairingType == "PROMPT", whether or not it already holds a key - so both
        messages always go out, or the connection hangs until its 2s connect timeout.
        """
        message = json.loads(await ws.recv())
        uid = message.get("id", "register_0")
        if message.get("type") != "register":
            self.journal("protocol-error", conn=conn, got=message.get("type"))
            return False

        offered_key = (message.get("payload") or {}).get("client-key")
        self.journal("register", conn=conn, client_key=offered_key)

        # One round trip's worth, before the first reply. Nothing on the client side bounds
        # this wait: the ws.recv() calls below sit outside every wait_for in webos_client.py.
        await self.timing.sleep(self.timing.pairing, kind="pairing")

        if self.refuse_pairing:
            # First message unchanged, so the client takes the second recv() and finds a
            # non-"registered" type there; client_key stays None and PyLGTVPairException fires.
            # A client that already holds a key does NOT fail here - see CLAUDE.md section 2.
            await self.send(ws, {"id": uid, "type": "response",
                                 "payload": {"pairingType": "PROMPT", "returnValue": True}})
            # GUESSED wording. The client only checks that type != "registered"; what a real TV
            # sends when the on-screen prompt is denied has not been captured.
            await self.send(ws, {"id": uid, "type": "error",
                                 "error": "403 User denied access", "payload": {}})
            self.journal("pairing-refused", conn=conn)
            return False

        await self.send(ws, {"id": uid, "type": "response",
                             "payload": {"pairingType": "PROMPT", "returnValue": True}})
        # A real TV hands back a key the client then stores per IP. Echoing the offered key back
        # keeps an existing sqlite entry stable; a fresh client gets a constant stand-in.
        await self.send(ws, {"id": uid, "type": "registered",
                             "payload": {"client-key": offered_key or "virtual-webos-tv-key"}})
        self.journal("registered", conn=conn)
        return True

    # -- commands ------------------------------------------------------------------------

    async def dispatch(self, ws, conn, message):
        uid = message.get("id")
        uri = str(message.get("uri", "")).removeprefix("ssap://")
        payload = message.get("payload") or {}
        self.journal("request", conn=conn, uri=uri, payload=payload, state=self.state)

        fault = self.faults.get(uri)
        if fault:
            self.journal("fault", conn=conn, uri=uri, fault=fault)
            await self.respond(ws, conn, uri, FAULTS[fault](uid))
            return

        handler = {
            GET_CURRENT_APP_INFO: self.do_current_app,
            GET_POWER_STATE: self.do_power_state,
            POWER_OFF: self.do_power_off,
            TURN_OFF_SCREEN: self.do_screen_off,
            TURN_ON_SCREEN: self.do_screen_on,
            SET_INPUT: self.do_set_input,
            GET_INPUTS: self.do_get_inputs,
        }.get(uri)

        if handler is None:
            # An unimplemented endpoint answers like a webOS version that lacks it, which is
            # also what makes scope creep loud instead of silent.
            self.journal("unimplemented", conn=conn, uri=uri)
            await self.respond(ws, conn, uri, error_not_found(uid))
            return

        await self.respond(ws, conn, uri, handler(uid, payload))

    def do_current_app(self, uid, payload):
        # get_current_app() reads res.get("appId") off the returned payload.
        #
        # Still GUESSED here, but no longer unknown: a real C3 that had gone to standby on its
        # own answered rc 0 with an empty appId (Basse's journal, 2026-08-12), so the guard skips
        # for a TV that is already off - the direction this comment predicted. The model is left
        # returning the app id in every reachable state because the journal does not say which
        # standby state that TV was in, and a controlled probe has not been run. Modelling it as
        # "" per standby state is the change to make once it has. See CLAUDE.md section 9.
        return response_ok(uid, appId=self.app_id)

    def do_power_state(self, uid, payload):
        # cli.py reads result["state"] and result.get("processing", ""). The processing key is
        # present only mid-transition, matching what the hardware showed.
        extra = {"processing": self.processing} if self.processing else {}
        return response_ok(uid, state=self.state, **extra)

    def do_power_off(self, uid, payload):
        # power_off() checks is_on first and never sends this from a standby state, and it uses
        # command() rather than request(), so no future is registered and this reply is dropped
        # unread by consumer_handler. Answered anyway because a real TV does.
        #
        # Active Standby is where power_off lands, not Power Off: cli.py's wake loop says so
        # in as many words ("Active Standby" from power_off), which makes it hardware-derived
        # rather than a guess. A TV with Always Ready disabled would presumably reach Suspend;
        # that has not been observed, so it is not modelled.
        if self.state in AWAKE_STATES:
            self.transition(ACTIVE_STANDBY, cause=POWER_OFF)
        return response_ok(uid)

    def do_screen_off(self, uid, payload):
        # payload is {"standbyMode": "active"}; passive is never sent by lgpowercontrol.
        if self.state in AWAKE_STATES:
            self.transition(SCREEN_OFF, cause=TURN_OFF_SCREEN)
            return response_ok(uid)
        # GUESSED: turning the screen off on a TV that is already in standby. Refused, on the
        # same -102 the other direction uses, because a success would be a stronger claim.
        return error_screen_state(uid)

    def do_screen_on(self, uid, payload):
        """The -102 ambiguity, which is the whole reason stage 2 exists.

        cli.py: "rc 102 = turn_screen_on refused with -102, ambiguous by design (screen already
        on vs TV asleep) - caller checks get_power_state." Both halves are modelled:

          Screen Off / Screen Saver -> success, the screen really did come on
          Active                    -> -102, screen already on          (derived from cli.py)
          Active Standby            -> -102, TV asleep                  (GUESSED)

        The wake loop only ever sends this after get_power_state returned an awake state, so it
        maps 102 to 0 and moves on; the second row is what makes that mapping necessary.
        """
        if self.state in (SCREEN_OFF, SCREEN_SAVER):
            self.transition(ACTIVE, cause=TURN_ON_SCREEN)
            return response_ok(uid)
        return error_screen_state(uid)

    def do_set_input(self, uid, payload):
        # set_input(input) sends {"inputId": input}, and cli.py passes f"HDMI_{n}".
        input_id = str(payload.get("inputId", ""))
        if self.state not in AWAKE_STATES:
            return error_screen_state(uid)  # GUESSED: switching input on a TV in standby
        if time.monotonic() < self.input_ready_at:
            # The app layer lagging a wake, which is why SET_INPUT_ATTEMPTS exists.
            self.journal("input-not-ready", input=input_id)
            return fault_error(uid)  # GUESSED error form; only the failure itself is derived
        number = input_id.removeprefix("HDMI_")
        if not number.isdigit():
            return error_response(uid, "400 Invalid input", -1000, f"no input {input_id}")
        self.app_id = f"com.webos.app.hdmi{number}"
        self.journal("input", input=input_id, app_id=self.app_id)
        return response_ok(uid)

    def do_get_inputs(self, uid, payload):
        # get_inputs() reads res.get("devices"). lgpowercontrol never calls this; it is here so
        # the command set is complete. The device list is GUESSED in every field but appId.
        devices = [{"id": f"HDMI_{n}", "appId": f"com.webos.app.hdmi{n}",
                    "label": f"HDMI {n}", "connected": True} for n in (1, 2, 3, 4)]
        return response_ok(uid, devices=devices)


# --- Transport supervisor -------------------------------------------------------------------
#
# A TV in deep standby does not answer TCP, and the difference matters: lgpowercontrol maps a
# refused connection to rc 2 and a readable "Suspend" to a WoL resend, and those are different
# code paths. So the listener is genuinely opened and closed as the state changes, rather than
# faking it by accepting and hanging up.

def make_process_request(tv):
    """Count and optionally delay the WebSocket opening handshake.

    Counting here rather than in handler() is the only way to see a connection attempt that
    never became a session: handler() runs after the handshake, so a client that gives up
    during it leaves no trace there at all. That is exactly what connect_retry_attempts
    produces, and the difference between attempts and sessions is how the rig sees it.

    This is also the one delay the client can time out on: it lands inside
    asyncio.wait_for(websockets.connect(...), timeout=timeout_connect). Exceeding the 2s
    default produces a genuine TimeoutError, a retry after connect_retry_interval_ms, and
    finally rc 2 - the only knob here that yields an error rather than a hang.
    """
    async def process_request(connection, request):
        tv.handshakes += 1
        tv.journal("handshake", attempt=tv.handshakes)
        await tv.timing.sleep(tv.timing.handshake, kind="handshake")
        return None  # None means "carry on with the handshake"

    return process_request


async def transport_supervisor(tv, args, ssl_context, stop):
    server = None
    try:
        while not stop.is_set():
            if tv.reachable() and server is None:
                server = await serve(tv.handler, args.host, args.port,
                                     ssl=ssl_context, ping_interval=None,
                                     process_request=make_process_request(tv))
                tv.journal("listening", host=args.host, port=args.port, state=tv.state)
            elif not tv.reachable() and server is not None:
                server.close()
                await server.wait_closed()
                server = None
                tv.journal("offline", state=tv.state,
                           note="refusing TCP, as a TV in this state is assumed to")

            tv.transport_dirty.clear()
            waiters = [asyncio.ensure_future(tv.transport_dirty.wait()),
                       asyncio.ensure_future(stop.wait())]
            done, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()


# --- Live control from stdin ----------------------------------------------------------------
#
# The point of running this in its own terminal is that it behaves like a TV standing in the
# room: it keeps its state between commands, and someone can walk over and press a button. The
# terminal is both the log and the remote, so no second channel or control socket is needed.
#
# Unlike the rigs, this mode is stateful on purpose. A rig case must not depend on what the
# previous case did, which is why each of those gets its own server - do not "simplify" them
# into sharing one.

CONTROL_STATES = {
    "on": ACTIVE, "active": ACTIVE,
    "standby": ACTIVE_STANDBY,
    "suspend": SUSPEND,
    "screen-off": SCREEN_OFF, "screenoff": SCREEN_OFF,
    "screensaver": SCREEN_SAVER, "screen-saver": SCREEN_SAVER,
}

CONTROL_HELP = """commands:
  on | standby | suspend | screen-off | screensaver   set the power state
  wol                    deliver a magic packet by hand (no root needed)
  app <id>               what getForegroundAppInfo reports; 'hdmi2' expands
  fail <endpoint>=<form> inject a fault; 'fail none' clears them
  lag <seconds>          how long switchInput refuses after a wake
  loss <p> | close <p>   drop responses / hang up, with probability p
  state                  print what the TV looks like right now
  help | quit"""


def control_command(tv, text, stop):
    word, _, rest = text.partition(" ")
    rest = rest.strip()

    if word in CONTROL_STATES:
        # Going to a state the TV refuses TCP in closes the listener for real, exactly as a
        # command-driven transition would.
        tv.transition(CONTROL_STATES[word], cause="control")
    elif word == "wol":
        # Worth having even when udp 9 is bound: it is the only way to wake the TV as a normal
        # user, since binding a privileged port is what needs root, not receiving the packet.
        tv.receive_wol(tv.mac, "control")
    elif word == "app":
        if not rest:
            return "app needs an id, e.g. 'app hdmi2' or 'app com.webos.app.livetv'"
        tv.app_id = f"com.webos.app.{rest}" if rest.startswith("hdmi") else rest
        tv.journal("input", input="(control)", app_id=tv.app_id)
    elif word == "fail":
        if rest in ("none", "off", ""):
            tv.faults.clear()
            return "faults cleared"
        try:
            uri, fault = parse_fault(rest)
        except argparse.ArgumentTypeError as exc:
            return str(exc)
        tv.faults[uri] = fault
    elif word == "lag":
        try:
            tv.input_lag = float(rest)
        except ValueError:
            return f"lag needs a number of seconds, got {rest!r}"
    elif word in ("loss", "close"):
        try:
            value = float(rest)
        except ValueError:
            return f"{word} needs a probability between 0 and 1, got {rest!r}"
        if not 0.0 <= value <= 1.0:
            return f"{word} needs a probability between 0 and 1, got {value}"
        setattr(tv.timing, word, value)
    elif word == "state":
        faults = ", ".join(f"{short_uri(u)}={f}" for u, f in tv.faults.items()) or "none"
        return (f"state={tv.state}"
                f"{' [' + tv.processing + ']' if tv.processing else ''}  app={tv.app_id}  "
                f"faults: {faults}  lag={tv.input_lag}s  "
                f"loss={tv.timing.loss} close={tv.timing.close}")
    elif word in ("help", "?"):
        return CONTROL_HELP
    elif word in ("quit", "exit"):
        stop.set()
        return "stopping"
    else:
        return f"unknown command {word!r} - type 'help'"
    return None


async def control_reader(tv, stop):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while not stop.is_set():
        raw = await reader.readline()
        if not raw:  # Ctrl-D. The TV keeps running; Ctrl-C or 'quit' stops it.
            tv.journal("control-eof", note="stdin closed - the TV is still running")
            return
        text = raw.decode(errors="replace").strip()
        if not text:
            continue
        tv.journal("control", command=text)
        note = control_command(tv, text, stop)
        if note is not None:
            tv.journal("control-error", note=note)


async def start_wol_listener(tv, args):
    """Bind UDP for magic packets. Port 9 is privileged, so this may legitimately fail."""
    loop = asyncio.get_running_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: WolProtocol(tv), local_addr=(args.host, args.wol_port))
    except PermissionError:
        # Not fatal: everything except the wake-from-standby path still works, and the rig
        # reports the cases it had to skip rather than pretending they passed.
        tv.journal("wol-bind-denied", port=args.wol_port,
                   note="ports below 1024 need root; wake-on-LAN cases cannot run")
        return None
    except OSError as exc:
        tv.journal("wol-bind-failed", port=args.wol_port, error=str(exc))
        return None
    tv.journal("wol-listening", host=args.host, port=args.wol_port, mac=tv.mac)
    return transport


# --- TLS ----------------------------------------------------------------------------------

def ensure_cert():
    """Self-signed is enough: the client sets check_hostname=False and CERT_NONE."""
    cert, key = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if not (cert.exists() and key.exists()):
        CERT_DIR.mkdir(exist_ok=True)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
             "-out", str(cert), "-days", "3650", "-nodes", "-subj", "/CN=virtual-webos-tv"],
            check=True, capture_output=True,
        )
        key.chmod(0o600)
    return cert, key


# --- Entry point --------------------------------------------------------------------------

def parse_fault(value):
    name, _, fault = value.partition("=")
    if name not in ENDPOINT_ALIASES:
        raise argparse.ArgumentTypeError(
            f"unknown endpoint {name!r}, pick one of {', '.join(ENDPOINT_ALIASES)}")
    if fault not in FAULTS:
        raise argparse.ArgumentTypeError(
            f"unknown fault {fault!r}, pick one of {', '.join(FAULTS)}")
    return ENDPOINT_ALIASES[name], fault


def parse_states(value):
    if not value:
        return ()
    states = tuple(s.strip() for s in value.split(","))
    unknown = [s for s in states if s not in POWER_STATES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown state(s) {', '.join(unknown)}; observed states are {', '.join(POWER_STATES)}")
    return states


def build_parser():
    parser = argparse.ArgumentParser(
        prog="virtual_webos_tv.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="A test double for an LG webOS TV's SSAP control surface (stages 1-2).",
        epilog="States are the ones observed on real hardware; no others can be set.")
    # Loopback by default and by intent: nothing here should ever be reachable by something
    # that believes it is talking to the real TV.
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=3001,
                        help="bscpylgtv uses 3001/wss unless without_ssl is set (default 3001)")
    parser.add_argument("--app-id", default="com.webos.app.hdmi1",
                        help="what getForegroundAppInfo reports (default com.webos.app.hdmi1)")
    parser.add_argument("--power-state", default=ACTIVE, choices=POWER_STATES, metavar="STATE",
                        help=f"starting power state (default {ACTIVE}); one of: "
                             f"{', '.join(POWER_STATES)}")
    parser.add_argument("--offline-states", type=parse_states,
                        default=DEFAULT_OFFLINE_STATES, metavar="STATE[,STATE]",
                        help=f"states in which TCP is refused (default {SUSPEND}); "
                             f"pass '' to answer in every state")
    parser.add_argument("--wake-seconds", type=float, metavar="N",
                        help="override the observed wake times (4s from Active Standby, 5s "
                             "from Suspend)")
    parser.add_argument("--input-lag-seconds", type=float, default=0.0, metavar="N",
                        help="how long switchInput keeps failing after a wake (default 0)")
    # A locally administered address that belongs to no device. send_wol() also broadcasts to
    # 255.255.255.255, which does leave this machine - a packet for a MAC nothing owns is inert,
    # but never put a real TV's MAC here.
    parser.add_argument("--mac", default="02:00:00:00:00:01",
                        help="MAC this TV wakes on (default 02:00:00:00:00:01)")
    parser.add_argument("--wol-port", type=int, default=9,
                        help="UDP port for magic packets (default 9, which needs root)")
    parser.add_argument("--ignore-wol", action="store_true",
                        help="receive magic packets but stay in standby (a TV that never wakes)")
    parser.add_argument("--refuse-pairing", action="store_true",
                        help="deny the pairing prompt; a keyless client then gets rc 3")
    parser.add_argument("--error", dest="errors", action="append", default=[],
                        type=parse_fault, metavar="ENDPOINT=FAULT",
                        help=f"inject a fault. ENDPOINT: {', '.join(ENDPOINT_ALIASES)}. "
                             f"FAULT: {', '.join(FAULTS)}")
    parser.add_argument("--journal", type=Path,
                        help="append one JSON object per event to this file")
    # Both default to what the terminal says, so running this by hand needs no flags and the
    # rigs - whose streams are pipes - get machine-readable output and no control channel.
    parser.add_argument("--pretty", action=argparse.BooleanOptionalAction, default=None,
                        help="human-readable stderr (default: on when stderr is a terminal)")
    parser.add_argument("--control", action=argparse.BooleanOptionalAction, default=None,
                        help="accept commands on stdin (default: on when stdin is a terminal)")

    timing = parser.add_argument_group(
        "timing and loss (stage 3)",
        "Only --latency-handshake can produce an error: it is the one delay inside the "
        "client's 2s connect timeout. The other two, and --loss, produce a hang instead.")
    timing.add_argument("--calibrated", action="store_true",
                        help=f"use the times measured on real hardware: "
                             f"{CALIBRATED_HANDSHAKE_MS}ms handshake, "
                             f"{CALIBRATED_COMMAND_MS}ms per command")
    timing.add_argument("--latency-handshake", type=float, metavar="MS",
                        help="delay the WebSocket opening handshake (default 0)")
    timing.add_argument("--latency-pairing", type=float, metavar="MS",
                        help="delay the pairing reply; no client timeout covers this (default 0)")
    timing.add_argument("--latency-command", type=float, metavar="MS",
                        help="delay every command response (default 0)")
    timing.add_argument("--jitter", type=float, default=0.0, metavar="MS",
                        help="symmetric spread added to each non-zero delay, clamped at 0")
    timing.add_argument("--loss", type=float, default=0.0, metavar="P",
                        help="probability of silently dropping a response - a hang, not an error")
    timing.add_argument("--close", type=float, default=0.0, metavar="P",
                        help="probability of hanging up instead of answering - gives rc 2")
    timing.add_argument("--seed", type=int, default=1,
                        help="seed for --jitter, --loss and --close (default 1, so runs repeat)")
    return parser


def resolve_output(args):
    if args.pretty is None:
        args.pretty = sys.stderr.isatty()
    if args.control is None:
        args.control = sys.stdin.isatty()
    return args


def resolve_timing(args):
    """--calibrated fills in only what was not asked for explicitly."""
    defaults = {"latency_handshake": CALIBRATED_HANDSHAKE_MS,
                "latency_command": CALIBRATED_COMMAND_MS,
                "latency_pairing": 0.0} if args.calibrated else {}
    for name in ("latency_handshake", "latency_pairing", "latency_command"):
        if getattr(args, name) is None:
            setattr(args, name, defaults.get(name, 0.0))
    for name in ("loss", "close"):
        if not 0.0 <= getattr(args, name) <= 1.0:
            sys.exit(f"--{name} must be a probability between 0 and 1")
    return args


async def run(args):
    journal = Journal(args.journal, pretty=args.pretty)
    tv = VirtualWebOsTv(args, journal)

    cert, key = ensure_cert()
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert, key)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    # The banner heads the log, so anything the WoL bind has to say lands under it rather
    # than above it. The rigs wait for this event, so it must still precede the listener.
    journal("ready", state=tv.state, app_id=tv.app_id, mac=tv.mac,
            offline_states=list(tv.offline_states), faults=dict(tv.faults),
            refuse_pairing=tv.refuse_pairing, control=args.control,
            timing={"handshake_ms": args.latency_handshake,
                    "pairing_ms": args.latency_pairing,
                    "command_ms": args.latency_command,
                    "jitter_ms": args.jitter, "loss": args.loss, "close": args.close,
                    "seed": args.seed})
    control = asyncio.create_task(control_reader(tv, stop)) if args.control else None
    wol = await start_wol_listener(tv, args)
    try:
        await transport_supervisor(tv, args, ssl_context, stop)
    finally:
        if wol is not None:
            wol.close()
        if control is not None:
            control.cancel()
        if tv.waking is not None:
            tv.waking.cancel()

    journal("stopped", connections=tv.connections, state=tv.state)
    journal.close()


def main():
    args = resolve_output(resolve_timing(build_parser().parse_args()))
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
