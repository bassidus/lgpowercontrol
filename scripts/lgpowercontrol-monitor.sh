#!/bin/bash
# LGPowerControl — screen DPMS monitor (system service mode)
# Watches DRM sysfs for display power state changes across all sessions.
# Falls back to logind IdleHint when DRM sysfs is unavailable.

set -euo pipefail

# shellcheck source=/dev/null
source /opt/lgpowercontrol/lgpowercontrol.conf

BIN=(/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP")

log() { logger -t lgpowercontrol -p "user.$1" "$2"; }

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
                        attempt=1
                        until "${BIN[@]}" turn_screen_off; do
                            log warning "turn_screen_off failed (attempt $attempt/5)"
                            (( attempt >= 5 )) && { log err "Giving up after 5 attempts"; break; }
                            (( attempt++ ))
                            sleep 3
                        done ;;
                esac
                ;;
            *:on)
                # Send WoL first — immediate, harmless if already on, wakes any standby
                # state without waiting for bscpylgtvcommand to time out on an unreachable TV.
                "${WOL_CMD[@]}"
                sleep 1
                # Also try turn_screen_on in case TV was only in Screen Off.
                "${BIN[@]}" turn_screen_on 2>/dev/null || true
                ;;
        esac
        prev=$state
    fi
    sleep 2
done
