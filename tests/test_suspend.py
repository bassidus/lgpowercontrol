# The three suspend entry points. Their guards are not duplication - each path has to detect that
# another one already handled this suspend - and that is exactly what no VM can test: a guest
# never wakes, so the dedupe has only ever been seen on real hardware, one path at a time. Here
# the flags are files in a scratch directory and every path can be walked in both orders.
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import suspend


class SuspendCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.sleep_flag = self.tmp / "sleep"
        self.hook_flag = self.tmp / "hook-sleep"
        self.tv_off_flag = self.tmp / "tv-off"
        self.commands: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.detached: list[tuple] = []

    def _record(self, cmd, **kwargs):
        self.commands.append([str(part) for part in cmd])
        self.environments.append(dict(kwargs.get("env") or {}))
        return mock.Mock(returncode=0)

    def _record_detached(self, *args, env=None):
        self.detached.append(([str(a) for a in args], env))

    # Runs one entry point with the flags, logging, D-Bus and every child process replaced.
    def run_path(self, entry, argv: list[str], *, sleeping: bool = True):
        with (
            mock.patch.object(suspend.sys, "argv", ["lgpowercontrol", *argv]),
            mock.patch.object(suspend, "SLEEP_FLAG", self.sleep_flag),
            mock.patch.object(suspend, "HOOK_SLEEP_FLAG", self.hook_flag),
            mock.patch.object(suspend, "TV_OFF_FLAG", self.tv_off_flag),
            mock.patch.object(suspend, "LGPC_BIN", "/opt/lgpowercontrol/bin/lgpowercontrol"),
            mock.patch.object(suspend, "Logger", return_value=mock.Mock()),
            mock.patch.object(suspend, "preparing_for_sleep", return_value=sleeping),
            mock.patch.object(suspend, "run_detached", side_effect=self._record_detached),
            mock.patch.object(suspend.subprocess, "run", side_effect=self._record),
        ):
            return entry()

    def assertTurnedTvOff(self) -> None:
        self.assertEqual(len(self.commands), 1, f"expected one TV command, got {self.commands}")
        self.assertEqual(self.commands[0][-1], "OFF")

    def assertDidNothing(self) -> None:
        self.assertEqual(self.commands, [])
        self.assertEqual(self.detached, [])


class DispatcherTest(SuspendCase):
    def test_pre_down_turns_the_tv_off_and_claims_the_suspend(self) -> None:
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"])
        self.assertTurnedTvOff()
        self.assertTrue(self.sleep_flag.exists())

    # The dispatcher fires on every link event on every interface, not only at suspend. logind is
    # what tells the two apart.
    def test_a_link_going_down_outside_a_suspend_is_ignored(self) -> None:
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"], sleeping=False)
        self.assertDidNothing()
        self.assertFalse(self.sleep_flag.exists())

    # pre-down fires once per NIC; only the first may act. NM runs dispatcher scripts serially,
    # so this check cannot race.
    def test_a_second_interface_does_not_turn_the_tv_off_again(self) -> None:
        self.sleep_flag.touch()
        self.run_path(suspend.dispatcher, ["wlan0", "pre-down"])
        self.assertDidNothing()

    def test_up_turns_the_tv_back_on_and_clears_the_flag(self) -> None:
        self.sleep_flag.touch()
        self.run_path(suspend.dispatcher, ["eno1", "up"])
        self.assertEqual(self.detached[0][0][-1], "ON")
        self.assertEqual(self.detached[0][1], {"LGPC_SOURCE": "resume"})
        self.assertFalse(self.sleep_flag.exists())

    # 'up' also fires at boot and on a cable replug, where there is nothing to turn back on.
    def test_up_without_a_preceding_suspend_is_a_no_op(self) -> None:
        self.run_path(suspend.dispatcher, ["eno1", "up"])
        self.assertDidNothing()

    def test_other_dispatcher_events_are_ignored(self) -> None:
        for action in ("down", "pre-up", "dhcp4-change", ""):
            with self.subTest(action=action):
                self.run_path(suspend.dispatcher, ["eno1", action])
                self.assertDidNothing()

    # The dispatcher's own turn-off must not be blocked by the flag it is about to set.
    def test_the_wake_uses_the_dispatcher_flag_not_the_hook_one(self) -> None:
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"])
        self.assertTrue(self.sleep_flag.exists())
        self.assertFalse(self.hook_flag.exists())


