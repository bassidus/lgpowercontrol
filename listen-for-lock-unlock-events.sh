#!/bin/bash

# Listen for screen saver (lock/unlock) events over DBus
dbus-monitor --session "type='signal',interface='org.freedesktop.ScreenSaver'" |
    while read -r x; do
        case "$x" in
        *"boolean true"*)
            MAX_RETRIES=10
            DELAY=5
            COUNT=1
            until <PWR_OFF_CMD>; do
                ((COUNT++))
                if [ "$COUNT" -gt "$MAX_RETRIES" ]; then
                    echo "ERROR: Gave up after $MAX_RETRIES failed attempts to power OFF TV"
                    break
                fi
                sleep $DELAY
            done
            ;;

        *"boolean false"*)
            if ! <PWR_ON_CMD>; then
                echo "ERROR: Failed to send Wake-on-LAN packet"
            fi
            ;;
        esac
    done