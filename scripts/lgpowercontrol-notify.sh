#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    [[ "${LOGGING:-yes}" == "no" ]] && return 0
    logger -t lgpowercontrol -p user.info -- "notify-service: $1"
}

# Everything from the conf is untrusted text: a bad value must degrade to
# a default, never crash the service into a systemd restart loop.
[[ "${OFF_WARNING_SECONDS:-}" =~ ^[0-9]+$ ]] || {
    [[ -n "${OFF_WARNING_SECONDS:-}" ]] \
        && log "Invalid OFF_WARNING_SECONDS='${OFF_WARNING_SECONDS}' - using 120"
    OFF_WARNING_SECONDS=120
}
((OFF_WARNING_SECONDS > 0)) || exit 0

# KDE Plasma only — exit quietly on other desktop environments.
command -v kscreen-doctor &> /dev/null && command -v kreadconfig6 &> /dev/null || exit 0

poll_interval="${NOTIFY_POLL_SECONDS:-2}"
[[ "$poll_interval" =~ ^[0-9]+$ && "$poll_interval" -gt 0 ]] || poll_interval=2

read_powerdevil() { # args: profile key default
    kreadconfig6 --file powerdevilrc --group "$1" --group Display --key "$2" --default "$3"
}

# Reads Plasma's idle timeouts (seconds) for the currently active power
# profile. Called again on every dim (see arm_timer), so settings changes and
# AC/battery switches apply without restarting the service. The dim event is
# our only idle anchor: the warning fires notify_delay seconds after the
# screen dims. Non-numeric kreadconfig6 output falls back to the defaults.
compute_timings() {
    local def_dim=300 def_off=600
    profile=$(busctl --user call org.kde.Solid.PowerManagement /org/kde/Solid/PowerManagement \
        org.kde.Solid.PowerManagement currentProfile 2> /dev/null | awk -F'"' '{print $2}')
    case "$profile" in
        Battery)    def_dim=120 def_off=300 ;;
        LowBattery) def_dim=60  def_off=120 ;;
        *)          profile="AC" ;;
    esac

    dim_timeout=$(read_powerdevil "$profile" DimDisplayIdleTimeoutSec "$def_dim")
    [[ "$dim_timeout" =~ ^[0-9]+$ ]] || dim_timeout=$def_dim
    off_timeout=$(read_powerdevil "$profile" TurnOffDisplayIdleTimeoutSec "$def_off")
    [[ "$off_timeout" =~ ^[0-9]+$ ]] || off_timeout=$def_off

    notify_delay=$((off_timeout - dim_timeout - OFF_WARNING_SECONDS))
    ((notify_delay > 0)) || notify_delay=0
    remaining=$((off_timeout - dim_timeout - notify_delay))
}

# The notification id is passed via a file since send_notification runs in
# the timer subshell, which cannot set variables in the main process.
# XDG_RUNTIME_DIR is per-user and private; the mktemp fallback avoids a
# predictable (symlink-attackable) name in the world-writable /tmp and is
# removed by the exit trap below.
tmp_id_file=""
if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    id_file="$XDG_RUNTIME_DIR/lgpowercontrol-notify.id"
else
    id_file=$(mktemp)
    tmp_id_file=$id_file
fi

send_notification() {
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify "susssasa{sv}i" \
        "LGPowerControl" 0 "video-television" "TV turning off" \
        "The TV turns off in ${remaining} seconds. Move the mouse or press a key to keep it on." \
        0 0 $((remaining * 1000)) | awk '{print $2}' > "$id_file"
    log "Warning notification sent"
}

# Dismiss a still-visible warning as soon as activity ends the dim.
close_notification() {
    [[ -s "$id_file" ]] || return 0
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications CloseNotification u "$(< "$id_file")" 2> /dev/null || true
    : > "$id_file"
}

timer_pid=""

arm_timer() {
    if [[ -n "$timer_pid" ]] && kill -0 "$timer_pid" 2> /dev/null; then
        return 0
    fi
    compute_timings
    log "Screen dimmed; warning notification in ${notify_delay}s (profile=${profile})"
    # Re-check the dim before firing: sleep runs on CLOCK_MONOTONIC, which
    # freezes during suspend - a timer armed before a suspend would
    # otherwise fire its remaining seconds after resume, warning about a
    # screen-off that is no longer coming.
    (
        sleep "$notify_delay"
        screen_dimmed && send_notification
    ) &
    timer_pid=$!
}

cancel_timer() {
    close_notification
    [[ -n "$timer_pid" ]] || return 0
    if kill "$timer_pid" 2> /dev/null; then
        log "Screen dim ended, pending warning canceled"
    fi
    timer_pid=""
}

trap 'cancel_timer; [[ -n "$tmp_id_file" ]] && rm -f "$tmp_id_file"; log "Notify service stopped"; exit 0' SIGTERM SIGINT

# Plasma's idle dim lowers each output's "dimming" value (normally 100%,
# 30% while dimmed). This is KWin-internal state with no D-Bus signal, so
# it is polled via kscreen-doctor, which ships with Plasma. Note: the value
# only appears in the text output (-o), not in the JSON (-j).
screen_dimmed() {
    kscreen-doctor -o 2> /dev/null | grep -oE "dimming to [0-9]+%" | grep -qv "100%"
}

compute_timings

# With screen-off disabled the TV never turns off on idle; a warning would lie.
if [[ $(read_powerdevil "$profile" TurnOffDisplayWhenIdle true) != "true" ]]; then
    log "'Turn off screen' is disabled in System Settings -> Power Management; no TV-off warning needed"
    exit 0
fi

log "Notify service started (dim=${dim_timeout}s, off=${off_timeout}s, warning=${remaining}s before off, profile=${profile})"

# The dim is our idle anchor, so warn if it is disabled. Keep running: if the
# user enables it later, warnings start working without a service restart.
if [[ $(read_powerdevil "$profile" DimDisplayWhenIdle true) != "true" ]]; then
    log "Warning: 'Dim automatically' is disabled in System Settings -> Power Management; no TV-off warning can be shown until it is enabled"
fi

state=inactive

while true; do
    new=inactive
    screen_dimmed && new=active

    if [[ "$new" != "$state" ]]; then
        state=$new
        if [[ "$state" == active ]]; then
            arm_timer
        else
            cancel_timer
        fi
    fi

    sleep "$poll_interval"
done
