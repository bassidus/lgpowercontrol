#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    logger -t lgpowercontrol -p user.info -- "$1"
}

[[ "${OFF_WARNING_SECONDS:-0}" -gt 0 ]] || exit 0

# KDE Plasma only — exit quietly on other desktop environments.
command -v kscreen-doctor &> /dev/null && command -v kreadconfig6 &> /dev/null || exit 0

# Plasma's idle timeouts (seconds). Read once at startup; restart this service
# after changing them in System Settings -> Energy Saving:
# systemctl --user restart lgpowercontrol-notify.service
read_powerdevil() {
    kreadconfig6 --file powerdevilrc --group AC --group Display --key "$1" --default "$2"
}

dim_enabled=$(read_powerdevil DimDisplayWhenIdle true)
dim_timeout=$(read_powerdevil DimDisplayIdleTimeoutSec 300)
off_timeout=$(read_powerdevil TurnOffDisplayIdleTimeoutSec 600)

poll_interval="${NOTIFY_POLL_SECONDS:-2}"
[[ "$poll_interval" =~ ^[0-9]+$ && "$poll_interval" -gt 0 ]] || poll_interval=2

if [[ "$dim_enabled" != "true" ]]; then
    log "Plasma's 'Dim automatically' is disabled, but it is the idle signal this service relies on. Exiting."
    exit 0
fi

# The dim event is our only idle anchor, so the warning fires notify_delay
# seconds after Plasma dims the screen.
notify_delay=$((off_timeout - dim_timeout - OFF_WARNING_SECONDS))
((notify_delay > 0)) || notify_delay=0
remaining=$((off_timeout - dim_timeout - notify_delay))

# The notification id is passed via a file since send_notification runs in
# the timer subshell, which cannot set variables in the main process.
id_file="${XDG_RUNTIME_DIR:-/tmp}/lgpowercontrol-notify.id"

send_notification() {
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify "susssasa{sv}i" \
        "LGPowerControl" 0 "video-television" "TV turning off" \
        "The TV turns off in ${remaining} seconds. Move the mouse or press a key to keep it on." \
        0 0 $((remaining * 1000)) | awk '{print $2}' > "$id_file"
    log "Notification sent!"
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
    log "Screen dimmed; warning notification in ${notify_delay}s"
    (
        sleep "$notify_delay"
        send_notification
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

trap 'cancel_timer; log "Notify service stopped"; exit 0' SIGTERM SIGINT

# Plasma's idle dim lowers each output's "dimming" value (normally 100%,
# 30% while dimmed). This is KWin-internal state with no D-Bus signal, so
# it is polled via kscreen-doctor, which ships with Plasma.
screen_dimmed() {
    kscreen-doctor -o 2> /dev/null | grep -oE "dimming to [0-9]+%" | grep -qv "100%"
}

log "Notify service started (dim=${dim_timeout}s, off=${off_timeout}s, warning=${remaining}s before off)"

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
