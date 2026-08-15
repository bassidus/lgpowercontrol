#!/usr/bin/python3
"""Drives lgpowercontrol's wake loop (the ON command) against virtual_webos_tv.py.

This is the part CLAUDE.md calls the real value of stage 2: the loop rests entirely on
turn_screen_on answering -102 ambiguously and get_power_state telling the two cases apart, and
until now that could only be exercised by suspending real hardware and watching a TV.

Read CLAUDE.md section 6 before trusting a green run: this proves the loop's branches against
our model of webOS, not webOS. In particular it can never say anything about the pre-down race,
which runs against NetworkManager flushing the interface, not against the TV.
"""

import argparse
import sys
import tempfile
from pathlib import Path

import rig


class Case:
    def __init__(self, name, proves, server_args, conf, command,
                 expect_rc, expect_state, expect_uris=None, expect_absent=None,
                 needs_wol=False):
        self.name = name
        self.proves = proves
        self.server_args = server_args
        self.conf = conf
        self.command = command
        self.expect_rc = expect_rc
        self.expect_state = expect_state          # TV's power state when the case ends
        self.expect_uris = expect_uris or []      # endpoints that must have been called
        self.expect_absent = expect_absent or []  # endpoints that must NOT have been called
        self.needs_wol = needs_wol


SCREEN_ON = "com.webos.service.tvpower/power/turnOnScreen"
POWER_STATE = "com.webos.service.tvpower/power/getPowerState"
SET_INPUT = "tv/switchInput"

