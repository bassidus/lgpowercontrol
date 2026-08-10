import asyncio
import contextlib
import io
import unittest
from unittest import mock

import websockets.exceptions
from bscpylgtv.exceptions import (
    PyLGTVCmdError,
    PyLGTVCmdException,
    PyLGTVPairException,
    PyLGTVServiceNotFoundError,
)

from lgpowercontrol import cli
from tests.harness import CliCase, FakeTV

# Locally administered, so it belongs to no manufacturer and therefore to no device. The socket
# is replaced in every case below, but rig.py refuses any globally administered address for the
# same reason and the placeholder here should not be the odd one out.
MAC = "02:ab:cd:ef:00:01"
MAC_BYTES = bytes.fromhex("02abcdef0001")
# A documentation address, never a real TV: send_wol is exercised with the socket replaced, but
# the fake IP is a second line of defence in case a future edit lets a packet out for real.
TV_IP = "192.0.2.10"


class FakeSocket:
    def __init__(self, log: list) -> None:
        self.log = log
        self.broadcast = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def setsockopt(self, level, option, value) -> None:
        self.broadcast = bool(value)

    def sendto(self, data, dest) -> None:
        self.log.append((data, dest, self.broadcast))


class SendWolTest(unittest.TestCase):
    def send(self, conf: dict[str, str], fail: bool = False) -> list:
        log: list = []

        def factory(*args, **kwargs):
            if fail:
                raise OSError("ENETUNREACH")
            return FakeSocket(log)

        with mock.patch.object(cli, "CONF", conf), mock.patch.object(cli.socket, "socket", factory):
            cli.send_wol()
        return log

    def test_packet_is_six_ff_bytes_then_the_mac_sixteen_times(self) -> None:
        packet = self.send({"LGTV_MAC": MAC})[0][0]
        self.assertEqual(packet, b"\xff" * 6 + MAC_BYTES * 16)
        self.assertEqual(len(packet), 102)

    def test_separator_free_and_dash_separated_macs_are_accepted(self) -> None:
        for written in (MAC, MAC.upper().replace(":", "-"), "02abcdef0001"):
            with self.subTest(mac=written):
                self.assertEqual(self.send({"LGTV_MAC": written})[0][0][6:12], MAC_BYTES)

    # Both copies matter and neither replaces the other: broadcast is the only reliable path on
    # the TV's own segment, where a sleeping TV will not answer ARP, and the routed unicast is
    # what reaches a TV on another subnet (#12), where webOS does answer ARP in standby.
    def test_broadcast_and_unicast_are_both_sent(self) -> None:
        sent = self.send({"LGTV_MAC": MAC, "LGTV_IP": TV_IP})
        self.assertEqual(
            [(dest, broadcast) for _, dest, broadcast in sent],
            [(("255.255.255.255", 9), True), ((TV_IP, 9), False)],
        )

    def test_an_empty_address_drops_the_unicast_copy(self) -> None:
        # An empty host would be sent to 0.0.0.0 rather than skipped, hence the explicit check.
        sent = self.send({"LGTV_MAC": MAC, "LGTV_IP": ""})
        self.assertEqual([dest for _, dest, _ in sent], [("255.255.255.255", 9)])

    def test_a_missing_or_malformed_mac_sends_nothing(self) -> None:
        for mac in ("", "zz:zz:zz:zz:zz:zz", "02:ab:cd", "02:ab:cd:ef:00:01:02"):
            with self.subTest(mac=mac):
                self.assertEqual(self.send({"LGTV_MAC": mac}), [])

    def test_a_failing_socket_is_swallowed(self) -> None:
        # Transient ENETUNREACH mid-resume: the wake loop resends every second, so this must not
        # take the whole wake down.
        self.assertEqual(self.send({"LGTV_MAC": MAC}, fail=True), [])


