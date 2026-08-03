#!/bin/bash
# Migration shim for bash installs (<= 2.13); remove once obsolete.
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f lgpowercontrol.conf ]]; then
    while IFS= read -r line; do
        [[ $line =~ ^([A-Z_0-9]+)= ]] || continue
        key="${BASH_REMATCH[1]}"
        # Skip keys no longer present in lgtvpc.conf.
        if grep -q "^${key}=" lgtvpc.conf; then
            # Escape sed replacement metacharacters.
            repl=${line//\\/\\\\}; repl=${repl//|/\\|}; repl=${repl//&/\\&}
            sed -i "s|^${key}=.*|${repl}|" lgtvpc.conf
        fi
    done < lgpowercontrol.conf
fi

exec ./install.py
