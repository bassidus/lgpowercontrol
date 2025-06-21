#!/bin/bash

log() {
    echo "$(date '+%F %T') $1" | logger --tag listen-for-lock-unlock-events
}

power_off_tv() {
    local MAX_RETRIES=10
    local DELAY=1
    local COUNT=1
    until <PWR_OFF_CMD>; do
        ((COUNT++))
        if [ "$COUNT" -gt "$MAX_RETRIES" ]; then
            log "ERROR: Gave up after $MAX_RETRIES failed attempts to power OFF TV"
            break
        fi
        sleep $DELAY
    done
}

power_on_tv() {
    local MAX_RETRIES=10
    local DELAY=1
    local COUNT=1
    until <PWR_ON_CMD>; do
        ((COUNT++))
        if [ "$COUNT" -gt "$MAX_RETRIES" ]; then
            log "ERROR: Gave up after $MAX_RETRIES failed attempts to send Wake-on-LAN packet"
            break
        fi
        sleep $DELAY
    done
}

# Listen for screen saver (lock/unlock) events over DBus
dbus-monitor --session "type='signal',interface='org.freedesktop.ScreenSaver'" |
    while read -r x; do
        case "$x" in
        *"boolean true"*)  power_off_tv ;; # Screen lock
        *"boolean false"*) power_on_tv  ;; # Screen unlock
        esac
    done
