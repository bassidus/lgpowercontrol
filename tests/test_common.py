import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import common

REPO_CONF = Path(__file__).resolve().parent.parent / "lgpowercontrol.conf"


# Gives each test a scratch directory to write conf files into.
class ConfCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def conf_file(self, text: str) -> Path:
        path = self.tmp / "lgpowercontrol.conf"
        path.write_text(text)
        return path


class LoadConfTest(ConfCase):
    def test_reads_quoted_values(self) -> None:
        conf = common.load_conf(self.conf_file('LGTV_IP="10.0.0.5"\nHDMI_INPUT="2"\n'))
        self.assertEqual(conf, {"LGTV_IP": "10.0.0.5", "HDMI_INPUT": "2"})

    def test_skips_comments_blank_lines_and_prose(self) -> None:
        conf = common.load_conf(self.conf_file(
            "# a comment\n"
            "\n"
            'LOGGING="1" # 1 = enabled | 0 = disabled\n'
            "# Run journalctl -t lgpowercontrol to see them.\n"
        ))
        self.assertEqual(conf, {"LOGGING": "1"})

    def test_keeps_whitespace_inside_quotes(self) -> None:
        # The trap behind shared_tv_app_id()'s strip(): shlex splits on unquoted whitespace only,
        # so a value the user padded inside the quotes arrives padded.
        conf = common.load_conf(self.conf_file('HDMI_INPUT="2 "\n'))
        self.assertEqual(conf["HDMI_INPUT"], "2 ")

    def test_empty_value_is_an_empty_string_not_a_missing_key(self) -> None:
        conf = common.load_conf(self.conf_file('HDMI_INPUT=""\n'))
        self.assertEqual(conf, {"HDMI_INPUT": ""})

    def test_last_definition_wins(self) -> None:
        conf = common.load_conf(self.conf_file('LGTV_IP="10.0.0.5"\nLGTV_IP="10.0.0.6"\n'))
        self.assertEqual(conf["LGTV_IP"], "10.0.0.6")


class ConfIntTest(unittest.TestCase):
    def test_missing_empty_and_non_numeric_fall_back(self) -> None:
        for conf in ({}, {"K": ""}, {"K": "abc"}, {"K": "12s"}):
            with self.subTest(conf=conf):
                self.assertEqual(common.conf_int(conf, "K", 42), 42)

    def test_negative_falls_back(self) -> None:
        # "-5".isdigit() is False, which is what keeps a negative timeout out of the code.
        self.assertEqual(common.conf_int({"K": "-5"}, "K", 42), 42)

    def test_padded_value_falls_back(self) -> None:
        # conf_int does not strip. Only a value padded inside the quotes can get here, and a
        # fallback to the default is the safe reading of one.
        self.assertEqual(common.conf_int({"K": " 5"}, "K", 42), 42)

    def test_zero_means_default_unless_allowed(self) -> None:
        self.assertEqual(common.conf_int({"K": "0"}, "K", 42), 42)

    def test_zero_is_kept_when_allowed(self) -> None:
        # OFF_WARNING_SECONDS="0" is the documented way to switch the warning off; it once read
        # as "unset" and silently restored the default. Never let zero mean default here again.
        self.assertEqual(common.conf_int({"K": "0"}, "K", 42, allow_zero=True), 0)

    def test_plain_number_is_parsed(self) -> None:
        self.assertEqual(common.conf_int({"K": "7"}, "K", 42), 7)


