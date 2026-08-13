#!/usr/bin/env python3
# One entry point for everything that can be checked without a real TV:
#
#     ./tests/run_all.py            the working tree, ~1 min
#     sudo ./tests/run_all.py       ...including the three Wake-on-LAN cases
#     ./tests/run_all.py --target installed    what /opt actually runs
#
# Two suites, deliberately kept apart and run in this order:
#
#   test_*.py    189 unittest cases against src/, no TV, no root, no network. Sub-second, so it
#                runs first: there is no point starting a websocket server to discover that a
#                conf value stopped parsing.
#   check_*.py   the virtual-TV rigs. Real sockets, a server subprocess per case, and the TV's
#                journal as the assertion - which is what makes them slow and what makes them
#                worth the minute.
#
# Neither replaces the VM round or the hardware check. This only removes the reason to spend
# those on something a virtual TV would have caught in ten seconds.
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RIG_DIR = REPO_ROOT / "tests" / "virtual-webos-tv"

RIGS = ("check_guard_paths.py", "check_wake_paths.py", "check_timing_paths.py")

# Rig exit codes. 2 is not a failure and not a pass: it means a case never ran, and reporting
# that as green is the exact mistake this project already has a scar from.
PASSED, FAILED, INCOMPLETE = 0, 1, 2

# Bytecode is never written by anything started here: a run under sudo would otherwise leave
# root-owned __pycache__ directories in the tree that the next ordinary run cannot rewrite.
CHILD_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def run(label, command, cwd):
    print(f"\n\033[1m=== {label} ===\033[0m", flush=True)
    started = time.monotonic()
    rc = subprocess.run(command, cwd=cwd, env=CHILD_ENV, check=False).returncode
    return label, rc, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every check that needs no real TV.")
    parser.add_argument("--target", choices=("tree", "installed"), default="tree",
                        help="run the working tree (default) or the installed copy in /opt")
    parser.add_argument("--unit-only", action="store_true", help="skip the virtual-TV rigs")
    parser.add_argument("--rigs-only", action="store_true", help="skip the unittest suite")
    args = parser.parse_args()

    results = []

    if not args.rigs_only:
        results.append(run("unittest discover", [sys.executable, "-m", "unittest", "discover"],
                           cwd=REPO_ROOT))

    if not args.unit_only:
        for rig in RIGS:
            results.append(run(rig, [sys.executable, str(RIG_DIR / rig), "--target", args.target],
                               cwd=RIG_DIR))

    width = max(len(label) for label, _, _ in results)
    verdicts = {PASSED: "ok", FAILED: "FAILED", INCOMPLETE: "INCOMPLETE - cases were skipped"}

    print("\n\033[1m=== summary ===\033[0m")
    for label, rc, seconds in results:
        print(f"{label.ljust(width)}  {seconds:5.1f}s  {verdicts.get(rc, f'exit {rc}')}")

    codes = [rc for _, rc, _ in results]
    if any(rc not in (PASSED, INCOMPLETE) for rc in codes):
        return FAILED
    if INCOMPLETE in codes:
        # Wake-on-LAN needs UDP port 9, which is privileged. Three cases cannot run without it.
        if os.geteuid() != 0:
            print("\nRun again with sudo to cover the Wake-on-LAN cases.")
        return INCOMPLETE
    return PASSED


if __name__ == "__main__":
    sys.exit(main())