# tv_cmd() with the websocket client replaced. The mapping from library exception to exit code is
# the whole contract every caller reads, and several of these cannot be provoked on real hardware.
class TvCmdTest(unittest.TestCase):
    def call(self, command: str = "get_power_state", *, result=None, error=None, retries=None):
        recorded = {}

        class Client:
            async def connect(self_) -> None:
                pass

            async def disconnect(self_) -> None:
                recorded["disconnected"] = True

            def __getattr__(self_, name):
                async def invoke(*args):
                    recorded["command"] = (name, args)
                    if error is not None:
                        raise error
                    return result
                return invoke

        class Factory:
            @staticmethod
            async def create(**kwargs):
                recorded["create"] = kwargs
                return Client()

        with mock.patch.object(cli, "WebOsClient", Factory), mock.patch.object(cli, "log"):
            outcome = cli.tv_cmd(command, retries=retries)
        return outcome, recorded

    def test_success_returns_the_result(self) -> None:
        (rc, result, err), recorded = self.call(result={"state": "Active"})
        self.assertEqual((rc, result, err), (0, {"state": "Active"}, ""))
        self.assertEqual(recorded["command"], ("get_power_state", ()))

    def test_the_client_is_always_disconnected(self) -> None:
        _, recorded = self.call(error=OSError("boom"))
        self.assertTrue(recorded["disconnected"])

    def test_retries_reach_the_client(self) -> None:
        _, recorded = self.call(retries=1)
        self.assertEqual(recorded["create"]["connect_retry_attempts"], 1)

    # Raised with a plain string rather than the response dict, although it subclasses
    # PyLGTVCmdError. Caught in its own except before that one on purpose: the payload lookup
    # there would TypeError, and a TypeError raised inside an except block escapes the whole try,
    # uncatchable by the handlers under it. This is the case that used to reach the user as a
    # traceback. It cannot be provoked on a real TV.
    def test_service_not_found_is_an_error_not_a_traceback(self) -> None:
        (rc, _, err), _ = self.call(error=PyLGTVServiceNotFoundError("no such service"))
        self.assertEqual(rc, 1)
        self.assertEqual(err, "no such service")

    def test_a_command_error_reports_code_and_text(self) -> None:
        error = PyLGTVCmdError({"payload": {"errorCode": "-1000", "errorText": "denied"}})
        (rc, _, err), _ = self.call(error=error)
        self.assertEqual((rc, err), (1, "-1000 denied"))

    # -102 from turn_screen_on is ambiguous by design: it fires both when the screen is already
    # on and when the TV answers from Always Ready standby. The caller decides which, from
    # get_power_state - so this must stay a distinct code and never read as success on its own.
    def test_minus_102_from_turn_screen_on_is_its_own_code(self) -> None:
        error = PyLGTVCmdError({"payload": {"errorCode": "-102", "errorText": "already on"}})
        (rc, _, _), _ = self.call("turn_screen_on", error=error)
        self.assertEqual(rc, 102)

    def test_minus_102_from_any_other_command_is_a_plain_error(self) -> None:
        error = PyLGTVCmdError({"payload": {"errorCode": "-102", "errorText": "nope"}})
        (rc, _, _), _ = self.call("set_input", error=error)
        self.assertEqual(rc, 1)

    def test_a_pairing_failure_is_rc_3(self) -> None:
        # Only rc 3 means the key itself is broken; authorize() wipes the key on it, so nothing
        # else may ever land here.
        (rc, _, err), _ = self.call(error=PyLGTVPairException("denied"))
        self.assertEqual(rc, 3)
        self.assertIn("not paired", err)

    def test_a_generic_command_exception_is_rc_1(self) -> None:
        (rc, _, _), _ = self.call(error=PyLGTVCmdException("bad response"))
        self.assertEqual(rc, 1)

    def test_network_errors_are_rc_2(self) -> None:
        for error in (
            OSError(101, "Network is unreachable"),
            TimeoutError("timed out"),
            asyncio.TimeoutError("timed out"),
            websockets.exceptions.ConnectionClosedError(None, None),
        ):
            with self.subTest(error=type(error).__name__):
                (rc, _, err), _ = self.call(error=error)
                self.assertEqual(rc, 2)
                self.assertTrue(err.startswith("unreachable:"))

    # CancelledError subclasses BaseException, so it slipped past the catch-all and left main()
    # as a traceback with rc 1 - a network event reading as a program bug. It has to be named
    # explicitly to be caught at all, and only the virtual TV can provoke it.
    def test_a_connection_dropped_mid_command_is_rc_2(self) -> None:
        (rc, _, err), _ = self.call(error=asyncio.CancelledError())
        self.assertEqual(rc, 2)
        self.assertIn("unreachable", err)

    # A bug in this program must never read as network trouble, or the exit code sends the user
    # looking at their TV.
    def test_an_unexpected_exception_is_an_internal_error(self) -> None:
        (rc, _, err), _ = self.call(error=ValueError("bug"))
        self.assertEqual(rc, 1)
        self.assertIn("internal error", err)

    def test_library_chatter_stays_off_stdout(self) -> None:
        # bscpylgtv logs its connect retries to stdout; STATUS writes machine-readable lines there.
        class Noisy(Exception):
            pass

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.call(error=OSError("boom"))
            print("library noise", file=cli.sys.stdout)
        self.assertEqual(stdout.getvalue(), "library noise\n")


