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

# Turning the TV off before sleep is handled by the NetworkManager dispatcher
# script (90-lgpowercontrol) — NM kills the network within milliseconds of
# PrepareForSleep, so its blocking pre-down window is the only reliable spot.
# This watcher owns the wake side: turn the TV back on when sleep ends.
watch_sleep() {
    dbus-monitor --system \
        "type='signal',sender='org.freedesktop.login1',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'" |
    while read -r line; do
        case "$line" in
        *"boolean true"*)
            log "System going to sleep"
            ;;
        *"boolean false"*)
            log "System woke up"
            rm -f /run/lgpowercontrol-sleep
            # Network wait and WoL retries live in lgpowercontrol itself,
            # so every caller (resume, DRM change, boot) gets them.
            /opt/lgpowercontrol/lgpowercontrol ON "$MONITOR_MODE" \
                || log "lgpowercontrol ON failed after resume"
            ;;
        esac
    done
    log "Sleep watcher exited unexpectedly"
}

trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT

watch_sleep &

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