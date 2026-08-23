#!/usr/bin/env python3
# Checks the installation, which is the one thing run_all.py cannot: its suites test code, this
# tests the layout that code was put into. A green sweep says the tree behaves; this says the tree
# is what /opt is actually running, with the ownership, units and links install.py meant to leave.
#
#     ./tests/check_installation.py
#
# Needs no root and changes nothing. It does need the TV for the last check, and says so rather
# than failing when the TV is off.
#
# Exit: 0 every check ran and passed, 1 something failed, 2 passed but a check could not be made
# (no pairing key yet, no NetworkManager, a TV that is off). 2 is not green - it is the exit code
# this project already has a scar from treating as green, so it is spelled out here too.
#
# The counterpart of the byte-comparison below has cost time twice: a stale copy in /opt looks
# exactly like a fresh one, so a rig ran against 4.0.1 while the table appeared to say something
# about the new code. Nothing here trusts a version string alone.
import argparse
import filecmp
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_DIR = Path("/opt/lgpowercontrol")
LIB_DIR = INSTALL_DIR / "lib"

PASSED, FAILED, INCOMPLETE = 0, 1, 2

BOLD, GREEN, RED, YELLOW, RESET = "\033[1m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.skipped = 0

    def ok(self, text: str) -> None:
        print(f"  {GREEN}ok{RESET}    {text}")

    def bad(self, text: str) -> None:
        print(f"  {RED}FAIL{RESET}  {text}")
        self.failures += 1

    # Not a failure and not a pass: the check never ran. Kept apart from both for the same reason
    # the rigs keep their skips apart - a check that could not be made must not read as green.
    def skip(self, text: str) -> None:
        print(f"  {YELLOW}skip{RESET}  {text}")
        self.skipped += 1

    def check(self, condition: bool, good: str, bad: str) -> bool:
        self.ok(good) if condition else self.bad(bad)
        return condition


# The paths install.py lays down outside INSTALL_DIR live in the package, so they are imported
# from the installed copy rather than repeated here - a second copy of these names is a second
# thing to drift. Importing them from /opt is also the first real check: a package that cannot be
# imported from where the wrappers point is an installation that cannot run at all.
def load_installed_layout(report: Report):
    sys.path.insert(0, str(LIB_DIR))
    try:
        import lgpowercontrol
        from lgpowercontrol import uninstall
    except ImportError as exc:
        report.bad(f"cannot import lgpowercontrol from {LIB_DIR}: {exc}")
        return None

    # The import must have come from /opt and not from the src/ tree next door. Everything below
    # compares the two, and a checker reading src/ while claiming to read /opt would pass whatever
    # it was pointed at - which is exactly the failure mode this file exists to catch.
    origin = Path(lgpowercontrol.__file__ or "").resolve()
    if LIB_DIR.resolve() not in origin.parents:
        report.bad(f"imported lgpowercontrol from {origin}, not from {LIB_DIR}")
        return None
    return uninstall


def check_code_matches_tree(report: Report) -> None:
    version_dirs = list(LIB_DIR.glob("lgpowercontrol-*.dist-info"))
    match = re.search(r'^version\s*=\s*"(.*)"', (REPO_ROOT / "pyproject.toml").read_text(), re.MULTILINE)
    want = match.group(1) if match else ""
    installed = version_dirs[0].name.split("-", 1)[1].removesuffix(".dist-info") if version_dirs else ""
    report.check(installed == want and bool(want),
                 f"version {installed} matches pyproject.toml",
                 f"installed version {installed!r} is not pyproject's {want!r}")

    # The version above can be right while the files are old - a reinstall that failed halfway,
    # or an install from a different clone. Comparing the bytes is what actually answers it.
    stale = []
    for source in sorted((REPO_ROOT / "src" / "lgpowercontrol").glob("*.py")):
        installed_file = LIB_DIR / "lgpowercontrol" / source.name
        if not installed_file.is_file():
            stale.append(f"{source.name} (missing)")
        elif not filecmp.cmp(source, installed_file, shallow=False):
            stale.append(source.name)
    report.check(not stale,
                 "every src/lgpowercontrol/*.py is byte-identical to the installed copy",
                 "the installation does not match the working tree: " + ", ".join(stale))