class LoggerTest(ConfCase):
    def build(self, conf_text: str | None):
        path = self.conf_file(conf_text) if conf_text is not None else self.tmp / "absent.conf"
        with mock.patch.object(common, "CONF_FILE", path), mock.patch.object(common, "syslog"):
            return common.Logger("test")

    def test_enabled_by_one(self) -> None:
        for value in ('LOGGING="1"', 'LOGGING=" 1 "'):
            with self.subTest(value=value):
                self.assertTrue(self.build(value + "\n").enabled)

    # "on" is among the disabled values on purpose: it is what confs written before 4.2 said, and
    # an update is a fresh clone with a newly filled in conf, never an old file carried across.
    def test_disabled_by_zero_and_by_a_missing_key(self) -> None:
        for text in ('LOGGING="0"\n', 'LOGGING=""\n', 'LOGGING="on"\n', 'LGTV_IP="10.0.0.5"\n'):
            with self.subTest(text=text):
                self.assertFalse(self.build(text).enabled)

    def test_unreadable_conf_logs_without_being_asked(self) -> None:
        # The one case that logs unasked: the program cannot work at all then, and that line is
        # what says so.
        self.assertTrue(self.build(None).enabled)

    def test_disabled_logger_writes_nothing(self) -> None:
        path = self.conf_file('LOGGING="0"\n')
        with mock.patch.object(common, "CONF_FILE", path), mock.patch.object(common, "syslog") as sl:
            common.Logger("test")("a message")
        sl.syslog.assert_not_called()

    def test_enabled_logger_writes_a_tagged_line(self) -> None:
        path = self.conf_file('LOGGING="1"\n')
        with mock.patch.object(common, "CONF_FILE", path), mock.patch.object(common, "syslog") as sl:
            common.Logger("dpms-monitor")("a message")
        self.assertEqual(sl.syslog.call_args.args[1], "dpms-monitor: a message")


