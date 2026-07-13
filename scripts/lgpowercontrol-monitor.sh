#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf
MONITOR_MODE="${MONITOR_MODE:-screen}" # conf may predate the key

log() {
    [[ "${LOGGING:-yes}" == "no" ]] && return 0
    logger -t lgpowercontrol -p user.info -- "$1"
}

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

# In screen mode the TV drops into deep standby ~13 min after screen-off
# (~10 s wake). A full power_off before that threshold lands it in Always
# Ready instead (~3-4 s wake) on TVs that have it enabled; on others it's a
# no-op wake-wise. One-shot per screen-off period.
escalate_after=600
off_seconds=0

while true; do
    current_state=$(get_drm_state)

    if [[ -n "$current_state" && "$current_state" != "$previous_state" ]]; then
        log "DRM state: ${previous_state:-unknown} -> ${current_state}"
        # At suspend the dispatcher has already turned the TV off (and the
        # network may be gone); its flag file marks that window.
        if [[ "$current_state" == off && -e /run/lgpowercontrol-sleep ]]; then
            log "Suspend in progress - TV already off via dispatcher"
        else
            # A failed TV command must not kill the monitor (set -e), so log it instead.
            /opt/lgpowercontrol/lgpowercontrol "${current_state^^}" "$MONITOR_MODE" \
                || log "lgpowercontrol ${current_state^^} failed"
        fi
        previous_state=$current_state
        off_seconds=0
    fi

    if [[ "$previous_state" == off ]]; then
        off_seconds=$((off_seconds + 1))
        if [[ "$MONITOR_MODE" == screen && $off_seconds -eq $escalate_after \
            && ! -e /run/lgpowercontrol-sleep ]]; then
            log "Screen off for 10 min - escalating to full power off (fast wake via Always Ready)"
            /opt/lgpowercontrol/lgpowercontrol OFF power \
                || log "lgpowercontrol OFF failed"
        fi
    fi

    sleep 1
done