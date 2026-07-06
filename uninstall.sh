#!/bin/bash
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }

# Removes artefacts from older LGPowerControl versions.
cleanup_legacy() {
    local user_home f
    user_home=$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)

    for f in lgtv-power-on-at-boot.service lgtv-power-off-at-shutdown.service \
             lgtv-btw-boot.service lgtv-btw-shutdown.service \
             lgpowercontrol-sleep.service lgpowercontrol-resume.service; do
        [[ -f "/etc/systemd/system/$f" ]] || continue
        echo "Removing legacy service: $f"
        systemctl stop "$f" 2> /dev/null
        systemctl disable "$f" 2> /dev/null
        rm -f "/etc/systemd/system/$f"
    done

    for f in "$user_home/.config/autostart/lgpowercontrol-dbus-events.desktop" \
             "$user_home/.config/autostart/lgpowercontrol-monitor.desktop" \
             /etc/sudoers.d/lgpowercontrol-etherwake \
             /usr/local/bin/bscpylgtvcommand \
             /opt/lgpowercontrol/lgpowercontrol-dbus-events.sh; do
        [[ -f "$f" ]] || continue
        echo "Removing legacy file: $f"
        rm -f "$f"
    done

    for f in "$user_home/.local/lgtv-btw" "$user_home/.local/lgpowercontrol"; do
        [[ -d "$f" ]] || continue
        echo "Removing legacy directory: $f"
        rm -rf "$f"
    done
}

systemctl disable --now \
    lgpowercontrol-boot.service \
    lgpowercontrol-shutdown.service \
    lgpowercontrol-monitor.service 2> /dev/null

systemctl --global disable lgpowercontrol-notify.service 2> /dev/null
if [[ -n "${SUDO_USER:-}" ]]; then
    systemctl --machine="${SUDO_USER}@" --user stop lgpowercontrol-notify.service 2> /dev/null
fi

cleanup_legacy

rm -rf /opt/lgpowercontrol
rm -f /etc/systemd/system/lgpowercontrol*
rm -f /etc/systemd/user/lgpowercontrol*
systemctl daemon-reload

# --quiet suppresses the summary line when run from install.sh.
[[ "${1:-}" == "--quiet" ]] || echo "LGPowerControl uninstalled."
