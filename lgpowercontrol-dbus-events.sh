#!/bin/bash
#
# LGPowerControl Activity Monitor
# Monitors screen DPMS state and controls TV power accordingly.

resolve_session_id() {
    if [[ -n "$XDG_SESSION_ID" ]]; then
        echo "$XDG_SESSION_ID"
        return
    fi
    loginctl --no-legend list-sessions 2>/dev/null \
        | awk -v u="$USER" '$3==u && $4!="" {print $1; exit}'
}

SESSION_ID=$(resolve_session_id)

if [[ -z "$SESSION_ID" ]]; then
    logger -t "lgpowercontrol" -p "user.err" "Activity monitor: could not determine logind session ID. Exiting."
    exit 1
fi

logger -t "lgpowercontrol" -p "user.info" "Activity monitor started (session $SESSION_ID)"

# Returns "on", "off", or "" (unknown — keep last known state).
#
# Detection order:
#   1. DRM sysfs — works on both Wayland and X11. KDE's Screen Energy Saving writes
#      here directly; xset/logind IdleHint are unreliable on Wayland for this.
#      Returns "" when no connector is connected (TV unplugged) to avoid a false flip.
#   2. X11 DPMS (xset q) — native X11 only; skipped if Wayland is present.
#   3. logind IdleHint — fallback for GNOME and other compositors.
get_screen_state() {
    local drm_found=false any_connected=false conn_dpms conn_status conn_dir

    for conn_dir in /sys/class/drm/card*/card*-*/; do
        [[ -f "${conn_dir}status" ]] || continue
        drm_found=true
        conn_status=$(< "${conn_dir}status")
        [[ "$conn_status" == "connected" ]] || continue
        any_connected=true
        [[ -f "${conn_dir}dpms" ]] || { echo "on"; return; }
        conn_dpms=$(< "${conn_dir}dpms")
        [[ "$conn_dpms" == "On" ]] && { echo "on"; return; }
    done

    $any_connected && { echo "off"; return; }
    $drm_found     && return   # connected displays absent — return "" (no state change)

    if [[ -n "$DISPLAY" && -z "$WAYLAND_DISPLAY" && "$XDG_SESSION_TYPE" != "wayland" ]]; then
        local xdpms
        xdpms=$(xset q 2>/dev/null | awk '/Monitor is/{print $3}')
        case "$xdpms" in
            Off|Standby|Suspend) echo "off"; return ;;
            On)                  echo "on";  return ;;
        esac
    fi

    local hint
    hint=$(loginctl show-session "$SESSION_ID" -p IdleHint 2>/dev/null | grep -o 'yes\|no')
    case "$hint" in
        yes) echo "off" ;;
        no)  echo "on"  ;;
    esac
}

prev_state=""
while true; do
    state=$(get_screen_state)

    if [[ -n "$state" && "$state" != "$prev_state" ]]; then
        logger -t "lgpowercontrol" -p "user.info" "Screen state: ${prev_state:-unknown} -> $state"
        case "$state" in
            off) PWR_OFF_CMD ;;
            on)  PWR_ON_CMD  ;;
        esac
        prev_state="$state"
    fi

    sleep 2
done
