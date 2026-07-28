#!/usr/bin/env python3
# Verifies the TV pairing, triggering a new authorization dialog when the
# key is missing or invalid (e.g. after a factory reset). STATUS both
# triggers the dialog and validates the key; a denied dialog leaves a
# broken key file behind, so remove it and retry. A merely unreachable TV
# (exit code 2) must NOT wipe a valid key - only exit code 3 (not paired/
# denied) means the key itself is the problem.
import os
import subprocess
import sys

from lgtvpc_common import CONF_FILE, LGTVPC, PAIRING_DB, require_root


def main() -> None:
    require_root()

    if not os.access(CONF_FILE, os.R_OK):
        sys.exit("LGPowerControl is not installed.")

    if not PAIRING_DB.is_file():
        print("TV Authorization - A dialog will appear on your TV screen - accept it with the remote.")

    while True:
        rc = subprocess.run(
            [LGTVPC, "STATUS"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        if rc == 0:
            break

        if rc == 3:
            PAIRING_DB.unlink(missing_ok=True)
            print("Authorization failed or was denied on the TV.")
        else:
            print(f"Could not reach the TV (exit code {rc}). Make sure it's on and connected.")
        input("Press Enter to show a new dialog on the TV (Ctrl+C to abort): ")

    print("TV authorization OK!")


if __name__ == "__main__":
    main()
