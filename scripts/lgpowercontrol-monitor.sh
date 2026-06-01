#!/bin/bash
source /opt/lgpowercontrol/lgpowercontrol.conf

log() { logger -t lgpowercontrol -p "user.info" -- "$1"; }

get_drm_state() {
    for d in /sys/class/drm/card*/card*-*/; do
        [[ $(< "${d}status") == "connected" ]] || continue
        [[ ! -f "${d}dpms" ]] || [[ $(< "${d}dpms") == "On" ]] && { echo on; return; }
    done
    echo off
}

trap 'log "Monitor stopped"; exit 0' SIGTERM SIGINT
log "DRM monitor started (POWER_MODE=$POWER_MODE)"
prev=$(get_drm_state)
log "Initial DRM state: $prev"

while true; do
    state=$(get_drm_state)
    if [[ "$state" != "$prev" ]]; then
        log "DRM state: $prev -> $state"
        /opt/lgpowercontrol/lgpowercontrol "${state^^}" "$POWER_MODE"
        prev=$state
    fi
    sleep 2
done
