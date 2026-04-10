#!/bin/bash
# LGPowerControl uninstaller

set -euo pipefail

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

# ---- helpers ----------------------------------------------------------------

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m' YEL='\033[0;33m' BLU='\033[0;94m'
SEP='----------------------------------------------------------------'

die()     { echo -e "${RED}Error: $1${RST}" >&2; exit 1; }
info()    { echo -e "${BLU}$1${RST}"; }
sep()     { echo -e "${BLU}$SEP${RST}"; }
confirm() { local a; read -r -p "$1 [Y/n] " a; echo; [[ "${a:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]; }

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

confirm "Remove LGPowerControl and all its files?" || { echo -e "${YEL}Cancelled.${RST}"; exit 0; }

# ---- legacy cleanup ---------------------------------------------------------

_legacy_cleaned=false

for _svc in lgtv-power-on-at-boot.service lgtv-power-off-at-shutdown.service \
            lgtv-btw-boot.service lgtv-btw-shutdown.service \
            lgpowercontrol-sleep.service lgpowercontrol-resume.service; do
    [[ -f "/etc/systemd/system/$_svc" ]] || continue
    echo -e "${YEL}Removing legacy service: $_svc${RST}"
    systemctl stop    "$_svc" 2>/dev/null || true
    systemctl disable "$_svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$_svc"
    _legacy_cleaned=true
done

for _df in "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop" \
           "$HOME/.config/autostart/lgpowercontrol-monitor.desktop"; do
    [[ -f "$_df" ]] || continue
    echo -e "${YEL}Removing legacy autostart entry: $(basename "$_df")${RST}"
    rm -f "$_df"; _legacy_cleaned=true
done

for _old_dir in "$HOME/.local/lgtv-btw" "$HOME/.local/lgpowercontrol"; do
    [[ -d "$_old_dir" ]] || continue
    echo -e "${YEL}Removing legacy install directory: $_old_dir${RST}"
    rm -rf "$_old_dir"; _legacy_cleaned=true
done

for _f in "/etc/sudoers.d/lgpowercontrol-etherwake" \
          "/usr/local/bin/bscpylgtvcommand" \
          "/opt/lgpowercontrol/lgpowercontrol-dbus-events.sh"; do
    [[ -f "$_f" ]] || continue
    echo -e "${YEL}Removing legacy: $_f${RST}"
    rm -f "$_f"; _legacy_cleaned=true
done

$_legacy_cleaned && { systemctl daemon-reload 2>/dev/null || true; echo -e "${GRN}Legacy files cleaned up.${RST}"; }

# ---- remove services --------------------------------------------------------

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
