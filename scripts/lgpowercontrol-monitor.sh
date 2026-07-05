#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    logger -t lgpowercontrol -p user.info -- "$1"
}

# Returns "on" if any connected DRM output is active, "off" otherwise.
# Checks every connector by status instead of matching names, so it also
# works with eDP, DVI, VGA and virtual (VM) outputs.
get_drm_state() {
    local dir
    for dir in /sys/class/drm/card*-*/; do
        [[ -r "$dir/status" && -r "$dir/dpms" ]] || continue
        if [[ $(< "$dir/status") == "connected" && $(< "$dir/dpms") == "On" ]]; then
            echo "on"
            return
        fi
    done
    echo "off"
}

trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT

log "DRM monitor started (MONITOR_MODE=${MONITOR_MODE})"

previous_state=$(get_drm_state)
log "Initial DRM state: ${previous_state}"

while true; do
    current_state=$(get_drm_state)

    if [[ "$current_state" != "$previous_state" ]]; then
        log "DRM state: ${previous_state} -> ${current_state}"
        # Pass state as uppercase (ON/OFF) to match lgpowercontrol's expected argument.
        /opt/lgpowercontrol/lgpowercontrol "${current_state^^}" "$MONITOR_MODE"
        previous_state=$current_state
    fi

    sleep 1
done