#!/usr/bin/python3
"""Shared plumbing for the check_*.py rigs: start a virtual TV, run the real CLI, read back
what the TV was asked to do.

Nothing here asserts anything - the assertions live in the rigs, because what counts as a pass
is the interesting part and it should be readable next to the case that claims it.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
SERVER = REPO_DIR / "virtual_webos_tv.py"

# Where the lgpowercontrol under test is imported from.
#
# "tree" is the default and the one to reach for while changing code: it runs the working tree
# straight out of src/, so no install stands between the edit and the result. bscpylgtv still
# comes from the installed lib - it is not in the tree, and the system python has no websockets.
#
# "installed" is what these rigs did exclusively until now, and it stays for the case where the
# install itself is what is under test (wrappers, the pinned interpreter, the --target lib dir).
# Its trap is the reason preflight() checks which file actually won: the very first rig run went
# against 4.0.1, which predates the guard entirely, and every case took the plain OFF path while
# the table looked like it was saying something about the code that had just been written.
LGPC_REPO = REPO_DIR.parent.parent
TREE_SRC = LGPC_REPO / "src"
INSTALLED_LIB = Path("/opt/lgpowercontrol/lib")

TARGETS = ("tree", "installed")
TARGET = "tree"
TARGET_PATHS = [str(TREE_SRC), str(INSTALLED_LIB)]

TV_IP = "127.0.0.1"     # never anything else; assert_safe_config() enforces it
TV_PORT = 3001
WOL_PORT = 9

# A locally administered MAC, which by definition belongs to no manufacturer and so to no
# device. send_wol() always broadcasts to 255.255.255.255:9 and that packet does leave this
# machine - it just wakes nothing. assert_safe_config() refuses any globally administered
# address, which is what a real TV's MAC is.
TEST_MAC = "02:00:00:00:00:01"

# Runs the installed CLI with its conf, pairing database, lock file and off flag redirected.
#
# Both conf globals must be patched on `common` before `cli` is imported: cli does
# `from ...common import CONF_FILE, PAIRING_DB`, binding the values into its own namespace at
# import time, and Logger reads common.CONF_FILE in its constructor, which also runs then.
#
# ON_LOCK and TV_OFF_FLAG live in /run and are patched for a sharper reason: lgpowercontrol-
# monitor runs for real on this machine, and ON takes a non-blocking flock on
# /run/lgpowercontrol-on.lock whose whole purpose is to make a second ON return 0 and do
# nothing. A test holding that lock would silently swallow a real wake.
RUNNER = '''\
import sys
from pathlib import Path

lib_path, conf_file, pairing_db, run_dir = sys.argv[1:5]
cli_argv = sys.argv[5:]          # the command and any flags, passed through verbatim

for entry in reversed(lib_path.split(":")):
    sys.path.insert(0, entry)

from lgpowercontrol import common
common.CONF_FILE = Path(conf_file)
common.PAIRING_DB = Path(pairing_db)

from lgpowercontrol import cli
cli.CONF_FILE = Path(conf_file)
cli.PAIRING_DB = Path(pairing_db)
cli.ON_LOCK = Path(run_dir) / "on.lock"
cli.TV_OFF_FLAG = Path(run_dir) / "tv-off"

conf = common.load_conf(cli.CONF_FILE)
if conf.get("LGTV_IP") != "%(tv_ip)s":
    sys.exit(99)          # would have talked to something that is not the virtual TV
if conf.get("LGTV_MAC", "%(mac)s") != "%(mac)s":
    sys.exit(99)          # would have sent a magic packet addressed to a real device

sys.argv = ["lgpowercontrol", *cli_argv]
sys.exit(cli.main())
''' % {"tv_ip": TV_IP, "mac": TEST_MAC}  # noqa: UP031 - the template emits %s of its own

# A stale install is the failure mode worth catching early: 4.0.1 had no guard at all, so every
# case ran the plain OFF path and the table blamed the pairing counts instead of the install.
# The origin check is the second half of that: with two possible sources on sys.path, "which one
# did I just test?" has to be answered by the file that was imported, not by the flag that was
# passed. An installed copy shadowing the tree would otherwise look exactly like a green run.
PREFLIGHT = '''\
import sys
for entry in reversed(%(path)r):
    sys.path.insert(0, entry)
from lgpowercontrol import cli
missing = [n for n in %(names)r if not hasattr(cli, n)]
if missing:
    sys.exit("lgpowercontrol at %%s has no %%s" %% (cli.__file__, ", ".join(missing)))
if not cli.__file__.startswith(%(path)r[0]):
    sys.exit("expected lgpowercontrol from %%s, imported %%s" %% (%(path)r[0], cli.__file__))
print(cli.__file__)
'''


def assert_safe_config():
    """Refuse to run at all if a future edit could point a test at real hardware."""
    if TV_IP != "127.0.0.1":
        sys.exit(f"refusing to run: TV_IP is {TV_IP!r}, not loopback")
    first_octet = int(TEST_MAC.split(":")[0], 16)
    if not first_octet & 0x02:
        sys.exit(f"refusing to run: TEST_MAC {TEST_MAC} is globally administered, so it could "
                 f"be a real device's address")


def add_target_argument(parser):
    """Every rig takes the same --target, so they all read the same thing on the same run."""
    parser.add_argument("--target", choices=TARGETS, default=TARGET,
                        help="run the working tree (default) or the installed copy in /opt")


def use_target(target):
    """Pick which lgpowercontrol the rig imports. Call before preflight()."""
    global TARGET, TARGET_PATHS
    if target not in TARGETS:
        sys.exit(f"unknown target {target!r}; expected one of {', '.join(TARGETS)}")
    TARGET = target
    # The installed lib stays on the path in tree mode: bscpylgtv lives there, and the system
    # python has no websockets of its own. src/ comes first, so the tree wins for our own package.
    TARGET_PATHS = ([str(TREE_SRC)] if target == "tree" else []) + [str(INSTALLED_LIB)]


def target_version():
    """What to call the code under test. Never taken from installed metadata in tree mode - the
    installed dist-info is still on the path and would happily report a version the tree is not."""
    if TARGET == "tree":
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (LGPC_REPO / "pyproject.toml").read_text())
        return f"{match.group(1) if match else '?'} (working tree)"
    found = sorted(INSTALLED_LIB.glob("lgpowercontrol-*.dist-info"))
    version = found[-1].name[len("lgpowercontrol-"):-len(".dist-info")] if found else "?"
    return f"{version} (installed)"


def preflight(python, *names):
    """Return (version, path) of the lgpowercontrol under test, or exit saying what is wrong."""
    result = subprocess.run([python, "-c", PREFLIGHT % {"names": names, "path": TARGET_PATHS}],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        hint = ("check out the branch that carries them" if TARGET == "tree"
                else "install the build that carries them first")
        sys.exit(f"cannot run these checks: {result.stderr.strip()}\n{hint}.")
    return target_version(), result.stdout.strip()


def write_conf(path, **values):
    """load_conf() runs shlex.split and keeps whitespace inside the quotes."""
    lines = [f'LGTV_IP="{TV_IP}"', f'LGTV_MAC="{TEST_MAC}"', 'LOGGING="off"']
    lines += [f'{key}="{value}"' for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n")


def read_journal(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class VirtualTv:
    """The server as a subprocess, for the life of one case."""

    def __init__(self, journal, args):
        self.journal_path = Path(journal)
        self.args = list(args)
        self.process = None

    def __enter__(self):
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER), "--host", TV_IP, "--port", str(TV_PORT),
             "--wol-port", str(WOL_PORT), "--mac", TEST_MAC,
             "--journal", str(self.journal_path), *self.args],
            # stdin closed on purpose: the server reads control commands from a terminal, and
            # an inherited stdin could otherwise let it eat input meant for the rig.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            self.wait_ready()
        except Exception:
            self.process.kill()
            raise
        return self

    def wait_ready(self, timeout=10.0):
        """Wait for the `ready` line. The TCP port may legitimately be closed - a TV that
        starts in a state it does not answer TCP in is the whole point of --offline-states."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"virtual TV exited early with {self.process.returncode}")
            if any(r["event"] == "ready" for r in read_journal(self.journal_path)):
                return
            time.sleep(0.02)
        raise RuntimeError("virtual TV never reported ready")

    def wol_listening(self):
        return any(r["event"] == "wol-listening" for r in self.records())

    def records(self):
        return read_journal(self.journal_path)

    def __exit__(self, *exc):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        return False


