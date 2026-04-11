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
    until "$@" 2>&1; do
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

        # In screen mode, check the TV's state before acting — turn_screen_on/off are
        # only valid from certain states and will be rejected otherwise (e.g. Active Standby).
        tv_state=
        [[ "$MONITOR_MODE" != "power" ]] && tv_state=$(get_tv_state)

        case "$MONITOR_MODE:$state" in
            power:off) "${BIN[@]}" power_off ;;
            power:on)  "${WOL_CMD[@]}" ;;
            *:off)
                # Skip if TV is already off; avoids a rejected command from Active Standby.
                if [[ "$tv_state" == "Active Standby" || "$tv_state" == "Screen Off" ]]; then
                    log info "TV already off (state: ${tv_state:-unknown}), skipping"
                else
                    run_with_retry "${BIN[@]}" turn_screen_off || true
                fi
                ;;
            *:on)
                # From Active Standby the TV needs WoL to wake; turn_screen_on won't work.
                if [[ "$tv_state" == "Active" ]]; then
                    log info "TV already on, skipping"
                elif [[ "$tv_state" == "Active Standby" ]]; then
                    log info "TV in deep standby, waking with WoL"
                    "${WOL_CMD[@]}"
                else
                    # Brief delay so the TV has time to register the screen-on event.
                    sleep 1; run_with_retry "${BIN[@]}" turn_screen_on || true
                fi
                ;;
        esac
        prev=$state
    fi
    sleep 2
done