class NmcliTest(unittest.TestCase):
    def run_nmcli(self, *, rc: int = 0, stdout: str = "", stderr: str = "", check: bool = False,
                  missing: bool = False):
        result = mock.Mock(returncode=rc, stdout=stdout, stderr=stderr)
        side_effect = FileNotFoundError() if missing else None
        with mock.patch.object(common.subprocess, "run", return_value=result,
                               side_effect=side_effect):
            return common.nmcli("-g", "DEVICE", "device", "status", check=check)

    def test_output_is_stripped(self) -> None:
        self.assertEqual(self.run_nmcli(stdout="eno1\n"), "eno1")

    def test_failure_is_an_empty_string_by_default(self) -> None:
        self.assertEqual(self.run_nmcli(rc=1, stderr="boom"), "")

    def test_missing_binary_is_an_empty_string_by_default(self) -> None:
        self.assertEqual(self.run_nmcli(missing=True), "")

    def test_check_turns_a_failure_into_an_exit(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.run_nmcli(rc=1, stderr="boom", check=True)
        self.assertEqual(str(caught.exception), "boom")

    def test_check_reports_a_missing_binary(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_nmcli(missing=True, check=True)


class WiredLookupTest(unittest.TestCase):
    def patch_nmcli(self, *outputs: str):
        patcher = mock.patch.object(common, "nmcli", side_effect=outputs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_wired_devices_keeps_only_ethernet(self) -> None:
        self.patch_nmcli("eno1:ethernet\nwlan0:wifi\nbr0:bridge\ndummy0:ethernet")
        self.assertEqual(common.wired_devices(), ["eno1", "dummy0"])

    def test_connection_for_maps_the_placeholder_to_empty(self) -> None:
        self.patch_nmcli("--")
        self.assertEqual(common.connection_for("eno1"), "")

    def test_sole_wired_connection_returns_the_pair(self) -> None:
        self.patch_nmcli("eno1:ethernet", "Wired connection 1")
        self.assertEqual(common.sole_wired_connection(), ("eno1", "Wired connection 1"))

    def test_sole_wired_connection_is_none_without_a_card(self) -> None:
        self.patch_nmcli("wlan0:wifi")
        self.assertIsNone(common.sole_wired_connection())

    def test_sole_wired_connection_is_none_with_several_cards(self) -> None:
        self.patch_nmcli("eno1:ethernet\nenp2s0:ethernet")
        self.assertIsNone(common.sole_wired_connection())

    def test_sole_wired_connection_is_none_without_an_active_connection(self) -> None:
        self.patch_nmcli("eno1:ethernet", "--")
        self.assertIsNone(common.sole_wired_connection())


class NotifyTest(unittest.TestCase):
    def test_notify_send_returns_the_notification_id(self) -> None:
        with mock.patch.object(common, "busctl", return_value="u 42\n"):
            self.assertEqual(common.notify_send("summary", "body"), 42)

    def test_notify_send_returns_zero_when_the_call_says_nothing(self) -> None:
        with mock.patch.object(common, "busctl", return_value=""):
            self.assertEqual(common.notify_send("summary", "body"), 0)

    def test_notify_close_skips_the_call_for_a_zero_id(self) -> None:
        with mock.patch.object(common, "busctl") as busctl:
            common.notify_close(0)
        busctl.assert_not_called()

    def test_notify_close_passes_the_id_on(self) -> None:
        with mock.patch.object(common, "busctl", return_value="") as busctl:
            common.notify_close(42)
        self.assertIn("42", busctl.call_args.args)


class PreparingForSleepTest(unittest.TestCase):
    def test_reads_the_logind_property(self) -> None:
        for out, expected in (("b true\n", True), ("b false\n", False), ("", False)):
            with self.subTest(out=out), mock.patch.object(common, "busctl", return_value=out):
                self.assertIs(common.preparing_for_sleep(), expected)


class RunDetachedTest(unittest.TestCase):
    def test_environment_is_passed_as_setenv_flags(self) -> None:
        with mock.patch.object(common.subprocess, "run") as run:
            common.run_detached("/opt/lgpowercontrol/bin/lgpowercontrol", "ON",
                                env={"LGPC_SOURCE": "resume"})
        self.assertEqual(
            run.call_args.args[0],
            ["systemd-run", "--quiet", "--collect", "--setenv=LGPC_SOURCE=resume",
             "/opt/lgpowercontrol/bin/lgpowercontrol", "ON"],
        )


# The conf file this repo ships is what the installer copies over the installed one, so a key the
# code reads but the template lacks is a shipped bug: the feature silently falls back to its
# default with nothing for the user to edit.
class ShippedConfTest(unittest.TestCase):
    KEYS_READ_BY_THE_CODE = {
        "LGTV_IP", "LGTV_MAC", "HDMI_INPUT", "SHARED_TV",
        "POWER_OFF_AT_SUSPEND", "POWER_OFF_AT_SHUTDOWN",
        "OFF_WARNING_SECONDS", "NOTIFY_POLL_SECONDS", "LOGGING",
    }

    def setUp(self) -> None:
        self.conf = common.load_conf(REPO_CONF)

    def test_every_key_the_code_reads_is_present(self) -> None:
        self.assertEqual(self.KEYS_READ_BY_THE_CODE - set(self.conf), set())

    def test_no_key_beyond_the_ones_the_code_reads(self) -> None:
        self.assertEqual(set(self.conf) - self.KEYS_READ_BY_THE_CODE, set())

    def test_tv_address_is_shipped_empty_so_the_installer_stops(self) -> None:
        self.assertEqual(self.conf["LGTV_IP"], "")
        self.assertEqual(self.conf["LGTV_MAC"], "")

    def test_the_input_and_the_shared_tv_flag_are_shipped_disabled(self) -> None:
        self.assertEqual(self.conf["HDMI_INPUT"], "")
        self.assertEqual(self.conf["SHARED_TV"], "0")

    def test_the_off_events_are_shipped_enabled(self) -> None:
        self.assertEqual(self.conf["POWER_OFF_AT_SUSPEND"], "1")
        self.assertEqual(self.conf["POWER_OFF_AT_SHUTDOWN"], "1")

    def test_logging_is_shipped_off(self) -> None:
        self.assertNotEqual(self.conf["LOGGING"].strip(), "1")


if __name__ == "__main__":
    unittest.main()
