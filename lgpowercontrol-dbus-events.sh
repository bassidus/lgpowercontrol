#!/bin/bash
# LGPowerControl DBus Event Listener
# Usage: configured by install.sh — do not edit directly

dbus-monitor --session "DESKTOP_ENV" |
    grep --line-buffered -E '^\s+boolean (true|false)' |
    while read -r line; do
        case "$line" in
        *"boolean true"*)  LOCK_CMD ;;
        *"boolean false"*) UNLOCK_CMD ;;
        esac
    done
