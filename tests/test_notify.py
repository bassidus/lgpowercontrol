import unittest
from unittest import mock

from lgpowercontrol import notify

# What kscreen-doctor -o actually prints for a dimmed output. Plasma's idle dim is invisible
# everywhere else: no brightness-interface signal, no compositor effect, no session-bus event.
# The -j JSON does not carry it either, which is why this plain text is parsed at all.
DIMMED_OUTPUT = """\
Output: 1 DP-1
\tenabled
\tconnected
\tGeometry: 0,0 3840x2160
\tBrightness: set to 100% and dimming to 30%
"""

BRIGHT_OUTPUT = DIMMED_OUTPUT.replace("dimming to 30%", "dimming to 100%")


class ScreenDimmedTest(unittest.TestCase):
    def dimmed(self, stdout: str) -> bool:
        with mock.patch.object(notify.subprocess, "run",
                               return_value=mock.Mock(stdout=stdout, returncode=0)):
            return notify.screen_dimmed()

    def test_a_dimmed_output_is_detected(self) -> None:
        self.assertTrue(self.dimmed(DIMMED_OUTPUT))

    def test_a_full_brightness_output_is_not(self) -> None:
        self.assertFalse(self.dimmed(BRIGHT_OUTPUT))

    # Every output is matched rather than one named output: Plasma's per-output display names
    # change between sessions, so naming one would break silently.
    def test_any_dimmed_output_counts(self) -> None:
        self.assertTrue(self.dimmed(BRIGHT_OUTPUT + DIMMED_OUTPUT.replace("DP-1", "HDMI-A-1")))

    def test_output_without_a_dimming_field_is_not_dimmed(self) -> None:
        # A desktop that reports no dimming at all must read as "not dimmed", never as dimmed.
        self.assertFalse(self.dimmed("Output: 1 DP-1\n\tenabled\n"))

    def test_no_output_at_all_is_not_dimmed(self) -> None:
        self.assertFalse(self.dimmed(""))


class ReadPowerdevilIntTest(unittest.TestCase):
    def read(self, value: str, default: int = 300) -> int:
        with mock.patch.object(notify, "read_powerdevil", return_value=value):
            return notify.read_powerdevil_int("AC", "DimDisplayIdleTimeoutSec", default)

    def test_a_number_is_parsed(self) -> None:
        self.assertEqual(self.read("450"), 450)

    def test_anything_else_falls_back_to_the_default(self) -> None:
        for value in ("", "abc", "-1", "30.5"):
            with self.subTest(value=value):
                self.assertEqual(self.read(value), 300)


