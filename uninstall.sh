#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
YEL='\033[0;33m' BLU='\033[0;34m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

info() { echo -e "${BLU}$1${RST}"; }

remove_service() {
    local svc="$1" path="/etc/systemd/system/$1"
    if sudo test -f "$path"; then
        info "Disabling and removing $svc"
        sudo systemctl stop "$svc" 2>/dev/null || true
        sudo systemctl disable "$svc" 2>/dev/null || true
        sudo rm -f "$path"
    fi
}

[[ $EUID -eq 0 ]] && { echo -e "${YEL}Do not run as root/sudo.${RST}" >&2; exit 1; }

echo "$SEP"
echo -e "${RED}LGPowerControl Uninstallation${RST}"
echo "$SEP"
echo

read -r -p "Remove LGPowerControl and all its files? [y/N] " answer
[[ "${answer:-N}" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo -e "${YEL}Cancelled.${RST}"; exit 0; }

echo
info "Systemd service cleanup"
remove_service lgpowercontrol-boot.service
remove_service lgpowercontrol-shutdown.service
sudo systemctl daemon-reload 2>/dev/null || true

sudo test -f /etc/sudoers.d/lgpowercontrol-etherwake \
    && { info "Removing sudoers rule"; sudo rm -f /etc/sudoers.d/lgpowercontrol-etherwake; }

info "Removing autostart entry"
rm -f "$HOME/.config/autostart/lgpowercontrol-monitor.desktop"

info "Removing installation files"
rm -rf "$HOME/.local/lgpowercontrol"

info "Killing running monitor processes"
pkill -f lgpowercontrol-monitor.sh 2>/dev/null || true

echo
echo "$SEP"
echo -e "${GRN}LGPowerControl uninstalled.${RST}"
echo "$SEP"