class SharedTvAppIdTest(unittest.TestCase):
    def app_id(self, value: str):
        with mock.patch.object(cli, "CONF", {"POWER_OFF_ONLY_ON_HDMI": value}), \
             mock.patch.object(cli, "log"):
            return cli.shared_tv_app_id()

    def test_an_input_number_becomes_a_webos_app_id(self) -> None:
        # Confirmed against real hardware: get_current_app answers 'com.webos.app.hdmi1'.
        self.assertEqual(self.app_id("1"), "com.webos.app.hdmi1")
        self.assertEqual(self.app_id("2"), "com.webos.app.hdmi2")

    def test_padding_inside_the_quotes_is_stripped(self) -> None:
        # load_conf keeps whitespace inside quotes, and "2 " once produced com.webos.app.hdmi2 ,
        # which matches nothing and therefore disabled power-off for good.
        self.assertEqual(self.app_id(" 2 "), "com.webos.app.hdmi2")

    def test_an_empty_value_is_the_off_switch(self) -> None:
        self.assertIsNone(self.app_id(""))

    # A mismatch is what makes the guard stand down, so a typo would otherwise disable power-off
    # permanently with a single log line as the only symptom. Anything that is not a plain input
    # number reads as "not configured" instead.
    def test_a_malformed_value_reads_as_not_configured(self) -> None:
        for value in ("HDMI_2", "hdmi2", "2a", "-1", "1.0", "two"):
            with self.subTest(value=value):
                self.assertIsNone(self.app_id(value))

    def test_zero_is_a_typo_not_a_disable(self) -> None:
        # There is no HDMI 0; the key is disabled by leaving it empty.
        self.assertIsNone(self.app_id("0"))

    def test_a_malformed_value_says_so_in_the_log(self) -> None:
        with mock.patch.object(cli, "CONF", {"POWER_OFF_ONLY_ON_HDMI": "HDMI_2"}), \
             mock.patch.object(cli, "log") as log:
            cli.shared_tv_app_id()
        self.assertIn("not an input number", log.call_args.args[0])

    def test_an_empty_value_stays_out_of_the_log(self) -> None:
        with mock.patch.object(cli, "CONF", {}), mock.patch.object(cli, "log") as log:
            cli.shared_tv_app_id()
        log.assert_not_called()


