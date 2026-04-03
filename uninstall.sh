#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

readonly RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
readonly YEL='\033[0;33m' BLU='\033[0;34m'

info() { echo -e "${BLU}$1${RST}"; }

remove_service() {
    local svc="$1" path="/etc/systemd/system/$1"
    if sudo systemctl status "$svc" >/dev/null 2>&1; then
        info "Stopping $svc"
        sudo systemctl stop "$svc" 2>/dev/null || true
        sudo systemctl is-enabled --quiet "$svc" 2>/dev/null && sudo systemctl disable "$svc"
    fi
    if sudo test -f "$path"; then
        info "Removing $path"
        sudo rm -f "$path"
    fi
}

[[ $EUID -eq 0 ]] && { echo -e "${YEL}Do not run as root/sudo.${RST}" >&2; exit 1; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${RED}LGPowerControl Uninstallation${RST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
rm -f "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop"

info "Removing installation files"
rm -rf "$HOME/.local/lgpowercontrol"

info "Killing running monitor processes"
pkill -f lgpowercontrol-dbus-events.sh 2>/dev/null || true

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GRN}LGPowerControl uninstalled.${RST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"