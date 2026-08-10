# The ON path: the wake loop and the input switch. This is the part of cli.py that is hardest to
# exercise by hand - half of it only happens when a suspended computer and a sleeping TV come back
# in a particular order - and the loop's own comments carry measurements that should not be
# quietly undone, so the budgets are pinned here as well.
import fcntl
import os
import unittest

from lgpowercontrol import cli
from tests.harness import CliCase, FakeTV

AWAKE = {"state": "Active"}
STANDBY = {"state": "Active Standby"}      # Always Ready standby, after power_off
DEEP = {"state": "Suspend"}                # deep standby
WAKING = {"state": "Suspend", "processing": "Screen On"}

SHARED_TV = {"HDMI_INPUT": "1", "POWER_OFF_ONLY_ON_HDMI": "1"}
OWN_TV = {"HDMI_INPUT": "1"}


class WakeLoopTest(CliCase):
    def test_a_tv_that_is_already_awake_gets_its_screen_turned_on(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(tv.commands(), ["get_power_state", "turn_screen_on"])

    def test_a_magic_packet_goes_out_before_the_polling_starts(self) -> None:
        self.run_cli("ON", FakeTV([AWAKE]))
        self.assertEqual(self.wol_packets, 1)

    # The three states that mean the TV is awake. Everything else means the packet never landed.
    def test_screen_off_and_screen_saver_also_count_as_awake(self) -> None:
        for state in ("Active", "Screen Off", "Screen Saver"):
            with self.subTest(state=state):
                self.wol_packets = 0
                tv = FakeTV([{"state": state}])
                self.assertEqual(self.run_cli("ON", tv), 0)
                self.assertEqual(tv.commands(), ["get_power_state", "turn_screen_on"])
                self.assertEqual(self.wol_packets, 1)  # no resend needed

    def test_standby_resends_the_packet_until_the_tv_answers_awake(self) -> None:
        tv = FakeTV([STANDBY, DEEP, AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(tv.count("get_power_state"), 3)
        self.assertEqual(self.wol_packets, 3)  # the first one, plus one per standby answer

    # Unknown states fall through to the same treatment on purpose: resending is safe either way,
    # and a webOS release inventing a new name must not strand the wake.
    def test_an_unknown_state_is_treated_as_standby(self) -> None:
        tv = FakeTV([{"state": "Hibernating"}, AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(self.wol_packets, 2)
        self.assertLogged("TV in standby (Hibernating)")

    # Mid-transition: the state value cannot be trusted to say which standby the TV is leaving,
    # so the loop waits for a plain state rather than acting on this one - and sends no packet,
    # the earlier one has clearly landed.
    def test_a_transition_is_waited_out_without_another_packet(self) -> None:
        tv = FakeTV([WAKING, WAKING, AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(self.wol_packets, 1)
        self.assertEqual(tv.count("turn_screen_on"), 1)

    def test_an_unreachable_tv_is_retried_with_another_packet(self) -> None:
        tv = FakeTV([2, 2, AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(self.wol_packets, 3)

    # -102 from turn_screen_on is ambiguous, but the state above has already proven the TV awake,
    # so here it means the screen was on already. Reading it as failure would fail every wake of
    # a TV that never slept.
    def test_minus_102_with_the_tv_proven_awake_is_success(self) -> None:
        tv = FakeTV([AWAKE], screen_on_rc=102)
        self.assertEqual(self.run_cli("ON", tv), 0)

    def test_the_loop_gives_up_after_the_full_budget(self) -> None:
        tv = FakeTV([2])
        self.assertNotEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(tv.count("get_power_state"), cli.WAKE_ATTEMPTS)
        self.assertEqual(self.wol_packets, cli.WAKE_ATTEMPTS + 1)
        self.assertLogged("Giving up - TV unreachable")

    def test_a_screen_that_refuses_to_come_on_uses_the_whole_budget_too(self) -> None:
        tv = FakeTV([AWAKE], screen_on_rc=1)
        self.assertEqual(self.run_cli("ON", tv), 1)
        self.assertEqual(tv.count("turn_screen_on"), cli.WAKE_ATTEMPTS)

    def test_the_tv_off_flag_is_cleared(self) -> None:
        # The flag says "the TV is already off" to the suspend path; a stale one would make the
        # next suspend skip a TV that is on.
        self.tv_off_flag.touch()
        self.run_cli("ON", FakeTV([AWAKE]))
        self.assertFalse(self.tv_off_flag.exists())

    def test_a_missing_network_manager_does_not_stop_the_wake(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv, network_rc=None), 0)

    def test_a_network_that_never_came_back_is_tried_anyway(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv, network_rc=1), 0)
        self.assertLogged("trying anyway")

    # At resume the display watcher and the dispatcher both fire ON; the flock drops the loser,
    # which exits as if it had succeeded. The lock file is 0600 so no other local user can hold it
    # and block every future wake.
    def test_a_concurrent_wake_is_dropped(self) -> None:
        holder = os.fdopen(os.open(self.on_lock, os.O_WRONLY | os.O_CREAT, 0o600), "w")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertEqual(tv.commands(), [])
        self.assertEqual(self.wol_packets, 0)

    def test_the_lock_file_is_owner_only(self) -> None:
        self.run_cli("ON", FakeTV([AWAKE]))
        self.assertEqual(self.on_lock.stat().st_mode & 0o777, 0o600)


class InputSwitchTest(CliCase):
    def test_no_input_configured_means_no_switch(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv), 0)
        self.assertNotIn("set_input", tv.commands())

    def test_the_configured_input_is_selected(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv, OWN_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    # The counterpart to the off guard. Which side owns the input is decided from the wake loop
    # at no extra round trip: a TV we found asleep is one our own packet woke, so the picture is
    # ours to claim. A TV that was already on may have someone watching the other source.
    def test_a_tv_found_awake_keeps_its_picture_on_a_shared_tv(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv, SHARED_TV), 0)
        self.assertNotIn("set_input", tv.commands())
        self.assertLogged("not switching input")

    def test_a_tv_woken_from_standby_gets_the_input_switched(self) -> None:
        tv = FakeTV([STANDBY, AWAKE])
        self.assertEqual(self.run_cli("ON", tv, SHARED_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    def test_a_tv_woken_from_deep_standby_gets_the_input_switched(self) -> None:
        tv = FakeTV([DEEP, AWAKE])
        self.assertEqual(self.run_cli("ON", tv, SHARED_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    # A TV that could not be reached is not one we found awake.
    def test_a_tv_that_was_unreachable_first_gets_the_input_switched(self) -> None:
        tv = FakeTV([2, AWAKE])
        self.assertEqual(self.run_cli("ON", tv, SHARED_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    # A TV moving between power states right after our packet is one it woke. The alternative
    # reading - someone pressing screen-off on the remote in that same second - is a sub-second
    # window with the user standing at the TV, and it costs them one button press.
    def test_a_tv_caught_mid_transition_counts_as_ours(self) -> None:
        tv = FakeTV([WAKING, AWAKE])
        self.assertEqual(self.run_cli("ON", tv, SHARED_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    # Without the shared-TV key the switch is unconditional, which is the ordinary single-computer
    # setup: nothing else is using the TV, so there is nobody to take the picture from.
    def test_an_unshared_tv_switches_input_even_when_found_awake(self) -> None:
        tv = FakeTV([AWAKE])
        self.assertEqual(self.run_cli("ON", tv, OWN_TV), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    # A malformed shared-TV value must not gate the switch either - shared_tv_app_id() reads it as
    # "not configured", and this path has to agree with the guard about that.
    def test_a_malformed_shared_tv_value_does_not_gate_the_switch(self) -> None:
        tv = FakeTV([AWAKE])
        conf = {"HDMI_INPUT": "1", "POWER_OFF_ONLY_ON_HDMI": "HDMI_1"}
        self.assertEqual(self.run_cli("ON", tv, conf), 0)
        self.assertIn(("set_input", "HDMI_1"), tv.calls)

    def test_the_shared_tv_line_stays_out_of_the_log_without_an_input(self) -> None:
        # Checked after HDMI_INPUT so the line never appears for a setup that never switches.
        self.run_cli("ON", FakeTV([AWAKE]), {"POWER_OFF_ONLY_ON_HDMI": "1"})
        self.assertNotLogged("not switching input")

    def test_the_switch_is_retried(self) -> None:
        # The app layer can lag a wake from deep standby.
        tv = FakeTV([AWAKE], set_input_rc=[1, 1, 0])
        self.assertEqual(self.run_cli("ON", tv, OWN_TV), 0)
        self.assertEqual(tv.count("set_input"), 3)

    def test_a_switch_that_never_takes_is_an_error(self) -> None:
        tv = FakeTV([AWAKE], set_input_rc=1)
        self.assertEqual(self.run_cli("ON", tv, OWN_TV), 1)
        self.assertEqual(tv.count("set_input"), cli.SET_INPUT_ATTEMPTS)
        self.assertLogged("could not set input")


# Both numbers are measured budgets, not preferences, and both have been argued over once already.
# This is a tripwire, not an opinion: if a change means to move them, it means to edit this too.
class BudgetTest(unittest.TestCase):
    def test_the_wake_budget_is_fifteen_attempts(self) -> None:
        # Raised 10 -> 15 once because real wakes barely fit. 15 attempts take ~35s wall, not 15s:
        # the connect timeout dominates the 1s poll interval. Never shrink either number, and
        # never shorten the interval - that was tried and halved the budget in practice.
        self.assertEqual(cli.WAKE_ATTEMPTS, 15)

    def test_the_input_switch_has_its_own_smaller_budget(self) -> None:
        # Separate on purpose: the wake loop has already proven the TV awake and answering, so
        # this only covers webOS finishing the switch. Was 15, a leftover from before the loop
        # verified anything; nothing has ever needed the extra tries.
        self.assertEqual(cli.SET_INPUT_ATTEMPTS, 5)

    def test_the_gated_off_events_are_the_documented_four_sources(self) -> None:
        self.assertEqual(
            cli.OFF_EVENT_KEYS,
            {
                "shutdown": "POWER_OFF_AT_SHUTDOWN",
                "nm-dispatcher": "POWER_OFF_AT_SUSPEND",
                "sleep-hook": "POWER_OFF_AT_SUSPEND",
                "sleep-listener": "POWER_OFF_AT_SUSPEND",
            },
        )


if __name__ == "__main__":
    unittest.main()