class SleepHookTest(SuspendCase):
    # The hook covers NIC-WoL setups, where NM skips the device at sleep entirely and the
    # dispatcher never fires at all.
    def test_pre_turns_the_tv_off_when_the_dispatcher_did_not(self) -> None:
        self.run_path(suspend.hook, ["pre", "suspend"])
        self.assertTurnedTvOff()
        self.assertTrue(self.hook_flag.exists())

    def test_pre_stands_down_when_the_dispatcher_already_handled_this_suspend(self) -> None:
        self.sleep_flag.touch()
        self.run_path(suspend.hook, ["pre", "suspend"])
        self.assertDidNothing()
        self.assertFalse(self.hook_flag.exists())

    # There is no 'up' event on this path, so the hook owns the wake too - hence its own flag.
    def test_post_turns_the_tv_back_on(self) -> None:
        self.hook_flag.touch()
        self.run_path(suspend.hook, ["post", "suspend"])
        self.assertEqual(self.detached[0][0][-1], "ON")
        self.assertEqual(self.detached[0][1], {"LGPC_SOURCE": "sleep-hook"})
        self.assertFalse(self.hook_flag.exists())

    def test_post_does_nothing_when_the_dispatcher_owned_the_suspend(self) -> None:
        # The dispatcher's own 'up' handles that case; acting here as well would be a second ON.
        self.sleep_flag.touch()
        self.run_path(suspend.hook, ["post", "suspend"])
        self.assertDidNothing()

    # A dead network must not hold up suspend, so this path caps the connect attempts.
    def test_the_off_command_is_given_a_single_attempt(self) -> None:
        self.run_path(suspend.hook, ["pre", "suspend"])
        self.assertIn("--retries", self.commands[0])
        self.assertEqual(self.commands[0][self.commands[0].index("--retries") + 1], "1")

    # The dispatcher uses the lgpowercontrol default instead: it runs with the network still up.
    def test_the_dispatcher_does_not_cap_the_attempts(self) -> None:
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"])
        self.assertNotIn("--retries", self.commands[0])


class AlreadyOffTest(SuspendCase):
    # The monitor's 10-minute escalation may have turned the TV off long before the suspend. This
    # is the common case on a machine that suspends after idling, not the exception.
    def test_a_tv_that_is_already_off_is_left_alone(self) -> None:
        self.tv_off_flag.touch()
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"])
        self.assertEqual(self.commands, [])

    # The flag still has to be claimed, or the wake never fires.
    def test_the_wake_is_still_armed_for_a_tv_that_was_already_off(self) -> None:
        self.tv_off_flag.touch()
        self.run_path(suspend.dispatcher, ["eno1", "pre-down"])
        self.assertTrue(self.sleep_flag.exists())


class SourceTagTest(SuspendCase):
    # LGPC_SOURCE is what names the entry point in the journal, and it is also what the
    # POWER_OFF_AT_* gating reads - a wrong tag there silently ungates a disabled event.
    def test_each_path_tags_its_own_off_command(self) -> None:
        for entry, argv, expected in (
            (suspend.dispatcher, ["eno1", "pre-down"], "nm-dispatcher"),
            (suspend.hook, ["pre", "suspend"], "sleep-hook"),
        ):
            with self.subTest(source=expected):
                self.setUp()
                self.run_path(entry, argv)
                self.assertEqual(self.environments[0]["LGPC_SOURCE"], expected)


if __name__ == "__main__":
    unittest.main()
