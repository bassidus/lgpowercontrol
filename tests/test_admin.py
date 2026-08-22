# The `log` subcommand. Nothing here reads the real journal or the installed conf: journalctl is
# faked and CONF_FILE is redirected into a scratch directory, so the suite stays safe to run on the
# machine that owns the live install - including as root, which the full sweep does.
import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import admin, cli, uninstall, update

CONF_LINE = 'LOGGING="0" # 1 = enabled | 0 = disabled\n'


# Stands in for the exec that replaces this process, which cannot happen inside a test runner.
class Executed(Exception):
    pass


class LogCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.conf = self.tmp / "lgpowercontrol.conf"

    # Runs log_cmd() with the conf replaced and journalctl faked; returns (rc, stdout, stderr).
    # conf=None leaves the file absent, which is what an uninstalled machine looks like.
    # restart_hint is stubbed out because it reads /etc - it has its own test below.
    def run_log(self, argv: list[str], *, conf: str | None = CONF_LINE, journal_out: str = "",
                journal_rc: int = 0, journalctl: bool = True, euid: int = 1000):
        if conf is not None:
            self.conf.write_text(conf)
        self.journalctl = mock.Mock(
            return_value=mock.Mock(returncode=journal_rc, stdout=journal_out, stderr="")
        )
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(admin, "CONF_FILE", self.conf),
            mock.patch.object(admin.shutil, "which",
                              return_value="/usr/bin/journalctl" if journalctl else None),
            mock.patch.object(admin.subprocess, "run", self.journalctl),
            mock.patch.object(admin.os, "geteuid", return_value=euid),
            mock.patch.object(admin, "restart_services"),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = admin.log_cmd(argv)
        return rc, out.getvalue(), err.getvalue()

    def journal_argv(self) -> list[str]:
        return self.journalctl.call_args.args[0]

    def conf_lines(self) -> list[str]:
        return [line for line in self.conf.read_text().splitlines() if "LOGGING" in line]


