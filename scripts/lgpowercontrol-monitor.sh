#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    logger -t lgpowercontrol -p user.info -- "$1"
}

# Returns "on" if any connected DRM output is active, "off" if all connected
# outputs are inactive, or "" if no output is connected (e.g. mid-hotplug).
# Checks every connector by status instead of matching names, so it also
# works with eDP, DVI, VGA and virtual (VM) outputs.
get_drm_state() {
    local dir connected=0
    for dir in /sys/class/drm/card*-*/; do
        [[ -r "$dir/status" && -r "$dir/dpms" ]] || continue
        [[ $(< "$dir/status") == "connected" ]] || continue
        connected=1
        if [[ $(< "$dir/dpms") == "On" ]]; then
            echo "on"
            return
        fi
    done
    ((connected)) && echo "off"
    return 0 # empty output = indeterminate, must not trip set -e
}

trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT

log "DRM monitor started (MONITOR_MODE=${MONITOR_MODE})"

previous_state=$(get_drm_state)
log "Initial DRM state: ${previous_state:-unknown}"

while true; do
    current_state=$(get_drm_state)

    if [[ -n "$current_state" && "$current_state" != "$previous_state" ]]; then
        log "DRM state: ${previous_state:-unknown} -> ${current_state}"
        # A failed TV command must not kill the monitor (set -e), so log it instead.
        /opt/lgpowercontrol/lgpowercontrol "${current_state^^}" "$MONITOR_MODE" \
            || log "lgpowercontrol ${current_state^^} failed"
        previous_state=$current_state
    fi

    sleep 1
done