def run_cli(case_dir, conf, pairing_db, command, timeout=120, capture=True):
    run_dir = case_dir / "run"
    run_dir.mkdir(exist_ok=True)
    runner = case_dir / "run_cli.py"
    runner.write_text(RUNNER)
    argv = [command] if isinstance(command, str) else list(command)
    return subprocess.run(
        [sys.executable, str(runner), ":".join(TARGET_PATHS),
         str(conf), str(pairing_db), str(run_dir), *argv],
        capture_output=capture, text=True, timeout=timeout, check=False,
    )


def can_bind_wol():
    """Port 9 is privileged. Without it the wake-from-standby cases cannot run at all."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind((TV_IP, WOL_PORT))
        return True
    except OSError:
        return False
    finally:
        probe.close()


SKIPPED = "skipped"


def summarise(rows, columns):
    """Print the result table.

    rows:    (name, values dict, problems list); problems == [SKIPPED] means not run.
    columns: (heading, key) pairs read out of each row's values dict.
    """
    headings = ["case"] + [heading for heading, _ in columns]
    cells = [[name] + [str(values.get(key, "-")) for _, key in columns]
             for name, values, _ in rows]
    widths = [max(len(headings[i]), *(len(row[i]) for row in cells))
              for i in range(len(headings))]

    print()
    print("  ".join(h.ljust(w) for h, w in zip(headings, widths)) + "  result")
    print("  ".join("-" * w for w in widths) + "  ------")
    for (_, _, problems), row in zip(rows, cells):
        if problems == [SKIPPED]:
            verdict = SKIPPED
        elif problems:
            verdict = "FAIL: " + "; ".join(problems)
        else:
            verdict = "ok"
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)) + "  " + verdict)
    print()


def cleanup(workdir, keep):
    if keep:
        print(f"working directories kept under {workdir}")
        return
    # Under sudo the server may have left root-owned files in there.
    subprocess.run(["rm", "-rf", str(workdir)], check=False)


def running_as_root():
    return os.geteuid() == 0