class DisabledOffEventTest(unittest.TestCase):
    def disabled_by(self, source: str, conf: dict[str, str]):
        with mock.patch.object(cli, "SOURCE", source), mock.patch.object(cli, "CONF", conf):
            return cli.disabled_off_event()

    def test_suspend_sources_read_the_suspend_key(self) -> None:
        for source in ("nm-dispatcher", "sleep-hook", "sleep-listener"):
            with self.subTest(source=source):
                self.assertEqual(
                    self.disabled_by(source, {"POWER_OFF_AT_SUSPEND": "0"}),
                    "POWER_OFF_AT_SUSPEND",
                )

    def test_shutdown_reads_its_own_key(self) -> None:
        self.assertEqual(
            self.disabled_by("shutdown", {"POWER_OFF_AT_SHUTDOWN": "0"}), "POWER_OFF_AT_SHUTDOWN"
        )

    def test_the_two_keys_do_not_cover_for_each_other(self) -> None:
        self.assertIsNone(self.disabled_by("shutdown", {"POWER_OFF_AT_SUSPEND": "0"}))
        self.assertIsNone(self.disabled_by("nm-dispatcher", {"POWER_OFF_AT_SHUTDOWN": "0"}))

    # A hand-typed `lgpowercontrol OFF` carries no source and is never gated - typing the command
    # is the request itself.
    def test_a_hand_typed_command_is_never_gated(self) -> None:
        self.assertIsNone(self.disabled_by("", {"POWER_OFF_AT_SUSPEND": "0"}))

    # The idle escalation is deliberately absent from the table: turning the TV off before it sits
    # on a static image is what this program exists for, and a shared TV is already covered there
    # by POWER_OFF_ONLY_ON_HDMI.
    def test_the_idle_escalation_is_not_gateable(self) -> None:
        self.assertIsNone(self.disabled_by("dpms-monitor", {"POWER_OFF_AT_SUSPEND": "0"}))

    # Only an explicit 0 disables. A typo, a missing key or a conf from a release before these
    # keys existed all leave today's behaviour in place - the safe direction, because the failure
    # mode is one press on the remote rather than a TV that silently never turns off again.
    def test_only_an_explicit_zero_disables(self) -> None:
        for value in ("1", "", "no", "off", "false", "00"):
            with self.subTest(value=value):
                self.assertIsNone(self.disabled_by("shutdown", {"POWER_OFF_AT_SHUTDOWN": value}))

    def test_a_missing_key_leaves_the_event_on(self) -> None:
        self.assertIsNone(self.disabled_by("shutdown", {}))

    def test_padding_inside_the_quotes_is_stripped(self) -> None:
        self.assertEqual(
            self.disabled_by("shutdown", {"POWER_OFF_AT_SHUTDOWN": " 0 "}), "POWER_OFF_AT_SHUTDOWN"
        )