class ComputeTimingsTest(unittest.TestCase):
    # settings: the powerdevilrc values kreadconfig6 would answer with; anything absent falls
    # through to that key's default, which is Plasma's own.
    def timings(self, off_warning_seconds: int = 120, profile: str = "AC", settings=None):
        settings = settings or {}

        def read_powerdevil(group, key, default):
            return str(settings.get(key, default))

        notifier = notify.Notifier(off_warning_seconds)
        with (
            mock.patch.object(notify, "busctl", return_value=f's "{profile}"\n'),
            mock.patch.object(notify, "read_powerdevil", side_effect=read_powerdevil),
            mock.patch.object(notify, "log"),
        ):
            notifier.compute_timings()
        return notifier

    def test_the_warning_lands_the_configured_time_before_the_screen_goes_off(self) -> None:
        # Basse's own setup: Plasma's defaults, dim at 300s and off at 600s.
        notifier = self.timings()
        self.assertEqual((notifier.dim_timeout, notifier.off_timeout), (300, 600))
        self.assertEqual(notifier.notify_delay, 180)  # armed at the dim, fires 180s later
        self.assertEqual(notifier.remaining, 120)     # ...leaving the configured 120s

    def test_a_warning_longer_than_the_window_fires_at_the_dim(self) -> None:
        # Documented behaviour: more than the gap between dimming and screen-off means the
        # warning comes as soon as the screen dims, and it counts down the whole gap.
        notifier = self.timings(off_warning_seconds=400)
        self.assertEqual(notifier.notify_delay, 0)
        self.assertEqual(notifier.remaining, 300)

    def test_no_window_at_all_leaves_nothing_to_count_down(self) -> None:
        # off timeout not later than the dim timeout: main() logs this and arms no timer.
        notifier = self.timings(settings={"TurnOffDisplayIdleTimeoutSec": 300})
        self.assertEqual(notifier.remaining, 0)

    def test_explicit_plasma_values_win_over_the_defaults(self) -> None:
        notifier = self.timings(settings={
            "DimDisplayIdleTimeoutSec": 60, "TurnOffDisplayIdleTimeoutSec": 240,
        })
        self.assertEqual((notifier.dim_timeout, notifier.off_timeout), (60, 240))
        self.assertEqual(notifier.remaining, 120)

    def test_the_battery_profiles_have_their_own_defaults(self) -> None:
        # Unverified estimates, never confirmed against real hardware - kept apart from the AC row
        # so that stays true of only these two.
        notifier = self.timings(profile="Battery")
        self.assertEqual((notifier.dim_timeout, notifier.off_timeout), (120, 300))

    # The profile name doubles as the kreadconfig6 --group, so an unexpected one has to be
    # normalised rather than passed through.
    def test_an_unknown_profile_falls_back_to_ac(self) -> None:
        for answer in ("Performance", ""):
            with self.subTest(profile=answer):
                self.assertEqual(self.timings(profile=answer).profile, "AC")

    def test_turning_the_plasma_setting_off_disables_the_warning(self) -> None:
        notifier = self.timings(settings={"TurnOffDisplayWhenIdle": "false"})
        self.assertFalse(notifier.off_enabled)

    # Re-read at every dim, never once at startup: checking only at start meant re-enabling the
    # setting needed a manual service restart. This is the tripwire for that.
    def test_re_enabling_the_setting_is_picked_up_on_the_next_dim(self) -> None:
        notifier = notify.Notifier(120)

        def timings(enabled: str):
            with (
                mock.patch.object(notify, "busctl", return_value='s "AC"\n'),
                mock.patch.object(notify, "read_powerdevil",
                                  side_effect=lambda g, k, d: enabled if k == "TurnOffDisplayWhenIdle"
                                  else str(d)),
                mock.patch.object(notify, "log"),
            ):
                notifier.compute_timings()

        timings("false")
        self.assertFalse(notifier.off_enabled)
        timings("true")
        self.assertTrue(notifier.off_enabled)


class CancelTimerTest(unittest.TestCase):
    def test_a_pending_warning_is_cancelled_and_the_notification_closed(self) -> None:
        notifier = notify.Notifier(120)
        notifier.notification_id = 42
        notifier.timer = mock.Mock()
        notifier.timer.is_alive.return_value = True

        with mock.patch.object(notify, "notify_close") as close, mock.patch.object(notify, "log"):
            notifier.cancel_timer()

        close.assert_called_once_with(42)
        self.assertEqual(notifier.notification_id, 0)
        self.assertIsNone(notifier.timer)

    # Without this line a warning followed by no screen-off reads the same in the log whether the
    # user came back or something held the screen on - 2026-09-08 11:56 could not be told apart.
    def test_coming_back_after_the_warning_cancels_the_check_and_says_so(self) -> None:
        notifier = notify.Notifier(120)
        check_timer = notifier.check_timer = mock.Mock()
        check_timer.is_alive.return_value = True

        with mock.patch.object(notify, "notify_close"), mock.patch.object(notify, "log") as log:
            notifier.cancel_timer()

        check_timer.cancel.assert_called_once()
        log.assert_called_once_with("Screen dim ended, mouse or keyboard activity detected")
        self.assertIsNone(notifier.check_timer)


class ShowWarningTest(unittest.TestCase):
    # Re-checked because the dim may have ended while the timer waited - the user moving the mouse
    # in that window must not still get told the TV is about to go off.
    def test_a_dim_that_ended_while_the_timer_waited_shows_nothing(self) -> None:
        notifier = notify.Notifier(120)
        with mock.patch.object(notify, "screen_dimmed", return_value=False), \
             mock.patch.object(notify, "notify_send") as send, \
             mock.patch.object(notify.threading, "Timer") as timer:
            notifier.show_warning()
        send.assert_not_called()
        timer.assert_not_called()

    def test_the_notification_times_out_when_the_tv_does(self) -> None:
        notifier = notify.Notifier(120)
        notifier.remaining = 90
        with mock.patch.object(notify, "screen_dimmed", return_value=True), \
             mock.patch.object(notify, "notify_send", return_value=7) as send, \
             mock.patch.object(notify, "log"), \
             mock.patch.object(notify.threading, "Timer"):
            notifier.show_warning()
        self.assertEqual(send.call_args.kwargs["timeout_ms"], 90_000)
        self.assertIn("90 seconds", send.call_args.args[1])
        self.assertEqual(notifier.notification_id, 7)

    def test_a_sent_warning_arms_the_screen_off_check_past_the_promised_moment(self) -> None:
        notifier = notify.Notifier(120)
        notifier.remaining = 90
        with mock.patch.object(notify, "screen_dimmed", return_value=True), \
             mock.patch.object(notify, "notify_send", return_value=7), \
             mock.patch.object(notify, "log"), \
             mock.patch.object(notify.threading, "Timer") as timer:
            notifier.show_warning()
        timer.assert_called_once_with(90 + notify.SCREEN_OFF_GRACE_SECONDS, notifier.check_screen_off)
        timer.return_value.start.assert_called_once()

    # Measured, not chosen: the screen went off 116-121 s after a 120 s warning over 7-11 Sep 2026.
    # Much tighter and a normal screen-off raises the alarm.
    def test_the_grace_covers_the_measured_screen_off_spread(self) -> None:
        self.assertEqual(notify.SCREEN_OFF_GRACE_SECONDS, 30)


