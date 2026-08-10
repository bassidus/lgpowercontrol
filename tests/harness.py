# Shared scaffolding for the cli tests. Nothing here talks to a TV, to the network or to /run:
# every one of those is replaced before main() runs, so the suite is safe to run on the machine
# that owns the real TV. The FakeTV's call log is the actual assertion surface - an exit code
# alone proves little in this program, where opposite outcomes share one (the guard standing down
# and the guard letting the off command through both return 0).
import gc
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from lgpowercontrol import cli


# A TV that answers tv_cmd() from a script instead of over a websocket.
#
# states: one entry per get_power_state call - a dict is an answer, a bare int is an rc from a
# failed call. The last entry repeats forever, so a give-up test needs one entry, not fifteen.
# set_input takes the same repeat-the-last treatment, so "fails four times, then works" is a list.
class FakeTV:
    def __init__(self, states=None, screen_on_rc=0, set_input_rc=0, current_app="", app_rc=0):
        self.states = list(states or [{"state": "Active"}])
        self.screen_on_rc = screen_on_rc
        self.set_input_rc = set_input_rc if isinstance(set_input_rc, list) else [set_input_rc]
        self.current_app = current_app
        self.app_rc = app_rc
        self.calls: list[tuple] = []

    def _next(self, queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def tv_cmd(self, command: str, *args, retries=None):
        self.calls.append((command, *args))
        if command == "get_power_state":
            state = self._next(self.states)
            if isinstance(state, int):
                return state, None, "unreachable: fake"
            return 0, state, ""
        if command == "turn_screen_on":
            return self.screen_on_rc, None, ""
        if command == "set_input":
            return self._next(self.set_input_rc), None, ""
        if command == "get_current_app":
            return self.app_rc, self.current_app, ""
        if command in ("power_off", "turn_screen_off"):
            return 0, None, ""
        raise AssertionError(f"unexpected TV command: {command}")

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]

    def count(self, command: str) -> int:
        return self.commands().count(command)


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        # /run is root-only and shared with the running install; both flags move to the scratch
        # directory so a test can neither need root nor disturb a live suspend cycle.
        self.on_lock = self.tmp / "on.lock"
        self.tv_off_flag = self.tmp / "tv-off"
        self.wol_packets = 0
        self.log_lines: list[str] = []

    def _record_wol(self) -> None:
        self.wol_packets += 1

    # Runs cli.main() against the FakeTV. source is what LGPC_SOURCE would have been, i.e. which
    # entry point invoked us - the OFF gating reads it.
    # network_rc is what nm-online answers on the ON path; None stands for a machine without
    # NetworkManager, where the call raises instead.
    def run_cli(self, command: str, tv: FakeTV, conf: dict[str, str] | None = None,
                source: str = "", extra_argv: list[str] | None = None,
                network_rc: int | None = 0) -> int:
        argv = ["lgpowercontrol", *(extra_argv or []), command]
        network = (
            {"side_effect": OSError("no nm-online")} if network_rc is None
            else {"return_value": mock.Mock(returncode=network_rc)}
        )
        with (
            # ON leaves its lock file open on purpose - closing it would release the flock and
            # silently kill the dedupe - so the interpreter warns about it every run. Expected,
            # not a leak to go and fix in cli.py.
            warnings.catch_warnings(),
            mock.patch.object(cli.sys, "argv", argv),
            mock.patch.object(cli, "load_conf", return_value=dict(conf or {})),
            mock.patch.object(cli, "tv_cmd", side_effect=tv.tv_cmd),
            mock.patch.object(cli, "send_wol", side_effect=self._record_wol),
            mock.patch.object(cli, "log", side_effect=self.log_lines.append),
            mock.patch.object(cli, "SOURCE", source),
            mock.patch.object(cli, "ON_LOCK", self.on_lock),
            mock.patch.object(cli, "TV_OFF_FLAG", self.tv_off_flag),
            mock.patch.object(cli.time, "sleep"),
            mock.patch.object(cli.subprocess, "run", **network),
        ):
            warnings.simplefilter("ignore", ResourceWarning)
            rc = cli.main()
            gc.collect()  # collect that lock file here, while the filter above still applies
            return rc

    def assertLogged(self, fragment: str) -> None:
        self.assertTrue(
            any(fragment in line for line in self.log_lines),
            f"no log line contains {fragment!r}; got {self.log_lines}",
        )

    def assertNotLogged(self, fragment: str) -> None:
        self.assertFalse(
            any(fragment in line for line in self.log_lines),
            f"a log line contains {fragment!r}: {self.log_lines}",
        )