# The four outcomes of check_power_off_guard, through main()'s OFF branch so the exit code and the
# TV traffic are both visible. Only two of the four have ever been seen on real hardware.
class PowerOffGuardTest(CliCase):
    GUARDED = {"POWER_OFF_ONLY_ON_HDMI": "1"}

    def test_no_guard_configured_costs_no_round_trip(self) -> None:
        tv = FakeTV()
        self.assertEqual(self.run_cli("OFF", tv), 0)
        self.assertEqual(tv.commands(), ["power_off"])

    def test_the_configured_input_lets_the_off_command_through(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi1")
        self.assertEqual(self.run_cli("OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app", "power_off"])

    # Any app that is not the configured input skips, not just another HDMI port - which is what
    # protects a shared TV showing Netflix as well as one showing another source.
    def test_another_app_stands_the_off_command_down(self) -> None:
        tv = FakeTV(current_app="com.webos.app.mediadiscovery")
        self.assertEqual(self.run_cli("OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app"])
        self.assertLogged("skipping off command")

    def test_another_hdmi_port_stands_the_off_command_down(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi2")
        self.assertEqual(self.run_cli("OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app"])

    # Propagated rather than swallowed so monitor.py still logs the failure. Exit 2 was what
    # power_off itself returned before the guard existed, so the pre-down loss looks the same.
    def test_an_unreachable_tv_skips_and_propagates_rc_2(self) -> None:
        tv = FakeTV(app_rc=2)
        self.assertEqual(self.run_cli("OFF", tv, self.GUARDED), 2)
        self.assertEqual(tv.commands(), ["get_current_app"])

    # Fail-open: a webOS that cannot answer which app is showing must not become a TV that never
    # turns off. Cannot be provoked on real hardware.
    def test_a_non_network_error_fails_open(self) -> None:
        tv = FakeTV(app_rc=1)
        self.assertEqual(self.run_cli("OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app", "power_off"])
        self.assertLogged("proceeding with off command")

    def test_screen_off_is_guarded_the_same_way(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi2")
        self.assertEqual(self.run_cli("SCREEN_OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app"])

    def test_screen_off_proceeds_on_the_configured_input(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi1")
        self.assertEqual(self.run_cli("SCREEN_OFF", tv, self.GUARDED), 0)
        self.assertEqual(tv.commands(), ["get_current_app", "turn_screen_off"])


class OffCommandTest(CliCase):
    # Before the guard on purpose: a disabled event must not spend a round trip asking the TV
    # anything, least of all inside the pre-down window.
    def test_a_disabled_event_asks_the_tv_nothing(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi1")
        conf = {"POWER_OFF_AT_SUSPEND": "0", "POWER_OFF_ONLY_ON_HDMI": "1"}
        self.assertEqual(self.run_cli("OFF", tv, conf, source="nm-dispatcher"), 0)
        self.assertEqual(tv.commands(), [])
        self.assertLogged("leaving the TV on")

    def test_a_disabled_event_leaves_the_flag_alone(self) -> None:
        # The flag says "the TV is already off"; setting it here would make the suspend path skip
        # a TV that is still on.
        tv = FakeTV()
        self.run_cli("OFF", tv, {"POWER_OFF_AT_SHUTDOWN": "0"}, source="shutdown")
        self.assertFalse(self.tv_off_flag.exists())

    def test_a_successful_off_leaves_the_flag_behind(self) -> None:
        # What lets the suspend path skip a redundant power_off.
        self.assertEqual(self.run_cli("OFF", FakeTV()), 0)
        self.assertTrue(self.tv_off_flag.exists())

    def test_a_skipped_off_leaves_no_flag(self) -> None:
        tv = FakeTV(current_app="com.webos.app.hdmi2")
        self.run_cli("OFF", tv, {"POWER_OFF_ONLY_ON_HDMI": "1"})
        self.assertFalse(self.tv_off_flag.exists())


class StatusCommandTest(CliCase):
    def status(self, tv: FakeTV):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.run_cli("STATUS", tv)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_a_plain_state_is_printed(self) -> None:
        rc, out, _ = self.status(FakeTV([{"state": "Active"}]))
        self.assertEqual((rc, out), (0, "state=Active\n"))

    def test_a_transition_prints_the_processing_field(self) -> None:
        rc, out, _ = self.status(FakeTV([{"state": "Suspend", "processing": "Screen On"}]))
        self.assertEqual((rc, out), (0, "state=Suspend\nprocessing=Screen On\n"))

    def test_an_unreachable_tv_exits_2_and_says_why_on_stderr(self) -> None:
        rc, out, err = self.status(FakeTV([2]))
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn("unreachable", err)


class ArgumentTest(CliCase):
    def test_an_unknown_command_exits_2(self) -> None:
        # argparse's own code for a usage error; monitor.py and the wrappers rely on it.
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.run_cli("SLEEP", FakeTV())
        self.assertEqual(caught.exception.code, 2)

    def test_retries_never_drops_below_one(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.run_cli("STATUS", FakeTV(), extra_argv=["--retries", "0"])
        self.assertEqual(cli.RETRIES, 1)


if __name__ == "__main__":
    unittest.main()
