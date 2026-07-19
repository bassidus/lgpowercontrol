#!/bin/bash
set -euo pipefail

source /opt/lgpowercontrol/lgpowercontrol.conf

log() {
    [[ "${LOGGING:-yes}" == "no" ]] && return 0
    logger -t lgpowercontrol -p user.info -- "update-check: $1"
}

# At most once per UPDATE_CHECK_DAYS, compare the installed version with the
# latest GitHub release (or dev commit, see UPDATE_CHANNEL) and show a desktop
# notification when an update is available. Nothing is installed automatically.
# Triggered daily by lgpowercontrol-update-check.timer, independent of the
# notify service, so long-running sessions (suspend/resume, no reboot) still
# get checked on schedule.

repo="bassidus/lgpowercontrol"
# mtime = time of the last successful check. The notification repeats every
# UPDATE_CHECK_DAYS until the update is installed, as a reminder. Content is
# only used on the dev channel as a baseline sha when COMMIT is missing.
update_stamp="${XDG_CACHE_HOME:-$HOME/.cache}/lgpowercontrol-update-check"

fetch() {
    if command -v curl &> /dev/null; then
        curl -fsSL -m 10 "$1"
    else
        wget -qO- -T 10 "$1"
    fi
}

update_check_due() {
    local days="${UPDATE_CHECK_DAYS:-7}"
    [[ "$days" =~ ^[0-9]+$ && "$days" -gt 0 ]] || return 1
    command -v curl &> /dev/null || command -v wget &> /dev/null || return 1
    [[ -e "$update_stamp" ]] || return 0
    (( $(date +%s) - $(stat -c %Y "$update_stamp") >= days * 86400 ))
}

notify_update() { # args: body
    busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
        org.freedesktop.Notifications Notify "susssasa{sv}i" \
        "LGPowerControl" 0 "video-television" "Update available" "$1" \
        0 0 0 > /dev/null 2>&1 || true
}

check_for_update() {
    local latest installed=""

    if [[ "${UPDATE_CHANNEL:-main}" == "dev" ]]; then
        latest=$(fetch "https://api.github.com/repos/${repo}/commits/dev" 2> /dev/null |
            grep -m1 '"sha"' | cut -d'"' -f4) || true
        # Offline or API hiccup: skip the stamp touch so the next tick retries.
        [[ -n "$latest" ]] || return 0
        # COMMIT is written by update.sh --dev; absent on git-clone installs,
        # where the stamp content serves as a stand-in baseline instead.
        if [[ -r /opt/lgpowercontrol/COMMIT ]]; then
            installed=$(< /opt/lgpowercontrol/COMMIT)
        elif [[ -s "$update_stamp" ]]; then
            installed=$(< "$update_stamp")
        else
            # First check with nothing to compare against: record the current
            # dev commit silently and notify from the next new commit on.
            echo "$latest" > "$update_stamp"
            return 0
        fi
        touch "$update_stamp"
        [[ "$latest" == "$installed" ]] && return 0
        log "Update available: dev @ ${latest:0:7}"
        notify_update "A new dev commit (${latest:0:7}) is available. Install it with: sudo /opt/lgpowercontrol/update.sh --dev"
    else
        latest=$(fetch "https://api.github.com/repos/${repo}/releases/latest" 2> /dev/null |
            grep -m1 '"tag_name"' | cut -d'"' -f4) || true
        latest="${latest#v}"
        [[ -n "$latest" ]] || return 0
        [[ -r /opt/lgpowercontrol/VERSION ]] && installed=$(< /opt/lgpowercontrol/VERSION)
        touch "$update_stamp"
        [[ "$latest" == "$installed" ]] && return 0
        log "Update available: ${latest} (installed: ${installed:-unknown})"
        notify_update "LGPowerControl ${latest} is available (installed: ${installed:-unknown}). Update with: sudo /opt/lgpowercontrol/update.sh"
    fi
}

if update_check_due; then
    check_for_update || true
fi
