#!/bin/bash

log() {
    echo $1 | logger --tag lgpowercontrol-dbus-events
}

power_cycle() {
    log "Attempting to $2"
    local MAX_RETRIES=10
    local DELAY=1
    local COUNT=1
    until eval "$1"; do
        ((COUNT++))
        if [ "$COUNT" -gt "$MAX_RETRIES" ]; then
            log "ERROR: Gave up after $MAX_RETRIES failed attempts to $2"
            break
        fi
        sleep $DELAY
    done
}

# Listen for screen saver (lock/unlock) events over DBus
dbus-monitor --session "type='signal',interface='org.DESKTOP_ENV.ScreenSaver'" |
    while read -r x; do
        case "$x" in
        *"boolean true"*) power_cycle "PWR_OFF_CMD" "power OFF TV" ;; # Screen lock
        *"boolean false"*) power_cycle "PWR_ON_CMD" "power ON TV" ;;  # Screen unlock
        esac
    done
