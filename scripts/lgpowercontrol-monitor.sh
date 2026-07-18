#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

# Tags the main script's log lines with who triggered the command.
export LGPC_SOURCE=dpms-monitor

log() {
    [[ "${LOGGING:-yes}" == "no" ]] && return 0
    logger -t lgpowercontrol -p user.info -- "dpms-monitor: $1"
}

# Returns "on" if any connected DRM output is active, "off" if all connected
# outputs are inactive, or "" if no output is connected (e.g. mid-hotplug).
# Checks every connector by status instead of matching names, so it also
# works with eDP, DVI, VGA and virtual (VM) outputs.
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

preparing_for_sleep() {
    busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
        org.freedesktop.login1.Manager PreparingForSleep 2> /dev/null | grep -q true
}

# Turning the TV off at suspend is handled by the NetworkManager dispatcher
# script (90-lgpowercontrol) — NM kills the network within milliseconds of
# PrepareForSleep, so its blocking pre-down window is the only reliable spot.
# At resume both the dispatcher's up event and this watcher fire ON; a flock
# in turn_tv_on deduplicates. This watcher follows DRM output state
# (screen blank/unblank) while the system is awake.

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
        transition="DPMS state: ${previous_state:-unknown} -> ${current_state}"
        # At suspend the dispatcher has already turned the TV off (and the
        # network may be gone); its flag file marks that window.
        if [[ "$current_state" == off && -e /run/lgpowercontrol-sleep ]] && preparing_for_sleep; then
            log "${transition} - suspend in progress, TV already off via dispatcher"
        else
            # A leftover flag (dispatcher 'up' never fired, e.g. the network
            # never came back after resume) would suppress every screen-off.
            if [[ "$current_state" == off && -e /run/lgpowercontrol-sleep ]]; then
                log "Stale sleep flag removed - no suspend in progress"
                rm -f /run/lgpowercontrol-sleep
            fi
            if [[ "$current_state" == on ]]; then
                cmd=ON
                log "${transition}, turning TV on"
            else
                cmd=SCREEN_OFF
                log "${transition}, turning screen off"
            fi
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