#!/bin/bash
# LGPowerControl — screen DPMS monitor (system service mode)
# Watches DRM sysfs for display power state changes across all sessions.
# Falls back to logind IdleHint when DRM sysfs is unavailable.

set -euo pipefail

# shellcheck source=/dev/null
source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    local level="$1" msg="$2"
    [[ "$level" == "debug" && "${LOG_LEVEL:-info}" != "debug" ]] && return 0
    logger -t lgpowercontrol -p "user.$level" -- "$msg"
}

# Returns "on", "off", or "" (indeterminate).
get_drm_state() {
    local drm_found=0 any_connected=0 d

    for d in /sys/class/drm/card*/card*-*/; do
        [[ -f "${d}status" ]] || continue
        drm_found=1

        [[ $(< "${d}status") == "connected" ]] || continue
        any_connected=1

        [[ ! -f "${d}dpms" ]] && { echo on; return; }
        [[ $(< "${d}dpms") == "On" ]] && { echo on; return; }
    done

    (( any_connected )) && { echo off; return; }
    (( drm_found )) && return  # DRM present but no connected displays — indeterminate

    # DRM sysfs unavailable — fall back to logind IdleHint across all sessions.
    log debug "DRM sysfs unavailable — falling back to logind IdleHint"
    local session type idle
    while IFS= read -r session; do
        type=$(loginctl show-session "$session" -p Type 2>/dev/null | cut -d= -f2)
        [[ "$type" == "x11" || "$type" == "wayland" || "$type" == "mir" ]] || continue

        idle=$(loginctl show-session "$session" -p IdleHint 2>/dev/null | cut -d= -f2)
        [[ "$idle" == "no" ]] && { echo on; return; }
    done < <(loginctl --no-legend list-sessions 2>/dev/null | awk '{print $1}')

    echo off
}

trap 'log info "Monitor stopped"; exit 0' SIGTERM SIGINT

log info "DRM monitor started (MONITOR_MODE=$MONITOR_MODE, LOG_LEVEL=${LOG_LEVEL:-info})"

prev=$(get_drm_state)
log info "Initial DRM state: ${prev:-unknown}"

while true; do
    state=$(get_drm_state)
    log debug "Poll: drm=${state:-unknown}"

    if [[ -n "$state" && "$state" != "$prev" ]]; then
        log info "DRM state: ${prev:-unknown} -> $state"

        case "$state" in
            off)
                /opt/lgpowercontrol/lgpowercontrol OFF "$MONITOR_MODE" \
                    || log warning "lgpowercontrol OFF returned non-zero"
                ;;
            on)
                /opt/lgpowercontrol/lgpowercontrol ON "$MONITOR_MODE" \
                    || log warning "lgpowercontrol ON returned non-zero"
                ;;
        esac

        prev=$state
    fi

    sleep 2
done