CASES = [
    Case(
        name="already-awake",
        proves="-102 with the TV awake is mapped to success; the loop stops on attempt 1",
        server_args=["--power-state", "Active"],
        conf={}, command="ON",
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        name="screen-off",
        proves="turn_screen_on really turns the screen on when it was off",
        server_args=["--power-state", "Screen Off"],
        conf={}, command="ON",
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        name="screen-saver",
        proves="Screen Saver counts as awake, same as Active and Screen Off",
        server_args=["--power-state", "Screen Saver"],
        conf={}, command="ON",
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        # Always Ready standby: the TV answers, and says Active Standby. is_on() is false there,
        # so the loop must resend WoL rather than trust the socket it just used successfully.
        name="active-standby-wol",
        proves="a readable standby state resends WoL and waits out the ~4s wake",
        server_args=["--power-state", "Active Standby"],
        conf={}, command="ON", needs_wol=True,
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        # Deep standby with the network parked: get_power_state fails outright.
        name="deep-standby-offline",
        proves="rc 2 from a TV in deep standby resends WoL rather than giving up",
        server_args=["--power-state", "Suspend", "--offline-states", "Suspend"],
        conf={}, command="ON", needs_wol=True,
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        # The same TV under the opposite assumption: deep standby that still answers. Nobody
        # knows which is true of a real C3, and the loop is written to survive both - so both
        # are tested rather than one being picked. See DEFAULT_OFFLINE_STATES in the server.
        name="deep-standby-readable",
        proves="a readable Suspend takes the same WoL resend path as an unreachable one",
        server_args=["--power-state", "Suspend", "--offline-states", ""],
        conf={}, command="ON", needs_wol=True,
        expect_rc=0, expect_state="Active", expect_uris=[POWER_STATE, SCREEN_ON],
    ),
    Case(
        # 15 attempts at ~1s each, so this is the slow one. It is also the only case that
        # proves the loop ever stops.
        name="never-wakes",
        proves="WAKE_ATTEMPTS is exhausted and ON gives up with rc 1, not a hang",
        server_args=["--power-state", "Active Standby", "--ignore-wol"],
        conf={}, command="ON",
        expect_rc=1, expect_state="Active Standby", expect_absent=[SCREEN_ON],
    ),
    Case(
        # SET_INPUT_ATTEMPTS exists because "the app layer can lag a wake from deep standby".
        name="set-input-after-lag",
        proves="switchInput is retried until the app layer catches up, then lands on HDMI 2",
        server_args=["--power-state", "Screen Off", "--input-lag-seconds", "2"],
        conf={"HDMI_INPUT": "2"}, command="ON",
        expect_rc=0, expect_state="Active", expect_uris=[SCREEN_ON, SET_INPUT],
    ),
    Case(
        # Commit 76d15d4: on a shared TV the wake does not yank back the input that suspend
        # deliberately left alone. The TV here starts in Screen Off, i.e. awake, so it was
        # never ours to claim - a TV found in standby still gets switched.
        name="shared-tv-keeps-input",
        proves="SHARED_TV holds the input on a TV that was already awake",
        server_args=["--power-state", "Screen Off", "--app-id", "com.webos.app.hdmi2"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="ON",
        expect_rc=0, expect_state="Active", expect_uris=[SCREEN_ON], expect_absent=[SET_INPUT],
    ),
    Case(
        # The off side of the state machine, which stage 1 could not assert on.
        name="off-lands-in-standby",
        proves="system/turnOff leaves the TV in Active Standby, where is_on() is false",
        server_args=["--power-state", "Active"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="OFF",
        expect_rc=0, expect_state="Active Standby",
    ),
    Case(
        name="screen-off-command",
        proves="SCREEN_OFF passes the guard and leaves the TV in Screen Off, not standby",
        server_args=["--power-state", "Active"],
        conf={"HDMI_INPUT": "1", "SHARED_TV": "1"}, command="SCREEN_OFF",
        expect_rc=0, expect_state="Screen Off",
    ),
]


def final_state(records):
    """The last state the TV reported. `ready` carries the starting one."""
    state = None
    for record in records:
        if record["event"] == "ready":
            state = record["state"]
        elif record["event"] == "state":
            state = record["to"]
    return state


def run_case(case, workdir):
    case_dir = workdir / case.name
    case_dir.mkdir()

    # A fresh pairing database per case: bscpylgtv stores the key per IP, and a key left behind
    # by an earlier case changes how a pairing failure surfaces (CLAUDE.md section 2).
    pairing_db = case_dir / "pairing.sqlite"
    journal = case_dir / "tv.jsonl"
    conf = case_dir / "lgpowercontrol.conf"
    rig.write_conf(conf, **case.conf)

    with rig.VirtualTv(journal, case.server_args) as tv:
        if case.needs_wol and not tv.wol_listening():
            raise RuntimeError("the virtual TV could not bind the WoL port")
        result = rig.run_cli(case_dir, conf, pairing_db, case.command)
        records = tv.records()

    uris = [r["uri"] for r in records if r["event"] == "request"]
    return {
        "rc": result.returncode,
        "state": final_state(records),
        "wol": sum(1 for r in records if r["event"] == "wol"),
        "uris": uris,
        "stderr": result.stderr.strip(),
    }


def check(case, outcome):
    problems = []
    if outcome["rc"] != case.expect_rc:
        problems.append(f"exit {outcome['rc']}, expected {case.expect_rc}")
    if outcome["state"] != case.expect_state:
        problems.append(f"ended in {outcome['state']!r}, expected {case.expect_state!r}")
    for uri in case.expect_uris:
        if uri not in outcome["uris"]:
            problems.append(f"{uri.rsplit('/', 1)[-1]} was never called")
    for uri in case.expect_absent:
        if uri in outcome["uris"]:
            problems.append(f"{uri.rsplit('/', 1)[-1]} was called and should not have been")
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="Run lgpowercontrol's wake loop against the virtual TV.")
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

    wol = rig.can_bind_wol()
    if not wol:
        print(f"UDP port {rig.WOL_PORT} needs root - wake-on-LAN cases will be skipped. "
              f"Re-run with sudo for the full table.")

    workdir = Path(tempfile.mkdtemp(prefix="virtual-webos-tv-wake-"))
    rows = []
    for case in cases:
        if case.needs_wol and not wol:
            rows.append((case.name, {"rc": "-", "state": "-", "wol": "-"}, [rig.SKIPPED]))
            continue
        outcome = run_case(case, workdir)
        rows.append((case.name, outcome, check(case, outcome)))

    rig.summarise(rows, [("exit", "rc"), ("final state", "state"), ("WoL", "wol")])
    for case, (_, outcome, problems) in zip(cases, rows):
        if problems == [rig.SKIPPED]:
            continue
        print(f"{case.name}: {case.proves}")
        print(f"  endpoints called: {', '.join(outcome['uris']) or '(none)'}")

    failures = [name for name, _, problems in rows if problems and problems != [rig.SKIPPED]]
    skipped = [name for name, _, problems in rows if problems == [rig.SKIPPED]]
    rig.cleanup(workdir, args.keep)

    if failures:
        print(f"\n{len(failures)} of {len(rows)} cases failed: {', '.join(failures)}")
        return 1
    ran = len(rows) - len(skipped)
    print(f"\nall {ran} cases behaved as expected")
    if skipped:
        # Not a pass. A rig that exits 0 with a third of its cases unrun is the "feels like
        # verification" trap this project already has a scar from; 2 means incomplete.
        print(f"{len(skipped)} skipped for want of root: {', '.join(skipped)}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
