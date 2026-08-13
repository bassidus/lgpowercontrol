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


# The loop logic, driven a tick at a time against a fake clock. None of this was reachable while
# the state lived in main()'s locals: every case below would have needed a real ten minutes, a
# real suspend, or both. As in the cli suite, the recorded commands are the assertion surface -
# the watcher returns nothing, and what it asked lgpowercontrol to do is the whole test.
class DpmsWatcherCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # A scratch path, never /run: the tests must not touch a live suspend cycle or need root.
        self.sleep_flag = Path(tmp.name) / "sleep"
        self.commands: list[str] = []
        self.log_lines: list[str] = []
        self.sleeping = False
        for target, replacement in (
            ("SLEEP_FLAG", self.sleep_flag),
            ("run_lgpc", self.commands.append),
            ("log", self.log_lines.append),
            ("preparing_for_sleep", lambda: self.sleeping),
        ):
            patch = mock.patch.object(monitor, target, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def watcher(self, initial: str = "on") -> monitor.DpmsWatcher:
        return monitor.DpmsWatcher(initial, 0.0)

    # Steps of 10s, inclusive of both ends: small enough that an ordinary run of ticks is never
    # mistaken for the >30s gap that means the machine was suspended.
    def ticks(self, watcher, state: str, start: float, end: float, step: float = 10.0) -> None:
        now = start
        while now <= end:
            watcher.tick(now, state)
            now += step

    def assertLogged(self, fragment: str) -> None:
        self.assertTrue(
            any(fragment in line for line in self.log_lines),
            f"{fragment!r} not in {self.log_lines}",
        )


class DpmsTransitionTest(DpmsWatcherCase):
    def test_the_screen_going_off_turns_the_screen_off(self) -> None:
        self.watcher().tick(0.0, "off")
        self.assertEqual(self.commands, ["SCREEN_OFF"])

    def test_the_screen_coming_back_turns_the_tv_on(self) -> None:
        self.watcher("off").tick(0.0, "on")
        self.assertEqual(self.commands, ["ON"])

    def test_the_same_state_twice_is_not_a_transition(self) -> None:
        watcher = self.watcher()
        self.ticks(watcher, "off", 0.0, 30.0)
        self.assertEqual(self.commands, ["SCREEN_OFF"])

    # "" means no connected output, e.g. mid-hotplug - not a screen that went dark. It must not
    # count as a transition, and it must not overwrite what the last real reading was.
    def test_an_unknown_reading_is_ignored_and_does_not_become_the_previous_state(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "")
        self.assertEqual(self.commands, [])
        watcher.tick(1.0, "on")  # still "on" from before, so this is not a transition either
        self.assertEqual(self.commands, [])
        watcher.tick(2.0, "off")
        self.assertEqual(self.commands, ["SCREEN_OFF"])

    # The sleep path owns the TV-off during a suspend; a second one from here would race it.
    def test_a_suspend_in_progress_leaves_the_screen_off_to_the_sleep_path(self) -> None:
        self.sleeping = True
        self.watcher().tick(0.0, "off")
        self.assertEqual(self.commands, [])
        self.assertLogged("handled by the sleep path")

    # A flag left behind by a suspend that never cleared it would suppress every escalation from
    # then on, so an off transition with no suspend running is what clears it.
    def test_a_stale_sleep_flag_is_cleared_on_an_ordinary_screen_off(self) -> None:
        self.sleep_flag.touch()
        self.watcher().tick(0.0, "off")
        self.assertFalse(self.sleep_flag.exists())
        self.assertEqual(self.commands, ["SCREEN_OFF"])
        self.assertLogged("Stale sleep flag removed")

    def test_a_real_suspend_keeps_its_flag(self) -> None:
        self.sleeping = True
        self.sleep_flag.touch()
        self.watcher().tick(0.0, "off")
        self.assertTrue(self.sleep_flag.exists())


class DpmsEscalationTest(DpmsWatcherCase):
    # The whole point of the escalation: power_off lands the TV in Always Ready, screen-off alone
    # never does, and the TV's own timer drops it into deep standby a few minutes later.
    def test_ten_minutes_of_screen_off_escalates_to_a_full_power_off(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.ticks(watcher, "off", 10.0, 590.0)
        self.assertEqual(self.commands, ["SCREEN_OFF"])
        self.ticks(watcher, "off", 600.0, 900.0)
        self.assertEqual(self.commands, ["SCREEN_OFF", "OFF"])

    def test_the_escalation_fires_once_and_not_every_tick_after(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.ticks(watcher, "off", 10.0, 1800.0)
        self.assertEqual(self.commands.count("OFF"), 1)

    def test_the_screen_coming_back_before_the_deadline_cancels_it(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.ticks(watcher, "off", 10.0, 300.0)
        watcher.tick(310.0, "on")
        self.ticks(watcher, "on", 320.0, 1200.0)
        self.assertEqual(self.commands, ["SCREEN_OFF", "ON"])

    # And the countdown restarts from scratch, rather than resuming where it left off.
    def test_a_second_screen_off_starts_a_fresh_countdown(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.ticks(watcher, "off", 10.0, 300.0)
        watcher.tick(310.0, "on")
        watcher.tick(320.0, "off")
        self.ticks(watcher, "off", 330.0, 900.0)  # 580s into the second countdown
        self.assertEqual(self.commands, ["SCREEN_OFF", "ON", "SCREEN_OFF"])
        self.ticks(watcher, "off", 910.0, 930.0)
        self.assertEqual(self.commands[-1], "OFF")

    # A suspended machine does not accrue screen-off time: the screen was off the whole way, but
    # the TV was never left showing a static image, and waking to a TV that just turned itself
    # off would be the visible bug.
    def test_a_clock_jump_restarts_the_countdown_instead_of_firing_on_resume(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.ticks(watcher, "off", 10.0, 60.0)
        watcher.tick(4000.0, "off")  # resumed over an hour later
        self.assertEqual(self.commands, ["SCREEN_OFF"])
        self.ticks(watcher, "off", 4010.0, 4590.0)
        self.assertEqual(self.commands, ["SCREEN_OFF"])
        self.ticks(watcher, "off", 4600.0, 4620.0)
        self.assertEqual(self.commands, ["SCREEN_OFF", "OFF"])

    # The safety net for a suspend that started between two ticks. Not the suspend gate - that one
    # reads logind - so it only has to hold the escalation back while the flag is there.
    def test_the_sleep_flag_holds_the_escalation_back_while_it_exists(self) -> None:
        watcher = self.watcher()
        watcher.tick(0.0, "off")
        self.sleep_flag.touch()
        self.ticks(watcher, "off", 10.0, 900.0)
        self.assertEqual(self.commands, ["SCREEN_OFF"])
        self.sleep_flag.unlink()
        watcher.tick(910.0, "off")
        self.assertEqual(self.commands, ["SCREEN_OFF", "OFF"])


if __name__ == "__main__":
    unittest.main()
