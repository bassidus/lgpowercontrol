#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    [[ "${LOGGING:-yes}" == "no" ]] && return 0
    logger -t lgpowercontrol -p user.info -- "$1"
}

get_dpms_state() {
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

previous_state=$(get_dpms_state)
log "DPMS monitor started - Initial state: ${previous_state:-unknown}"

# The TV drops into deep standby ~13 min after a mere screen-off (~10 s
# wake). A full power_off before that threshold lands it in Always Ready
# instead (~3-4 s wake) on TVs that have it enabled; on others it's a
# no-op wake-wise. One-shot per screen-off period.
escalate_after=600
off_seconds=0

while true; do
    current_state=$(get_dpms_state)

    if [[ -n "$current_state" && "$current_state" != "$previous_state" ]]; then
        log "DPMS state: ${previous_state:-unknown} -> ${current_state}"
        # At suspend the dispatcher has already turned the TV off (and the
        # network may be gone); its flag file marks that window.
        if [[ "$current_state" == off && -e /run/lgpowercontrol-sleep ]]; then
            log "Suspend in progress - TV already off via dispatcher"
        else
            if [[ "$current_state" == on ]]; then cmd=ON; else cmd=SCREEN_OFF; fi
            # A failed TV command must not kill the monitor (set -e), so log it instead.
            /opt/lgpowercontrol/lgpowercontrol "$cmd" \
                || log "lgpowercontrol ${cmd} failed"
        fi
        previous_state=$current_state
        off_seconds=0
    fi

    if [[ "$previous_state" == off ]]; then
        off_seconds=$((off_seconds + 1))
        if [[ $off_seconds -eq $escalate_after && ! -e /run/lgpowercontrol-sleep ]]; then
            log "Screen off for 10 min - escalating to full power off (fast wake via Always Ready)"
            /opt/lgpowercontrol/lgpowercontrol OFF \
                || log "lgpowercontrol OFF failed"
        fi
    fi

    sleep 1
done