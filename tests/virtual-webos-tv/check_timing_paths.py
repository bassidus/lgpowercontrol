#!/usr/bin/python3
"""Exercises lgpowercontrol against a slow and a lossy TV.

Two different things live here. The first is a budget measurement: with the virtual TV set to
the times measured on real hardware, how long does a command actually take, and what does the
OFF guard's extra session cost? The second is sharper - three of these cases do not finish at
all, and that is the expected result.

Read CLAUDE.md section 6 first, and section 10 for what the hangs mean. Emphatically: none of
this says anything about the pre-down race. That race runs against NetworkManager flushing the
interface, not against the TV's response time, so a number from here is our own budget and
never an argument about where NM has got to.
"""

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import rig

# How long a case that is expected to hang is given before we call it a hang. Generous enough
# that a slow machine cannot fake one: every finishing case here completes in well under a
# second, and the client has no timeout that could fire at any length.
HANG_TIMEOUT = 8.0

HANGS = "hangs"   # expected "exit code" for the cases that never finish


class Case:
    def __init__(self, name, proves, server_args, conf, command, expect_rc,
                 expect_attempts=None, expect_sessions=None, min_seconds=None,
                 expect_stderr=None):
        self.name = name
        self.proves = proves
        self.server_args = server_args
        self.conf = conf
        self.command = command
        self.expect_rc = expect_rc                # an int, or HANGS
        # Attempts counts opening handshakes, sessions counts the ones that got past it. A
        # client that gives up mid-handshake shows up only in the first, which is how
        # connect_retry_attempts becomes visible at all.
        self.expect_attempts = expect_attempts
        self.expect_sessions = expect_sessions
        self.min_seconds = min_seconds            # floor on elapsed time, if asserted
        self.expect_stderr = expect_stderr        # substring the CLI must have printed


