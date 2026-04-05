#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
YEL='\033[0;33m' BLU='\033[0;34m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

info() { echo -e "${BLU}$1${RST}"; }

remove_service() {
    local svc="$1"
    sudo test -f "/etc/systemd/system/$svc" || return 0
    info "Disabling and removing $svc"
    sudo systemctl stop    "$svc" 2>/dev/null || true
    sudo systemctl disable "$svc" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/$svc"
}

echo "$SEP"
echo -e "${RED}LGPowerControl Uninstallation${RST}"
echo "$SEP"
echo

read -r -p "Remove LGPowerControl and all its files? [Y/n] " answer
[[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo -e "${YEL}Cancelled.${RST}"; exit 0; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/legacy_cleanup.sh" ]]; then
    echo
    bash "$SCRIPT_DIR/legacy_cleanup.sh"
fi

echo
info "Systemd service cleanup"
remove_service lgpowercontrol-boot.service
remove_service lgpowercontrol-shutdown.service
remove_service lgpowercontrol-monitor.service
sudo systemctl daemon-reload 2>/dev/null || true

info "Removing installation files"
sudo rm -rf /opt/lgpowercontrol

echo
echo "$SEP"
echo -e "${GRN}LGPowerControl uninstalled.${RST}"
echo "$SEP"