class JournalCommandTest(LogCase):
    def test_the_default_is_the_last_fifty_lines_of_this_programs_tag(self) -> None:
        self.run_log([])
        self.assertEqual(self.journal_argv(),
                         ["journalctl", "-q", "-t", "lgpowercontrol", "-n", "50"])

    def test_a_line_count_is_passed_through(self) -> None:
        self.run_log(["7"])
        self.assertEqual(self.journal_argv()[-1], "7")

    # -n 0 prints nothing and a negative one is a journalctl usage error; neither is what the
    # person who typed it meant, and cli.py clamps --retries the same way.
    def test_zero_and_negative_counts_clamp_to_one(self) -> None:
        for count in ("0", "-3"):
            with self.subTest(count=count):
                self.run_log([count])
                self.assertEqual(self.journal_argv()[-1], "1")

    def test_the_journal_output_is_printed_verbatim(self) -> None:
        rc, out, _ = self.run_log([], journal_out="aug 22 09:23:59 p600s lgpowercontrol: hi\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "aug 22 09:23:59 p600s lgpowercontrol: hi\n")

    def test_a_failing_journalctl_returns_its_code_and_explains_nothing(self) -> None:
        # Empty output means something else here - the command never ran - so the "nothing is
        # logged" advice would be an invented diagnosis.
        rc, out, _ = self.run_log([], journal_rc=1)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")

    def test_follow_execs_journalctl_instead_of_capturing_it(self) -> None:
        # The real execvp never returns, so the stand-in must not either - letting it fall
        # through would test a line that cannot run.
        with (mock.patch.object(admin.os, "execvp", side_effect=Executed) as execvp,
              self.assertRaises(Executed)):
            self.run_log(["-f"])
        self.journalctl.assert_not_called()
        self.assertEqual(execvp.call_args.args[0], "journalctl")
        self.assertEqual(execvp.call_args.args[1][-1], "-f")

    def test_a_machine_without_journalctl_says_so_instead_of_failing_to_start_it(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.run_log([], journalctl=False)
        self.assertIn("journalctl", str(caught.exception.code))


class EmptyLogTest(LogCase):
    def test_logging_being_off_is_named_as_the_reason(self) -> None:
        _, out, _ = self.run_log([], conf='LOGGING="0"\n')
        self.assertIn("logging is off", out)
        self.assertIn("--enable", out)

    # The other reason an empty log is not the same as a quiet program: the services log as root.
    def test_a_non_root_caller_is_offered_sudo(self) -> None:
        _, out, _ = self.run_log([], conf='LOGGING="1"\n')
        self.assertIn("sudo lgpowercontrol log", out)

    def test_root_is_not_told_to_use_sudo(self) -> None:
        _, out, _ = self.run_log([], conf='LOGGING="1"\n', euid=0)
        self.assertNotIn("sudo", out)

    # An absent conf is not evidence that logging is off - Logger treats it as on.
    def test_an_unreadable_conf_does_not_claim_logging_is_off(self) -> None:
        _, out, _ = self.run_log([], conf=None)
        self.assertNotIn("logging is off", out)

    def test_a_non_empty_log_explains_nothing(self) -> None:
        _, out, _ = self.run_log([], conf='LOGGING="0"\n', journal_out="a line\n")
        self.assertEqual(out, "a line\n")


class SetLoggingTest(LogCase):
    def test_enable_flips_the_value_and_keeps_the_comment(self) -> None:
        # The comment is what tells the next reader what 1 and 0 mean, so rewriting the whole
        # line the way install.py does would quietly cost the conf its documentation.
        rc, _, _ = self.run_log(["--enable"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.conf_lines(), ['LOGGING="1" # 1 = enabled | 0 = disabled'])

    def test_disable_flips_it_back(self) -> None:
        self.run_log(["--disable"], conf='LOGGING="1" # 1 = enabled | 0 = disabled\n')
        self.assertEqual(self.conf_lines(), ['LOGGING="0" # 1 = enabled | 0 = disabled'])

    def test_an_unquoted_value_is_replaced_too(self) -> None:
        self.run_log(["--enable"], conf="LOGGING=0\n")
        self.assertEqual(self.conf_lines(), ['LOGGING="1"'])

    def test_a_deleted_key_is_put_back_rather_than_silently_ignored(self) -> None:
        self.run_log(["--enable"], conf='LGTV_IP="10.0.0.5"\n')
        self.assertEqual(self.conf_lines(), ['LOGGING="1"'])
        self.assertIn('LGTV_IP="10.0.0.5"', self.conf.read_text())

    # load_conf lets the last definition win, so editing only the first occurrence would report
    # success while the setting read back unchanged.
    def test_every_occurrence_is_rewritten(self) -> None:
        self.run_log(["--enable"], conf='LOGGING="0"\nLOGGING="0"\n')
        self.assertEqual(self.conf_lines(), ['LOGGING="1"', 'LOGGING="1"'])

    def test_other_settings_are_left_alone(self) -> None:
        self.run_log(["--enable"], conf='SHARED_TV="1"\nLOGGING="0"\nHDMI_INPUT="2"\n')
        self.assertEqual(self.conf.read_text(), 'SHARED_TV="1"\nLOGGING="1"\nHDMI_INPUT="2"\n')

    # chmod would prove nothing here: the full sweep runs the suite as root, and root writes a
    # read-only file anyway.
    def test_a_conf_that_cannot_be_written_says_to_use_sudo(self) -> None:
        self.conf.write_text(CONF_LINE)  # before the patch below, which would block it too
        with (mock.patch.object(Path, "write_text", side_effect=PermissionError("denied")),
              self.assertRaises(SystemExit) as caught):
            self.run_log(["--enable"], conf=None)
        self.assertIn("sudo lgpowercontrol log --enable", str(caught.exception.code))

    def test_a_missing_conf_exits_instead_of_creating_one(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_log(["--enable"], conf=None)
        self.assertFalse(self.conf.exists())

    def test_a_toggle_asks_the_journal_nothing(self) -> None:
        self.run_log(["--enable"])
        self.journalctl.assert_not_called()


class LoggingStatusTest(LogCase):
    def test_one_reads_as_on(self) -> None:
        rc, out, _ = self.run_log(["--status"], conf='LOGGING="1"\n')
        self.assertEqual(rc, 0)
        self.assertIn("Logging is on", out)

    def test_zero_and_an_empty_value_read_as_off(self) -> None:
        for value in ('LOGGING="0"\n', 'LOGGING=""\n'):
            with self.subTest(value=value):
                _, out, _ = self.run_log(["--status"], conf=value)
                self.assertIn("Logging is off", out)
                self.assertIn("--enable", out)

    # Only 1 enables (see common.Logger), so "on" is off - and a status command that answered
    # "off" without naming the value would leave the user staring at a conf that says on.
    def test_a_value_that_is_neither_is_quoted_back(self) -> None:
        _, out, _ = self.run_log(["--status"], conf='LOGGING="on"\n')
        self.assertIn("Logging is off", out)
        self.assertIn("'on'", out)

    def test_a_missing_conf_exits(self) -> None:
        with self.assertRaises(SystemExit):
            self.run_log(["--status"], conf=None)


class InstalledServicesTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.system = Path(tmp.name) / "system"
        self.user = Path(tmp.name) / "user"
        self.system.mkdir()
        self.user.mkdir()

    def services(self, *units: str) -> list[tuple[str, bool]]:
        for name in units:
            directory = self.user if name == "notify" else self.system
            (directory / f"lgpowercontrol-{name}.service").touch()
        with (mock.patch.object(admin, "SYSTEM_UNIT_DIR", self.system),
              mock.patch.object(admin, "USER_UNIT_DIR", self.user)):
            return admin.installed_services()

    # The sleep listener is the immutable-OS fallback, absent wherever the sleep hook was
    # installed instead - restarting it there would fail on a unit that does not exist.
    def test_only_the_installed_units_are_listed(self) -> None:
        self.assertEqual(self.services("monitor", "notify"),
                         [("lgpowercontrol-monitor.service", False),
                          ("lgpowercontrol-notify.service", True)])

    def test_the_listener_joins_them_where_it_is_installed(self) -> None:
        self.assertIn(("lgpowercontrol-sleep.service", False), self.services("monitor", "sleep"))

    def test_nothing_installed_is_an_empty_list(self) -> None:
        self.assertEqual(self.services(), [])


class SystemctlTest(unittest.TestCase):
    def call(self, *args, user_scope: bool = False, euid: int = 1000, sudo_user: str | None = None):
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        environ = {"SUDO_USER": sudo_user} if sudo_user else {}
        with (mock.patch.object(admin.subprocess, "run", run),
              mock.patch.object(admin.os, "geteuid", return_value=euid),
              mock.patch.object(admin.os, "environ", environ),
              mock.patch.object(admin.pwd, "getpwnam", return_value=mock.Mock(pw_uid=1000))):
            result = admin.systemctl(*args, user_scope=user_scope)
        return result, run.call_args.args[0] if run.call_args else None

    def test_a_system_unit_is_a_plain_call(self) -> None:
        _, argv = self.call("restart", "lgpowercontrol-monitor.service")
        self.assertEqual(argv, ["systemctl", "restart", "lgpowercontrol-monitor.service"])

    def test_the_user_scope_call_stays_plain_for_the_user_themselves(self) -> None:
        _, argv = self.call("restart", "u.service", user_scope=True)
        self.assertEqual(argv, ["systemctl", "--user", "restart", "u.service"])

    # Under sudo, `systemctl --user` would reach root's session, where the notify unit does not
    # exist - and succeed at nothing, which is worse than failing.
    def test_under_sudo_the_user_scope_call_goes_through_runuser(self) -> None:
        _, argv = self.call("restart", "u.service", user_scope=True, euid=0, sudo_user="basse")
        self.assertEqual(argv, ["runuser", "-u", "basse", "--", "env",
                                "XDG_RUNTIME_DIR=/run/user/1000",
                                "systemctl", "--user", "restart", "u.service"])

    def test_a_root_login_has_no_user_session_to_aim_at(self) -> None:
        result, argv = self.call("restart", "u.service", user_scope=True, euid=0)
        self.assertIsNone(result)
        self.assertIsNone(argv)


class RestartServicesTest(unittest.TestCase):
    # services: the (name, user_scope) pairs that are installed. active: names is-active answers
    # yes for. failures: names whose restart fails.
    def restart(self, services, active=(), failures=()):
        self.calls: list[tuple] = []

        def fake_systemctl(*args, user_scope: bool):
            self.calls.append((*args, user_scope))
            name = args[-1]
            if args[0] == "is-active":
                return mock.Mock(returncode=0 if name in active else 1, stderr="")
            failed = name in failures
            return mock.Mock(returncode=1 if failed else 0,
                             stderr="Interactive authentication required." if failed else "")

        out = io.StringIO()
        with (mock.patch.object(admin, "installed_services", return_value=services),
              mock.patch.object(admin, "systemctl", side_effect=fake_systemctl),
              contextlib.redirect_stdout(out)):
            admin.restart_services()
        return out.getvalue()

    def test_a_running_service_is_restarted_and_named(self) -> None:
        out = self.restart([("lgpowercontrol-monitor.service", False)],
                           active=("lgpowercontrol-monitor.service",))
        self.assertIn("restart", [call[0] for call in self.calls])
        self.assertEqual(out, "Restarted lgpowercontrol-monitor.service.\n")

    # `restart` would start it; a service that is deliberately stopped must stay stopped, and it
    # reads the new value whenever it does start.
    def test_a_stopped_service_is_left_stopped(self) -> None:
        out = self.restart([("lgpowercontrol-monitor.service", False)])
        self.assertEqual([call[0] for call in self.calls], ["is-active"])
        self.assertEqual(out, "")

    def test_the_user_unit_is_restarted_in_the_user_scope(self) -> None:
        self.restart([("lgpowercontrol-notify.service", True)],
                     active=("lgpowercontrol-notify.service",))
        self.assertTrue(all(call[-1] is True for call in self.calls))

    # Without root this is polkit's decision, and a terminal with no agent gets a refusal. The
    # user must be told, or they are left believing the service picked up the setting.
    def test_a_refused_restart_reports_the_command_to_run_by_hand(self) -> None:
        out = self.restart([("lgpowercontrol-monitor.service", False)],
                           active=("lgpowercontrol-monitor.service",),
                           failures=("lgpowercontrol-monitor.service",))
        self.assertIn("Could not restart lgpowercontrol-monitor.service", out)
        self.assertIn("Interactive authentication required.", out)
        self.assertIn("sudo systemctl restart lgpowercontrol-monitor.service", out)

    def test_a_refused_user_unit_is_offered_the_user_scope_command(self) -> None:
        out = self.restart([("lgpowercontrol-notify.service", True)],
                           active=("lgpowercontrol-notify.service",),
                           failures=("lgpowercontrol-notify.service",))
        self.assertIn("  systemctl --user restart lgpowercontrol-notify.service", out)

    def test_one_failure_does_not_hide_the_others_success(self) -> None:
        out = self.restart([("lgpowercontrol-monitor.service", False),
                            ("lgpowercontrol-notify.service", True)],
                           active=("lgpowercontrol-monitor.service",
                                   "lgpowercontrol-notify.service"),
                           failures=("lgpowercontrol-notify.service",))
        self.assertIn("Restarted lgpowercontrol-monitor.service.", out)
        self.assertIn("Could not restart lgpowercontrol-notify.service", out)


class LogArgumentTest(LogCase):
    def usage_error(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            admin.log_cmd(argv)
        return caught.exception.code

    # Half-obeying `log 100 --disable` is worse than refusing it.
    def test_the_toggles_refuse_a_line_count_and_follow(self) -> None:
        for argv in (["100", "--disable"], ["-f", "--status"], ["5", "--enable"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.usage_error(argv), 2)

    def test_the_toggles_are_mutually_exclusive(self) -> None:
        self.assertEqual(self.usage_error(["--enable", "--disable"]), 2)

    def test_a_non_numeric_count_is_a_usage_error(self) -> None:
        self.assertEqual(self.usage_error(["abc"]), 2)


class DispatchTest(unittest.TestCase):
    def test_log_reaches_the_subcommand_in_any_case_with_the_rest_of_the_line(self) -> None:
        for typed in ("log", "LOG"):
            with self.subTest(typed=typed):
                with (mock.patch.object(admin, "log_cmd", return_value=0) as log_cmd,
                      mock.patch.object(cli.sys, "argv", ["lgpowercontrol", typed, "20"])):
                    self.assertEqual(cli.main(), 0)
                log_cmd.assert_called_once_with(["20"])

    # cli.SUBCOMMANDS drives both the help and the dispatch, so a name listed in one and missing
    # from the other is a KeyError on a command the help just advertised.
    def test_every_listed_command_has_a_handler(self) -> None:
        for name in cli.SUBCOMMANDS:
            with (self.subTest(name=name),
                  mock.patch.object(admin, "nic_wol", return_value=0),
                  mock.patch.object(admin, "authorize", return_value=0),
                  mock.patch.object(admin, "log_cmd", return_value=0),
                  mock.patch.object(update, "main", return_value=0),
                  mock.patch.object(uninstall, "main", return_value=0),
                  mock.patch.object(cli.sys, "argv", ["lgpowercontrol", name])):
                self.assertEqual(cli.main(), 0)

    def test_the_dispatch_does_not_shadow_the_modules_own_logger(self) -> None:
        # `from lgpowercontrol import log` would bind a local of that name for the whole of
        # main(), silently breaking every log() call on the ON and OFF paths below it.
        self.assertTrue(callable(cli.log))


if __name__ == "__main__":
    unittest.main()
