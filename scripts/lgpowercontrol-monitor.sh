#!/bin/bash
# LGPowerControl — screen DPMS monitor (system service mode)
# Watches DRM sysfs for display power state changes across all sessions.
# Falls back to logind IdleHint when DRM sysfs is unavailable.

set -euo pipefail

# shellcheck source=/dev/null
source /opt/lgpowercontrol/lgpowercontrol.conf

BIN=(/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP")

log() { logger -t lgpowercontrol -p "user.$1" "$2"; }

# Retry a command up to 5 times with a 3-second delay, logging each failure.
run_with_retry() {
    local attempt=1 max=5
    until "$@"; do
        log warning "Command failed (attempt $attempt/$max): $*"
        (( attempt >= max )) && { log err "Giving up after $max attempts: $*"; return 1; }
        (( attempt++ ))
        sleep 3
    done
}

# Query the TV's current power state (e.g. "Active", "Screen Off", "Active Standby").
# bscpylgtvcommand prints a Python dict, so we match single-quoted keys/values.
get_tv_state() {
    "${BIN[@]}" get_power_state 2>/dev/null \
        | sed -n "s/.*'state': '\([^']*\)'.*/\1/p" || true
}

# Returns "on", "off", or "" (indeterminate).
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

trap 'log info "Monitor stopped"; exit 0' SIGTERM SIGINT

log info "Screen monitor started"

prev=$(get_screen_state)
log info "Initial screen state: ${prev:-unknown}"

while true; do
    state=$(get_screen_state)
    if [[ -n "$state" && "$state" != "$prev" ]]; then
        log info "Screen state: ${prev:-unknown} -> $state"

        case "$MONITOR_MODE:$state" in
            power:off) "${BIN[@]}" power_off ;;
            power:on)  "${WOL_CMD[@]}" ;;
            *:off)
                # Skip if TV is already off; avoids a rejected command.
                case "$(get_tv_state)" in
                    "Active Standby"|"Screen Off")
                        log info "TV already off, skipping" ;;
                    *)
                        run_with_retry "${BIN[@]}" turn_screen_off || true ;;
                esac
                ;;
            *:on)
                # Try turn_screen_on; fall back to WoL if it fails (e.g. TV auto-powered
                # down to Active Standby while screen was off).
                sleep 1
                if ! "${BIN[@]}" turn_screen_on 2>/dev/null; then
                    log info "turn_screen_on failed, falling back to WoL"
                    "${WOL_CMD[@]}"
                    sleep 2
                    tv_state=$(get_tv_state)
                    case "$tv_state" in
                        "Active"|"Screen On")
                            log info "TV woke via WoL, state: $tv_state" ;;
                        "Screen Off")
                            log info "TV in Screen Off after WoL, turning on"
                            run_with_retry "${BIN[@]}" turn_screen_on || true ;;
                        *)
                            log warning "WoL didn't wake TV (state: ${tv_state:-unknown})" ;;
                    esac
                fi
                ;;
        esac
        prev=$state
    fi
    sleep 2
done
