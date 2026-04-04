#!/bin/bash
# LGPowerControl — screen DPMS monitor
# Polls DRM sysfs for display power state; falls back to xset/logind.

set -euo pipefail

resolve_session_id() {
    if [[ -n "${XDG_SESSION_ID:-}" ]]; then
        echo "$XDG_SESSION_ID"
        return
    fi
    loginctl --no-legend list-sessions 2>/dev/null \
        | awk -v u="$USER" '$3==u && $4!="" {print $1; exit}'
}

SESSION_ID=$(resolve_session_id)
if [[ -z "$SESSION_ID" ]]; then
    logger -t lgpowercontrol -p user.err "Cannot determine session ID"
    exit 1
fi
logger -t lgpowercontrol -p user.info "Activity monitor started (session $SESSION_ID)"

# Returns: "on", "off", or "" (indeterminate — no state change).
# 1. DRM sysfs dpms (Wayland + X11). 2. xset (X11-only). 3. logind IdleHint.
get_screen_state() {
    local drm_found=false any_connected=false d

    for d in /sys/class/drm/card*/card*-*/; do
        [[ -f "${d}status" ]] || continue
        drm_found=true
        [[ $(< "${d}status") == "connected" ]] || continue
        any_connected=true
        [[ -f "${d}dpms" ]] || { echo on; return; }
        [[ $(< "${d}dpms") == "On" ]] && { echo on; return; }
    done

    $any_connected && { echo off; return; }
    $drm_found && return

    if [[ -n "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" && "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
        case $(xset q 2>/dev/null | awk '/Monitor is/{print $3}') in
            Off|Standby|Suspend) echo off; return ;;
            On)                  echo on;  return ;;
        esac
    fi

    case $(loginctl show-session "$SESSION_ID" -p IdleHint 2>/dev/null | grep -o 'yes\|no') in
        yes) echo off ;;
        no)  echo on  ;;
    esac
}

prev=""
while true; do
    state=$(get_screen_state)
    if [[ -n "$state" && "$state" != "$prev" ]]; then
        logger -t lgpowercontrol -p user.info "Screen state: ${prev:-unknown} -> $state"
        case "$state" in
            off) PWR_OFF_CMD ;;
            on)  PWR_ON_CMD  ;;
        esac
        prev=$state
    fi
    sleep 2
done
