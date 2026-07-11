#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    logger -t lgpowercontrol -p user.info -- "$1"
}

# Returns "on" if any connected DRM output is active, "off" if all connected
# outputs are inactive, or "" if no output is connected (e.g. mid-hotplug).
# Checks every connector by status instead of matching names, so it also
# works with eDP, DVI, VGA and virtual (VM) outputs.
get_drm_state() {
    local dir connected=0
    for dir in /sys/class/drm/card*-*/; do
        [[ -r "$dir/status" && -r "$dir/dpms" ]] || continue
        [[ $(< "$dir/status") == "connected" ]] || continue
        connected=1
        if [[ $(< "$dir/dpms") == "On" ]]; then
            echo "on"
            return
        fi
    done
    ((connected)) && echo "off"
    return 0 # empty output = indeterminate, must not trip set -e
}

# NetworkManager takes the network down as soon as logind announces
# PrepareForSleep, before sleep.target units get to run. Holding a delay
# inhibitor makes logind (and NM) wait until we have sent the TV command
# or released it, so the TV can be turned off while the network is up.
new_inhibitor() {
    systemd-inhibit --what=sleep --mode=delay --who=LGPowerControl \
        --why="Turn TV off before sleep" sleep infinity &
    inhibitor_pid=$!
}

watch_sleep() {
    new_inhibitor
    dbus-monitor --system \
        "type='signal',sender='org.freedesktop.login1',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'" |
    while read -r line; do
        case "$line" in
        *"boolean true"*)
            log "System going to sleep"
            /opt/lgpowercontrol/lgpowercontrol OFF "$MONITOR_MODE" \
                || log "lgpowercontrol OFF failed"
            kill "$inhibitor_pid" 2> /dev/null || true # let the sleep proceed
            ;;
        *"boolean false"*)
            log "System woke up"
            new_inhibitor
            # Retry while the network reconnects after resume.
            for _ in 1 2 3 4 5; do
                /opt/lgpowercontrol/lgpowercontrol ON "$MONITOR_MODE" && break
                sleep 2
            done
            ;;
        esac
    done
    log "Sleep watcher exited unexpectedly"
}

trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT

watch_sleep &

log "DRM monitor started (MONITOR_MODE=${MONITOR_MODE})"

previous_state=$(get_drm_state)
log "Initial DRM state: ${previous_state:-unknown}"

while true; do
    current_state=$(get_drm_state)

    if [[ -n "$current_state" && "$current_state" != "$previous_state" ]]; then
        log "DRM state: ${previous_state:-unknown} -> ${current_state}"
        # A failed TV command must not kill the monitor (set -e), so log it instead.
        /opt/lgpowercontrol/lgpowercontrol "${current_state^^}" "$MONITOR_MODE" \
            || log "lgpowercontrol ${current_state^^} failed"
        previous_state=$current_state
    fi

    sleep 1
done