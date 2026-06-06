#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    logger -t lgpowercontrol -p user.info -- "$1"
}

# Check whether any connected DRM display is currently powered on.
get_drm_state() {
    if grep -q "On" /sys/class/drm/card*/card*-{DP,HDMI}*/dpms 2>/dev/null; then
        echo "on"
    else
        echo "off"
    fi
}

# Stop cleanly when the process receives SIGTERM or SIGINT.
trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT

log "DRM monitor started (MONITOR_MODE=${MONITOR_MODE})"

previous_state=$(get_drm_state)
log "Initial DRM state: ${previous_state}"

while true; do
    current_state=$(get_drm_state)

    if [[ "$current_state" != "$previous_state" ]]; then
        log "DRM state: ${previous_state} -> ${current_state}"
        /opt/lgpowercontrol/lgpowercontrol "${current_state^^}" "$MONITOR_MODE"
        previous_state=$current_state
    fi

    sleep 1
done