# conf_was is the template the installer was given, when the caller has one. install.py rewrites
# LGTV_MAC and nothing else, so that line is the only difference allowed.
def check_conf(report: Report, conf_file: Path, conf_was: Path | None) -> None:
    if not conf_file.is_file():
        report.bad(f"{conf_file} is missing")
        return
    text = conf_file.read_text()

    ip = re.search(r'^LGTV_IP="(.*)"', text, re.MULTILINE)
    report.check(bool(ip and ip.group(1)),
                 f"LGTV_IP is set ({ip.group(1) if ip else ''})",
                 "LGTV_IP is empty - the installation cannot reach any TV")

    if conf_was is None:
        return
    changed = [
        line for line in conf_was.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and line not in text.splitlines() and not line.startswith("LGTV_MAC=")
    ]
    report.check(not changed,
                 "the conf handed to the installer came through intact",
                 "the installed conf lost settings: " + "; ".join(changed))


# INSTALL_DIR stays root-owned with the sticky bit while the user owns the two data files - the
# whole argument for that split is in the comment above set_ownership() in install.py. Read the
# expected group off the conf's owner rather than off whoever runs this, so the check means the
# same thing under sudo as it does without.
def check_ownership(report: Report, conf_file: Path, pairing_db: Path) -> None:
    stat = INSTALL_DIR.stat()
    mode = stat.st_mode & 0o7777
    owner_ok = stat.st_uid == 0 and mode == 0o1775
    report.check(owner_ok,
                 f"{INSTALL_DIR} is {mode:04o} root-owned and sticky",
                 f"{INSTALL_DIR} is {mode:04o} uid {stat.st_uid}, expected 1775 root - "
                 "without the sticky bit the user can rename bin/ and have root exec their file")

    for directory in ("bin", "lib"):
        path = INSTALL_DIR / directory
        directory_stat = path.stat()
        report.check(directory_stat.st_uid == 0 and directory_stat.st_gid == 0,
                     f"{directory}/ is root:root",
                     f"{directory}/ is not root:root - root execs it at boot and at suspend")

    owner = None
    for path, required in ((conf_file, True), (pairing_db, False)):
        if not path.is_file():
            if required:
                report.bad(f"{path.name} is missing")
            else:
                report.skip("no pairing key yet - finish with: lgpowercontrol authorize")
            continue
        file_stat = path.stat()
        if file_stat.st_uid == 0:
            report.bad(f"{path.name} is root-owned - editing it and re-pairing will need sudo")
            continue
        owner = pwd.getpwuid(file_stat.st_uid)
        report.ok(f"{path.name} belongs to {owner.pw_name}")

    # Once, not once per file: group write on INSTALL_DIR is what lets that user replace their
    # own two files, and without their group the sticky-bit arrangement gives them nothing.
    if owner is not None:
        report.check(stat.st_gid == owner.pw_gid,
                     f"{INSTALL_DIR} is group-writable for {owner.pw_name}",
                     f"{INSTALL_DIR} is group {stat.st_gid}, not {owner.pw_name}'s {owner.pw_gid}"
                     f" - {owner.pw_name} cannot replace their own files")


def systemctl(*args: str) -> str:
    return subprocess.run(["systemctl", *args], capture_output=True, text=True,
                          check=False).stdout.strip()


def check_units(report: Report, layout) -> None:
    for name in ("boot", "shutdown", "monitor"):
        state = systemctl("is-enabled", f"lgpowercontrol-{name}.service")
        report.check(state == "enabled",
                     f"lgpowercontrol-{name}.service enabled",
                     f"lgpowercontrol-{name}.service is {state or 'not installed'!r}")
    state = systemctl("is-active", "lgpowercontrol-monitor.service")
    report.check(state == "active", "monitor service running",
                 f"monitor service is {state or 'dead'!r} - nothing watches the screen")

    # Two ways to reach the same behaviour, and a system has exactly one of them: the sleep hook,
    # or the listener service where /usr is read-only and the hook cannot be installed.
    hook = Path(layout.SLEEP_HOOK_LINK[1])
    listener_unit = layout.UNITS["sleep"].path
    if hook.is_symlink():
        report.ok(f"sleep hook installed ({hook})")
        if listener_unit.is_file():
            report.bad(f"the listener unit is installed as well ({listener_unit}) - "
                       "two paths would both turn the TV off")
    elif listener_unit.is_file():
        state = systemctl("is-active", "lgpowercontrol-sleep.service")
        report.check(state == "active",
                     "listener fallback running (read-only /usr)",
                     f"the listener is the installed path but the service is {state or 'dead'!r}")
    else:
        report.bad("neither the sleep hook nor the listener is installed - "
                   "nothing turns the TV off at suspend")