CASES = [
    Case(
        # Two sessions: the guard's get_current_app, then power_off. On real hardware that was
        # 138ms against 71ms for one, which is where "the guard costs ~67ms" comes from.
        name="calibrated-off",
        proves="the OFF guard's extra session costs roughly one more handshake",
        server_args=["--calibrated"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="OFF",
        expect_rc=0, expect_attempts=2, expect_sessions=2, min_seconds=2 * 0.057,
    ),
    Case(
        name="calibrated-status",
        proves="a single session for comparison; the difference is the guard's whole cost",
        server_args=["--calibrated"],
        conf={}, command="STATUS",
        expect_rc=0, expect_attempts=1, expect_sessions=1, min_seconds=0.057,
    ),
    Case(
        # The one delay that lands inside asyncio.wait_for(websockets.connect(...), timeout=2).
        # STATUS rather than OFF because the guard passes retries=1 and would return before the
        # retry loop ever ran; get_power_state uses the default RETRIES of 3.
        name="handshake-timeout",
        proves="a handshake past timeout_connect retries 3 times, reaches no session, gives rc 2",
        server_args=["--latency-handshake", "2500"],
        conf={}, command="STATUS",
        expect_rc=2, expect_attempts=3, expect_sessions=0,
    ),
    Case(
        # Nothing on the client bounds the pairing recv(). This case is expected never to end.
        name="pairing-never-answered",
        proves="a TV that completes TLS but never answers the pairing hangs the CLI forever",
        server_args=["--latency-pairing", "600000"],
        conf={}, command="STATUS",
        expect_rc=HANGS, expect_attempts=1, expect_sessions=1,
    ),
    Case(
        # Same story one layer up: request() awaits its future with no timeout.
        name="response-dropped",
        proves="a dropped command response hangs the CLI forever; loss is not an error here",
        server_args=["--loss", "1"],
        conf={}, command="STATUS",
        expect_rc=HANGS, expect_attempts=1, expect_sessions=1,
    ),
    Case(
        # And the same thing in the suspend path, which is where a hang would actually hurt.
        # systemd's delay inhibitor bounds the damage - the machine still suspends - but the
        # TV never gets turned off and the process is left behind.
        name="response-dropped-on-off",
        proves="the same hang on the OFF path, which is the one the sleep hook runs",
        server_args=["--loss", "1"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="OFF",
        expect_rc=HANGS, expect_attempts=1, expect_sessions=1,
    ),
    Case(
        # A link that dies rather than goes quiet. The socket closing cancels the pending
        # future, so unlike silence this is recoverable - but only since the fix below.
        #
        # This case found a defect: asyncio.CancelledError subclasses BaseException, so
        # tv_cmd's `except Exception` catch-all never saw it. It escaped main() as a traceback
        # with rc 1, where a TV that went away should give 2 - the code monitor.py classifies
        # on. Fixed by naming it explicitly in tv_cmd; see CLAUDE.md section 10.2.
        #
        # A failure here reading "exit 1" with a CancelledError traceback in the stderr dump
        # means the installed build predates that fix, not that the rig is wrong.
        name="connection-dropped",
        proves="a TV that hangs up mid-command is reported as unreachable, rc 2, no traceback",
        server_args=["--close", "1"],
        conf={}, command="STATUS",
        expect_rc=2, expect_attempts=1, expect_sessions=1,
    ),
    Case(
        # Sanity: the knobs must not break the protocol they are decorating.
        name="jittery-but-sound",
        proves="latency plus jitter changes timing only; the command still succeeds",
        server_args=["--calibrated", "--jitter", "25", "--seed", "7"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="OFF",
        expect_rc=0, expect_attempts=2, expect_sessions=2,
    ),
]


def run_case(case, workdir):
    case_dir = workdir / case.name
    case_dir.mkdir()

    pairing_db = case_dir / "pairing.sqlite"
    journal = case_dir / "tv.jsonl"
    conf = case_dir / "lgpowercontrol.conf"
    # No input unless the case asks for one: a timing case must never spend a round trip on
    # switchInput, and the guarded cases carry their own HDMI_INPUT for SHARED_TV to read.
    rig.write_conf(conf, **{"HDMI_INPUT": "", **case.conf})

    with rig.VirtualTv(journal, case.server_args) as tv:
        started = time.monotonic()
        stderr = ""
        try:
            result = rig.run_cli(case_dir, conf, pairing_db, case.command,
                                 timeout=HANG_TIMEOUT)
            rc = result.returncode
            stderr = result.stderr
        except subprocess.TimeoutExpired as expired:
            rc = HANGS
            stderr = (expired.stderr or b"").decode(errors="replace")
        elapsed = time.monotonic() - started
        records = tv.records()

    return {
        "rc": rc,
        "ms": f"{elapsed * 1000:.0f}",
        "elapsed": elapsed,
        "attempts": sum(1 for r in records if r["event"] == "handshake"),
        "sessions": sum(1 for r in records if r["event"] == "connect"),
        "uris": [r["uri"] for r in records if r["event"] == "request"],
        "lost": sum(1 for r in records if r["event"] in ("drop", "hang-up")),
        "stderr": stderr.strip(),
    }


def check(case, outcome):
    problems = []
    if outcome["rc"] != case.expect_rc:
        problems.append(f"exit {outcome['rc']}, expected {case.expect_rc}")
    for label, expected in (("attempts", case.expect_attempts),
                            ("sessions", case.expect_sessions)):
        if expected is not None and outcome[label] != expected:
            problems.append(f"{outcome[label]} {label}, expected {expected}")
    # Only a floor is asserted. An upper bound would measure this machine's load, not the code.
    if case.min_seconds is not None and outcome["elapsed"] < case.min_seconds:
        problems.append(f"finished in {outcome['ms']}ms, faster than the "
                        f"{case.min_seconds * 1000:.0f}ms of delay it was given")
    if case.expect_stderr and case.expect_stderr not in outcome["stderr"]:
        problems.append(f"stderr does not mention {case.expect_stderr}")
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="Run lgpowercontrol against a slow and a lossy virtual TV.")
    parser.add_argument("--keep", action="store_true",
                        help="keep the per-case working directories and print the path")
    parser.add_argument("--only", metavar="NAME", help="run a single case by name")
    rig.add_target_argument(parser)
    args = parser.parse_args()

    rig.use_target(args.target)

    rig.assert_safe_config()

    cases = CASES
    if args.only:
        cases = [c for c in CASES if c.name == args.only]
        if not cases:
            sys.exit(f"no case named {args.only!r}; have: {', '.join(c.name for c in CASES)}")

    version, path = rig.preflight(sys.executable, "check_power_off_guard", "shared_tv_app_id")
    print(f"testing lgpowercontrol {version} at {path}")

    workdir = Path(tempfile.mkdtemp(prefix="virtual-webos-tv-timing-"))
    rows = []
    for case in cases:
        outcome = run_case(case, workdir)
        rows.append((case.name, outcome, check(case, outcome)))

    rig.summarise(rows, [("exit", "rc"), ("elapsed ms", "ms"),
                         ("attempts", "attempts"), ("sessions", "sessions")])

    for name, outcome, problems in rows:
        if problems and outcome["stderr"]:
            print(f"--- {name} stderr ---\n{outcome['stderr']}\n")

    measured = {name: values for name, values, _ in rows}
    if {"calibrated-off", "calibrated-status"} <= measured.keys():
        one = float(measured["calibrated-status"]["ms"])
        two = float(measured["calibrated-off"]["ms"])
        # Only the difference is comparable to the hardware figure. The absolute numbers here
        # are whole CLI processes and carry ~100ms of interpreter startup and imports that the
        # hardware measurement - which timed the client calls alone - never included.
        print(f"guard cost at the calibrated times: {two - one:.0f}ms extra for the second "
              f"session. Real hardware measured ~67ms. The totals ({one:.0f}ms and {two:.0f}ms) "
              f"are whole processes and are not comparable to the 71/138ms measured there.")
        print("Either way this is our own budget - it says nothing about the pre-down race.\n")

    for case, (_, outcome, _) in zip(cases, rows):
        print(f"{case.name}: {case.proves}")

    failures = [name for name, _, problems in rows if problems]
    rig.cleanup(workdir, args.keep)

    if failures:
        print(f"\n{len(failures)} of {len(rows)} cases failed: {', '.join(failures)}")
        return 1
    print(f"\nall {len(rows)} cases behaved as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
