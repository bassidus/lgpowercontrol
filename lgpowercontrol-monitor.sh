#!/bin/bash
# LGPowerControl — screen DPMS monitor (system service mode)
# Watches DRM sysfs for display power state changes across all sessions.
# Falls back to logind IdleHint when DRM sysfs is unavailable.

set -euo pipefail

INSTALL_PATH="$(dirname "$(realpath "$0")")"
CONFIG="$INSTALL_PATH/lgpowercontrol.conf"
[[ -f "$CONFIG" ]] || { echo "Error: config not found: $CONFIG" >&2; exit 1; }
# shellcheck source=/dev/null
source "$CONFIG"

BIN="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand -p $INSTALL_PATH/.aiopylgtv.sqlite $LGTV_IP"
MONITOR_MODE="${MONITOR_MODE:-screen}"

log() { logger -t lgpowercontrol -p "user.$1" "$2"; }

# Returns "on", "off", or "" (indeterminate).
# DRM sysfs is session-agnostic and works for both X11 and Wayland.
get_screen_state() {
    local drm_found=0 any_connected=0 d

    for d in /sys/class/drm/card*/card*-*/; do
        [[ -f "${d}status" ]] || continue
        drm_found=1
        [[ $(< "${d}status") == "connected" ]] || continue
        any_connected=1
        [[ -f "${d}dpms" ]] || { echo on; return; }
        [[ $(< "${d}dpms") == "On" ]] && { echo on; return; }
    done

    (( any_connected )) && { echo off; return; }
    (( drm_found ))     && return

    # DRM sysfs unavailable — fall back to logind IdleHint across all sessions.
    local session type idle
    while IFS= read -r session; do
        type=$(loginctl show-session "$session" -p Type 2>/dev/null | cut -d= -f2)
        [[ "$type" == "x11" || "$type" == "wayland" || "$type" == "mir" ]] || continue
        idle=$(loginctl show-session "$session" -p IdleHint 2>/dev/null | cut -d= -f2)
        [[ "$idle" == "no" ]] && { echo on; return; }
    done < <(loginctl --no-legend list-sessions 2>/dev/null | awk '{print $1}')

    echo off
}

log info "Screen monitor started"

prev=$(get_screen_state)
log info "Initial screen state: ${prev:-unknown}"

while true; do
    state=$(get_screen_state)
    if [[ -n "$state" && "$state" != "$prev" ]]; then
        log info "Screen state: ${prev:-unknown} -> $state"
        case "$state" in
            off)
                case "$MONITOR_MODE" in
                    power) $BIN power_off                        ;;
                    *)     $BIN turn_screen_off 2>&1 || true     ;;
                esac
                ;;
            on)
                case "$MONITOR_MODE" in
                    power) $WOL_CMD                              ;;
                    *)     sleep 1; $BIN turn_screen_on 2>&1 || true ;;
                esac
                ;;
        esac
        prev=$state
    fi
    sleep 2
done