def check_links(report: Report, layout) -> None:
    # A real file in dispatcher.d/, never a symlink into /opt: SELinux labels an exec by its
    # target, and the label is what decides whether the dispatcher can read logind at all. The
    # comment on DISPATCHER_SHIM has the measurement.
    if Path(layout.DISPATCHER_DIR).is_dir():
        shim = Path(layout.DISPATCHER_SHIM)
        report.check(shim.is_file() and not shim.is_symlink() and os.access(shim, os.X_OK),
                     f"dispatcher shim in place ({shim})",
                     f"dispatcher shim missing, a symlink, or not executable: {shim}")
        predown = Path(layout.PREDOWN_LINK[1])
        report.check(predown.is_symlink(),
                     "pre-down link in place",
                     f"pre-down link missing ({predown}) - the suspend event never arrives")
    else:
        report.skip(f"no {layout.DISPATCHER_DIR} (systemd-networkd?) - "
                    "TV-off at suspend is unsupported there by design")

    local_bin = Path(layout.LOCAL_BIN_LINK[1])
    report.check(local_bin.is_symlink(), f"on PATH via {local_bin}",
                 f"{local_bin} is missing - the command is not on PATH")


# End to end, the way it is used. A TV that is off is not a broken installation, so it counts as
# a check that could not be made rather than as a failure.
def check_it_runs(report: Report, lgpc_bin: Path) -> None:
    result = subprocess.run([str(lgpc_bin), "STATUS"], capture_output=True, text=True, check=False)
    output = " ".join((result.stdout + result.stderr).split())
    if result.returncode == 0:
        state = next((line for line in result.stdout.splitlines()
                      if line.startswith("state=")), output)
        # bscpylgtv prints one of these per retry. More than none means the TV dropped a connect
        # just now - it succeeded, so this is not a failure, but it is the exact flakiness that
        # makes a one-attempt budget on the suspend path a bad idea. Worth saying out loud.
        retries = result.stderr.count("Connection attempt:")
        took = f" (took {retries + 1} connect attempts - the TV dropped one)" if retries else ""
        report.ok(f"lgpowercontrol STATUS -> {state}{took}")
    elif result.returncode == 2:
        report.skip(f"TV unreachable, so STATUS proves nothing either way: {output}")
    else:
        report.bad(f"lgpowercontrol STATUS exited {result.returncode}: {output}")


# pip and sudo both like leaving root-owned things behind in the clone; the next ordinary run
# then cannot overwrite them, which reads as a mysterious permission error much later.
def check_repo_clean(report: Report) -> None:
    leftovers = [
        path for path in REPO_ROOT.rglob("*")
        if ".git" not in path.parts and path.lstat().st_uid == 0
    ]
    report.check(not leftovers,
                 "no root-owned files left in the clone",
                 "root-owned files in the clone: "
                 + ", ".join(str(p.relative_to(REPO_ROOT)) for p in leftovers[:5]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the installation in /opt/lgpowercontrol.")
    parser.add_argument("--conf-was", type=Path, metavar="FILE",
                        help="the conf template the installer was given, to check it came through")
    args = parser.parse_args()

    print(f"\n{BOLD}=== checking {INSTALL_DIR} ==={RESET}")
    report = Report()

    if not INSTALL_DIR.is_dir():
        report.bad(f"{INSTALL_DIR} does not exist - nothing is installed")
        return FAILED

    layout = load_installed_layout(report)
    if layout is None:
        return FAILED

    check_code_matches_tree(report)
    check_conf(report, layout.INSTALL_DIR / "lgpowercontrol.conf", args.conf_was)
    check_ownership(report, layout.INSTALL_DIR / "lgpowercontrol.conf",
                    layout.INSTALL_DIR / ".aiopylgtv.sqlite")
    check_units(report, layout)
    check_links(report, layout)
    check_it_runs(report, layout.BIN_DIR / "lgpowercontrol")
    check_repo_clean(report)

    print(f"\n{BOLD}=== summary ==={RESET}")
    if report.failures:
        print(f"  {RED}{report.failures} check(s) failed{RESET}"
              + (f", {report.skipped} could not be made" if report.skipped else ""))
        return FAILED
    if report.skipped:
        print(f"  {YELLOW}installation looks right, but {report.skipped} check(s) never ran{RESET}")
        return INCOMPLETE
    print(f"  {GREEN}installation verified{RESET}")
    return PASSED


if __name__ == "__main__":
    sys.exit(main())
