#!/bin/bash
# LGPowerControl — screen DPMS monitor (system service mode)
# Watches DRM sysfs for display power state changes across all sessions.
# Falls back to logind IdleHint when DRM sysfs is unavailable.

set -euo pipefail

# shellcheck source=/dev/null
source /opt/lgpowercontrol/lgpowercontrol.conf

# LOG_LEVEL: error → 0, info → 1, debug → 2
case "${LOG_LEVEL:-info}" in
    error) _LOG_NUM=0 ;;
    debug) _LOG_NUM=2 ;;
    *)     _LOG_NUM=1 ;;
esac

log() {
    local level="$1" msg="$2"
    case "$level" in
        debug)        [[ $_LOG_NUM -ge 2 ]] || return 0 ;;
        info|warning) [[ $_LOG_NUM -ge 1 ]] || return 0 ;;
        err)          ;;  # always log errors
    esac
    logger -t lgpowercontrol -p "user.$level" "$msg"
}

# Returns "on", "off", or "" (indeterminate).
get_drm_state() {
    local drm_found=0 any_connected=0 d

    for d in /sys/class/drm/card*/card*-*/; do
        [[ -f "${d}status" ]] || continue
        drm_found=1

        [[ $(< "${d}status") == "connected" ]] || continue
        any_connected=1

        if [[ ! -f "${d}dpms" ]]; then
            echo on
            return
        fi

        if [[ $(< "${d}dpms") == "On" ]]; then
            echo on
            return
        fi
    done

    if (( any_connected )); then
        echo off
        return
    fi

    if (( drm_found )); then
        return  # DRM present but no connected displays — indeterminate
    fi

    # DRM sysfs unavailable — fall back to logind IdleHint across all sessions.
    log debug "DRM sysfs unavailable — falling back to logind IdleHint"
    local session type idle
    while IFS= read -r session; do
        type=$(loginctl show-session "$session" -p Type 2>/dev/null | cut -d= -f2)
        [[ "$type" == "x11" || "$type" == "wayland" || "$type" == "mir" ]] || continue

        idle=$(loginctl show-session "$session" -p IdleHint 2>/dev/null | cut -d= -f2)
        if [[ "$idle" == "no" ]]; then
            echo on
            return
        fi
    done < <(loginctl --no-legend list-sessions 2>/dev/null | awk '{print $1}')

    echo off
}

trap 'log info "Monitor stopped"; exit 0' SIGTERM SIGINT

log info "Screen state monitor started (MONITOR_MODE=$MONITOR_MODE, LOG_LEVEL=${LOG_LEVEL:-info})"

prev=$(get_drm_state)
log info "Initial screen state: ${prev:-unknown}"

while true; do
    state=$(get_drm_state)
    log debug "Poll: screen=${state:-unknown}"

    if [[ -n "$state" && "$state" != "$prev" ]]; then
        log info "Screen state: ${prev:-unknown} -> $state"

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
