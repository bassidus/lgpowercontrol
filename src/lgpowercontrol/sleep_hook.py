# systemd-sleep hook: lgpowercontrol pre|post suspend|.... Fallback TV-off/on for NIC-WoL
# setups where NM skips the device and the dispatcher never fires (see CLAUDE.md).
import os
import sys

from lgpowercontrol.common import (
    CONF_FILE,
    SLEEP_FLAG,
    Logger,
    fallback_tv_off,
    fallback_tv_on,
    load_conf,
)

log = Logger("sleep-hook")


def main() -> None:
    if not os.access(CONF_FILE, os.R_OK):  # conf gone: project removed, hook left behind somehow
        return
    log.configure(load_conf(CONF_FILE))

    phase = sys.argv[1] if len(sys.argv) > 1 else ""

    if phase == "pre":
        if SLEEP_FLAG.exists():  # dispatcher already handled this suspend
            return
        fallback_tv_off(log, "sleep-hook")

    elif phase == "post":
        fallback_tv_on(log, "sleep-hook")
