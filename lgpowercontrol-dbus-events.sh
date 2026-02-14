#!/bin/bash
#
# LGPowerControl DBus Event Listener
# Monitors screen lock/unlock events and controls TV power accordingly
# Configured during installation for GNOME, KDE, or Cinnamon desktop environments

# Log file location
LOG_FILE="$HOME/.local/lgpowercontrol/dbus-events.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Function to log events
log_event() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Listen for screen saver (lock/unlock) events over DBus
dbus-monitor --session "DESKTOP_ENV" |
    grep --line-buffered -E '^\s+boolean (true|false)' |
    while read -r line; do
        case "$line" in
        *"boolean true"*) 
            log_event "Screen locked - turning TV off"
            PWR_OFF_CMD 
            log_event "TV off command executed"
            ;;
        *"boolean false"*) 
            log_event "Screen unlocked - waiting 1s before turning TV on"
            sleep 1
            PWR_ON_CMD
            log_event "TV on command executed"
            ;;
        esac
    done