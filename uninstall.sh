#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo." >&2; exit 1; }

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

remove_service() {
    local svc="$1"
    [[ -f "/etc/systemd/system/$svc" ]] || return 0
    info "Disabling and removing $svc"
    systemctl stop    "$svc" 2>/dev/null || true
    systemctl disable "$svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$svc"
}

sep; info "LGPowerControl Uninstallation"; sep
echo

confirm "Remove LGPowerControl and all its files?" || { echo "${YEL}Cancelled.${RST}"; exit 0; }

# ---- legacy cleanup ---------------------------------------------------------

cleanup_legacy

# ---- remove services --------------------------------------------------------

echo
info "Systemd service cleanup"
remove_service lgpowercontrol-boot.service
remove_service lgpowercontrol-shutdown.service
remove_service lgpowercontrol-monitor.service
systemctl daemon-reload 2>/dev/null || true

info "Removing installation files"

# ---- auto-installed dependencies --------------------------------------------

if [[ -f /opt/lgpowercontrol/installed_deps ]]; then
    mapfile -t _auto_deps < /opt/lgpowercontrol/installed_deps
    if [[ ${#_auto_deps[@]} -gt 0 ]]; then
        echo
        info "The following packages were installed automatically during setup:"
        for _dep in "${_auto_deps[@]}"; do echo "   • $_dep"; done
        echo
        if confirm "Uninstall these packages now?"; then
            if   has pacman; then pacman -Rs --noconfirm "${_auto_deps[@]}"
            elif has apt;    then apt remove -y "${_auto_deps[@]}"
            elif has dnf;    then dnf remove -y "${_auto_deps[@]}"
            fi || echo "${YEL}Some packages may not have been removed.${RST}"
        fi
    fi
fi

rm -rf /opt/lgpowercontrol

echo
sep
echo "${GRN}LGPowerControl uninstalled.${RST}"
sep
