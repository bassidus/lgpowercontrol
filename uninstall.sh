#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
YEL='\033[0;33m' BLU='\033[0;94m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

info() { echo -e "${BLU}$1${RST}"; }
sep()  { echo -e "${BLU}$SEP${RST}"; }

[[ $EUID -eq 0 ]] || { echo -e "${RED}Error: Run as root: sudo $0${RST}" >&2; exit 1; }

remove_service() {
    local svc="$1"
    test -f "/etc/systemd/system/$svc" || return 0
    info "Disabling and removing $svc"
    systemctl stop    "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$svc"
}

sep
echo -e "${RED}LGPowerControl Uninstallation${RST}"
sep
echo

read -r -p "Remove LGPowerControl and all its files? [Y/n] " answer
[[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo -e "${YEL}Cancelled.${RST}"; exit 0; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/scripts/legacy_cleanup.sh" ]]; then
    echo
    bash "$SCRIPT_DIR/scripts/legacy_cleanup.sh"
fi

echo
info "Systemd service cleanup"
remove_service lgpowercontrol-boot.service
remove_service lgpowercontrol-shutdown.service
remove_service lgpowercontrol-monitor.service
systemctl daemon-reload 2>/dev/null || true

info "Removing installation files"
rm -rf /opt/lgpowercontrol

echo
sep
echo -e "${GRN}LGPowerControl uninstalled.${RST}"
sep
