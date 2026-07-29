#!/bin/bash
# Transitional shim for updates from the bash versions (<= 2.13). Their
# update.sh downloads the new tree, copies the user's old lgpowercontrol.conf
# into it and runs ./install.sh - so carry the settings over to lgtvpc.conf
# and hand off to the Python installer. Fresh installs never touch this file
# (the README says ./install.py). Remove once pre-3.0 installs have died out.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f lgpowercontrol.conf ]]; then
    while IFS= read -r line; do
        [[ $line =~ ^([A-Z_0-9]+)= ]] || continue
        key="${BASH_REMATCH[1]}"
        # Only keys that still exist; dropped ones (BOOT_SHUTDOWN_MODE,
        # MONITOR_MODE, WOL_L3, ...) are silently left behind.
        if grep -q "^${key}=" lgtvpc.conf; then
            # Escape the characters sed treats specially in a replacement.
            repl=${line//\\/\\\\}; repl=${repl//|/\\|}; repl=${repl//&/\\&}
            sed -i "s|^${key}=.*|${repl}|" lgtvpc.conf
        fi
    done < lgpowercontrol.conf
fi

exec ./install.py
