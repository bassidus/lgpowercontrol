#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/scripts/common.sh"

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

remove_service() {
    local svc="$1"
    test -f "/etc/systemd/system/$svc" || return 0
    info "Disabling and removing $svc"
    systemctl stop    "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$svc"
}

sep; info "LGPowerControl Uninstallation"; sep
echo

confirm "Remove LGPowerControl and all its files?" || { echo -e "${YEL}Cancelled.${RST}"; exit 0; }

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
