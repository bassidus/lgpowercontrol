#!/usr/bin/python3
"""Drives lgpowercontrol's check_power_off_guard() against virtual_webos_tv.py.

Runs the real CLI in a subprocess so the assertions are about the real exit code, and asks the
virtual TV's journal what it was actually told to do - because the interesting pair of cases
(guard proceeds vs guard stands down) both exit 0, and only the journal separates them.

Read CLAUDE.md section 6 before trusting a green run: this proves the guard's own branches,
not webOS.
"""

import argparse
import sys
import tempfile
from pathlib import Path

import rig

POWER_OFF = "system/turnOff"


class Case:
    def __init__(self, name, proves, server_args, hdmi_conf, command,
                 expect_rc, expect_turn_off, expect_registers, no_server=False):
        self.name = name
        self.proves = proves
        self.server_args = server_args
        self.hdmi_conf = hdmi_conf
        self.command = command
        self.expect_rc = expect_rc
        self.expect_turn_off = expect_turn_off
        self.expect_registers = expect_registers
        self.no_server = no_server


# The four outcomes of CLAUDE.md section 3, plus the already-verified unreachable row for a
# complete table. Note what the guard's two fail-open rows actually exit with: OFF fails open
# into tv_cmd("power_off"), which meets the same broken TV - so the 404 case still gets a
# working power_off (only get_current_app is faulted) and exits 0, while the unpaired case
# fails again and exits 3. Fail-open is proved by the second connection, not by the exit code.
CASES = [
    Case(
        name="on-our-input",
        proves="guard returns None, OFF proceeds and the TV is told to turn off",
        server_args=["--app-id", "com.webos.app.hdmi1"],
        hdmi_conf="1", command="OFF",
        expect_rc=0, expect_turn_off=True, expect_registers=2,
    ),
    Case(
        name="on-another-input",
        proves="guard returns 0, OFF is skipped and the TV is never told to turn off",
        server_args=["--app-id", "com.webos.app.hdmi2"],
        hdmi_conf="1", command="OFF",
        expect_rc=0, expect_turn_off=False, expect_registers=1,
    ),
    Case(
        name="endpoint-404",
        proves="PyLGTVServiceNotFoundError -> rc 1 -> guard fails open, OFF still happens",
        server_args=["--app-id", "com.webos.app.hdmi2", "--error", "current_app=not-found"],
        hdmi_conf="1", command="OFF",
        expect_rc=0, expect_turn_off=True, expect_registers=2,
    ),
    Case(
        # --app-id is deliberately the wrong input: if the guard were to read it despite the
        # pairing failure it would skip, and the second connection would not appear.
        name="pairing-refused",
        proves="PyLGTVPairException -> rc 3 -> guard fails open; OFF is attempted and fails 3",
        server_args=["--app-id", "com.webos.app.hdmi2", "--refuse-pairing"],
        hdmi_conf="1", command="OFF",
        expect_rc=3, expect_turn_off=False, expect_registers=2,
    ),
    Case(
        name="tv-unreachable",
        proves="ConnectionRefusedError -> rc 2 -> guard propagates 2 so monitor.py logs it",
        server_args=[], hdmi_conf="1", command="OFF",
        expect_rc=2, expect_turn_off=False, expect_registers=0, no_server=True,
    ),
    Case(
        # shared_tv_app_id(): anything that is not a plain input number of 1 or higher reads as
        # "not configured", because a typo would otherwise disable power-off for good with one
        # log line as the only symptom. The number moved to HDMI_INPUT in 4.2; SHARED_TV is on
        # in every case here, so this faults the only value the guard now builds its app id from.
        name="malformed-guard-value",
        proves="a malformed HDMI_INPUT is ignored rather than disabling power-off",
        server_args=["--app-id", "com.webos.app.hdmi2"],
        hdmi_conf="HDMI_2", command="OFF",
        expect_rc=0, expect_turn_off=True, expect_registers=1,
    ),
]


def run_case(case, workdir):
    case_dir = workdir / case.name
    case_dir.mkdir()

    # A fresh pairing database per case. bscpylgtv stores the key per IP, so a key left behind
    # by an earlier case would stop pairing-refused from raising at all - see CLAUDE.md sec. 2.
    pairing_db = case_dir / "pairing.sqlite"
    journal = case_dir / "tv.jsonl"
    conf = case_dir / "lgpowercontrol.conf"
    rig.write_conf(conf, HDMI_INPUT=case.hdmi_conf, SHARED_TV="1")

    if case.no_server:
        result = rig.run_cli(case_dir, conf, pairing_db, case.command)
        records = []
    else:
        with rig.VirtualTv(journal, case.server_args) as tv:
            result = rig.run_cli(case_dir, conf, pairing_db, case.command)
            records = tv.records()

    return {
        "rc": result.returncode,
        "registers": sum(1 for r in records if r["event"] == "register"),
        "turn_off": any(r["event"] == "request" and r["uri"] == POWER_OFF for r in records),
        "uris": [r["uri"] for r in records if r["event"] == "request"],
        "stderr": result.stderr.strip(),
    }


def check(case, outcome):
    problems = []
    if outcome["rc"] != case.expect_rc:
        problems.append(f"exit {outcome['rc']}, expected {case.expect_rc}")
    if outcome["turn_off"] != case.expect_turn_off:
        problems.append(f"system/turnOff seen={outcome['turn_off']}, "
                        f"expected {case.expect_turn_off}")
    if outcome["registers"] != case.expect_registers:
        problems.append(f"{outcome['registers']} pairing attempts, "
                        f"expected {case.expect_registers}")
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="Run lgpowercontrol's power-off guard against the virtual TV.")
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

    workdir = Path(tempfile.mkdtemp(prefix="virtual-webos-tv-guard-"))
    rows = []
    for case in cases:
        outcome = run_case(case, workdir)
        rows.append((case.name, outcome, check(case, outcome)))

    rig.summarise(rows, [("exit", "rc"), ("turnOff", "turn_off"), ("pairings", "registers")])
    for case, (_, outcome, _) in zip(cases, rows):
        print(f"{case.name}: {case.proves}")
        print(f"  endpoints called: {', '.join(outcome['uris']) or '(none)'}")

    failures = [name for name, _, problems in rows if problems]
    rig.cleanup(workdir, args.keep)

    if failures:
        print(f"\n{len(failures)} of {len(rows)} cases failed: {', '.join(failures)}")
        return 1
    print(f"\nall {len(rows)} cases behaved as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
