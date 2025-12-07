#!/bin/bash

# Listen for screen saver (lock/unlock) events over DBus
dbus-monitor --session "DESKTOP_ENV" |
    grep --line-buffered -E '^\s+boolean (true|false)' |
    while read -r line; do
        case "$line" in
        *"boolean true"*) PWR_OFF_CMD ;; # Screen lock
        *"boolean false"*) PWR_ON_CMD ;; # Screen unlock
        esac
    done
