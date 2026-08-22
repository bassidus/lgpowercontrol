# The `log` subcommand. Nothing here reads the real journal or the installed conf: journalctl is
# faked and CONF_FILE is redirected into a scratch directory, so the suite stays safe to run on the
# machine that owns the live install - including as root, which the full sweep does.
import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import admin, cli

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
            mock.patch.object(admin, "restart_hint", return_value=""),
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


class RestartHintTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.system = Path(tmp.name) / "system"
        self.user = Path(tmp.name) / "user"
        self.system.mkdir()
        self.user.mkdir()

    def hint(self, *units: str) -> str:
        for name in units:
            directory = self.user if name == "notify" else self.system
            (directory / f"lgpowercontrol-{name}.service").touch()
        with (mock.patch.object(admin, "SYSTEM_UNIT_DIR", self.system),
              mock.patch.object(admin, "USER_UNIT_DIR", self.user)):
            return admin.restart_hint()

    def test_only_the_installed_units_are_named(self) -> None:
        # The sleep listener is the immutable-OS fallback, absent wherever the sleep hook was
        # installed instead - naming it there would hand out a command that fails.
        hint = self.hint("monitor", "notify")
        self.assertIn("lgpowercontrol-monitor.service", hint)
        self.assertNotIn("lgpowercontrol-sleep.service", hint)

    def test_the_listener_joins_the_root_line_when_it_is_installed(self) -> None:
        hint = self.hint("monitor", "sleep")
        self.assertIn("sudo systemctl restart lgpowercontrol-monitor.service "
                      "lgpowercontrol-sleep.service", hint)

    # The notify unit runs in the user's session, so it needs the other systemctl.
    def test_the_notify_unit_gets_a_user_scope_line(self) -> None:
        self.assertIn("systemctl --user restart lgpowercontrol-notify.service",
                      self.hint("notify"))

    def test_nothing_installed_says_nothing(self) -> None:
        self.assertEqual(self.hint(), "")


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

    def test_the_dispatch_does_not_shadow_the_modules_own_logger(self) -> None:
        # `from lgpowercontrol import log` would bind a local of that name for the whole of
        # main(), silently breaking every log() call on the ON and OFF paths below it.
        self.assertTrue(callable(cli.log))


if __name__ == "__main__":
    unittest.main()
