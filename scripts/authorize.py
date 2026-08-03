#!/usr/bin/env python3
# STATUS both triggers the pairing dialog and validates the key. Only rc 3 (denied/unpaired)
# means the key itself is broken - rc 2 (unreachable) must NOT wipe a valid key.
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