class CheckScreenOffTest(unittest.TestCase):
    def check(self, dimmed: bool, dpms: str):
        notifier = notify.Notifier(120)
        notifier.notification_id = 7
        with (
            mock.patch.object(notify, "screen_dimmed", return_value=dimmed),
            mock.patch.object(notify, "get_dpms_state", return_value=dpms),
            mock.patch.object(notify, "notify_send", return_value=8) as send,
            mock.patch.object(notify, "notify_close") as close,
            mock.patch.object(notify, "log") as log,
        ):
            notifier.check_screen_off()
        return notifier, send, close, log

    # The case this exists for: 2026-09-11, a warning at 08:08:46 and the screen held on, dimmed,
    # until 08:30:47.
    def test_a_screen_still_on_and_dimmed_gets_told_the_tv_stayed_on(self) -> None:
        notifier, send, close, log = self.check(dimmed=True, dpms="on")
        close.assert_called_once_with(7)  # the warning's countdown is no longer true
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "TV not turned off")
        self.assertEqual(send.call_args.kwargs.get("timeout_ms", 0), 0)  # never expires
        log.assert_called_once()
        # not remembered, so the dim ending cannot close it before the user has read it
        self.assertEqual(notifier.notification_id, 0)

    def test_a_screen_that_went_off_as_promised_stays_quiet(self) -> None:
        _, send, close, log = self.check(dimmed=True, dpms="off")
        send.assert_not_called()
        close.assert_not_called()
        log.assert_not_called()

    def test_a_user_who_came_back_gets_nothing(self) -> None:
        _, send, _, log = self.check(dimmed=False, dpms="on")
        send.assert_not_called()
        log.assert_not_called()

    def test_no_connected_output_is_not_read_as_a_screen_held_on(self) -> None:
        _, send, _, _ = self.check(dimmed=True, dpms="")
        send.assert_not_called()


# main() ends in an endless poll loop, so both of these have to prove the early return happens
# rather than let the loop start: the Notifier below refuses to be built, which turns a lost
# early return into a failed test instead of a suite that hangs.
class EarlyReturnTest(unittest.TestCase):
    def refuse(self, *args, **kwargs):
        raise AssertionError("main() went past its early return and started the service")

    # A non-KDE desktop returns exit 0, so Restart=on-failure does not spin. Measured on Mint and
    # on the Ubuntu GNOME VM, where the unit rests enabled/inactive.
    def test_a_desktop_without_the_plasma_tools_exits_quietly(self) -> None:
        with (
            mock.patch.object(notify, "load_conf", return_value={"OFF_WARNING_SECONDS": "120"}),
            mock.patch.object(notify.shutil, "which", return_value=None),
            mock.patch.object(notify, "Notifier", side_effect=self.refuse),
        ):
            self.assertIsNone(notify.main())

    # 0 is the documented way to switch the warning off, and it must not read as "unset".
    def test_a_zero_warning_switches_the_service_off(self) -> None:
        with (
            mock.patch.object(notify, "load_conf", return_value={"OFF_WARNING_SECONDS": "0"}),
            mock.patch.object(notify.shutil, "which", return_value="/usr/bin/kscreen-doctor") as which,
            mock.patch.object(notify, "Notifier", side_effect=self.refuse),
        ):
            self.assertIsNone(notify.main())
        which.assert_not_called()  # returns before it even looks for the tools


if __name__ == "__main__":
    unittest.main()
