#!/usr/bin/env python3
# Fallback TV-off/on for immutable distros where scripts/sleep.py can't be installed (read-only /usr).
# See CLAUDE.md ("Immutable-OS fallback") for why a delay inhibitor is safe here despite being a
# dead end elsewhere, and why the grace wait below is needed.
import os
import subprocess
import sys
import time

from lgtvpc_common import (
    CONF_FILE,
    SLEEP_FLAG,
    Logger,
    fallback_tv_off,
    fallback_tv_on,
    load_conf,
)

log = Logger("sleep-listener")

MATCH = (
    "type='signal',sender='org.freedesktop.login1',"
    "path='/org/freedesktop/login1',"
    "interface='org.freedesktop.login1.Manager',member='PrepareForSleep'"
)


# killing the child process releases the lock; avoids the fd-passing D-Bus call
# that taking it directly would need (no D-Bus library in the stdlib)
def take_inhibitor() -> subprocess.Popen:
    return subprocess.Popen(
        [
            "systemd-inhibit", "--what=sleep", "--mode=delay", "--who=lgtvpc",
            "--why=Turning the TV off before suspend", "sleep", "infinity",
        ]
    )


def main() -> None:
    if os.access(CONF_FILE, os.R_OK):
        log.configure(load_conf(CONF_FILE))

    inhibitor = take_inhibitor()
    monitor = subprocess.Popen(
        ["busctl", "--system", "monitor", "--match", MATCH],
        stdout=subprocess.PIPE,
        text=True,
    )

    # the match above means the only BOOLEAN lines are PrepareForSleep's payload
    for line in monitor.stdout:
        if "BOOLEAN true" in line:
            for _ in range(10):  # grace wait: let pre-down's flag win the race if it fires
                if SLEEP_FLAG.exists():
                    break
                time.sleep(0.1)
            else:
                fallback_tv_off(log, "sleep-listener")
            inhibitor.terminate()
            inhibitor.wait()
        elif "BOOLEAN false" in line:
            inhibitor = take_inhibitor()  # re-arm before turning the TV on
            fallback_tv_on(log, "sleep-listener")

    sys.exit(1)  # busctl exiting means the system bus went away; let systemd restart us


if __name__ == "__main__":
    main()
