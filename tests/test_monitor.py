import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import monitor


class DpmsStateTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.drm = Path(tmp.name) / "drm"
        self.drm.mkdir()

    # outputs: (name, status, dpms) - dpms None leaves the file out, as a card mid-hotplug does.
    def state(self, outputs) -> str:
        for name, status, dpms in outputs:
            card = self.drm / name
            card.mkdir()
            card.joinpath("status").write_text(status + "\n")
            if dpms is not None:
                card.joinpath("dpms").write_text(dpms + "\n")
        with mock.patch.object(monitor, "Path", lambda _: self.drm):
            return monitor.get_dpms_state()

    def test_a_connected_output_that_is_on(self) -> None:
        self.assertEqual(self.state([("card1-DP-1", "connected", "On")]), "on")

    def test_a_connected_output_that_is_off(self) -> None:
        self.assertEqual(self.state([("card1-DP-1", "connected", "Off")]), "off")

    # Disconnected outputs are filtered out, and a machine has several: the Ubuntu VM has one
    # connected and three disconnected, which is the only place that filter is load-bearing.
    def test_disconnected_outputs_are_ignored(self) -> None:
        self.assertEqual(
            self.state([("card1-DP-1", "connected", "Off"), ("card1-HDMI-A-1", "disconnected", "Off")]),
            "off",
        )

    def test_any_output_being_on_means_on(self) -> None:
        self.assertEqual(
            self.state([("card1-DP-1", "connected", "Off"), ("card1-DP-2", "connected", "On")]),
            "on",
        )

    # "" rather than "off": no connected output at all is not a screen that went dark, and the
    # main loop only acts on a non-empty state.
    def test_no_connected_output_is_unknown_not_off(self) -> None:
        self.assertEqual(self.state([("card1-DP-1", "disconnected", "Off")]), "")

    def test_an_empty_drm_directory_is_unknown(self) -> None:
        self.assertEqual(self.state([]), "")

    def test_an_output_without_a_dpms_file_is_skipped(self) -> None:
        self.assertEqual(self.state([("card1-DP-1", "connected", None)]), "")


class MonitorBudgetTest(unittest.TestCase):
    # A tripwire: 600 has a measurement behind it, not a preference. The escalation has to land
    # inside the TV's ~13 min deep-standby timer - monitor.py has the mechanics.
    def test_the_escalation_stays_inside_the_deep_standby_timer(self) -> None:
        self.assertEqual(monitor.ESCALATE_AFTER_SECONDS, 600)
        self.assertLess(monitor.ESCALATE_AFTER_SECONDS, 13 * 60)


class RunLgpcTest(unittest.TestCase):
    def test_a_failing_command_is_logged(self) -> None:
        # The wrappers used to discard main()'s return value, so everything looked like a success
        # and this line could never fire.
        with mock.patch.object(monitor.subprocess, "run", return_value=mock.Mock(returncode=2)), \
             mock.patch.object(monitor, "log") as log:
            monitor.run_lgpc("OFF")
        self.assertIn("OFF failed", log.call_args.args[0])

    def test_a_successful_command_is_quiet(self) -> None:
        with mock.patch.object(monitor.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             mock.patch.object(monitor, "log") as log:
            monitor.run_lgpc("ON")
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
