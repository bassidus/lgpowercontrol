#!/bin/bash
# LGPowerControl legacy cleanup
# Removes artefacts from previous installs to avoid duplicate processes/services.
# Called by install.sh; can also be run standalone.

RST='\033[0m' GRN='\033[0;32m' YEL='\033[0;33m'

_did_legacy_cleanup=false

# Old systemd service names from previous naming conventions
_legacy_services=(
    lgtv-power-on-at-boot.service
    lgtv-power-off-at-shutdown.service
    lgtv-btw-boot.service
    lgtv-btw-shutdown.service
    lgpowercontrol-sleep.service
    lgpowercontrol-resume.service
)
for _svc in "${_legacy_services[@]}"; do
    if [[ -f "/etc/systemd/system/$_svc" ]]; then
        echo -e "${YEL}Removing legacy service: $_svc${RST}"
        sudo systemctl stop    "$_svc" 2>/dev/null || true
        sudo systemctl disable "$_svc" 2>/dev/null || true
        sudo rm -f "/etc/systemd/system/$_svc"
        _did_legacy_cleanup=true
    fi
done

# Old autostart desktop entries (pre-systemd approach)
_legacy_desktop_files=(
    "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop"
    "$HOME/.config/autostart/lgpowercontrol-monitor.desktop"
)
for _df in "${_legacy_desktop_files[@]}"; do
    if [[ -f "$_df" ]]; then
        echo -e "${YEL}Removing legacy autostart entry: $(basename "$_df")${RST}"
        rm -f "$_df"
        _did_legacy_cleanup=true
    fi
done

# Old install directories (before /opt move)
_legacy_install_dirs=(
    "$HOME/.local/lgtv-btw"
    "$HOME/.local/lgpowercontrol"
)
for _old_dir in "${_legacy_install_dirs[@]}"; do
    if [[ -d "$_old_dir" ]]; then
        echo -e "${YEL}Removing legacy install directory: $_old_dir${RST}"
        rm -rf "$_old_dir"
        _did_legacy_cleanup=true
    fi
done

# Old sudoers rule (no longer needed; services now run as root)
if [[ -f "/etc/sudoers.d/lgpowercontrol-etherwake" ]]; then
    echo -e "${YEL}Removing legacy sudoers rule for ether-wake${RST}"
    sudo rm -f "/etc/sudoers.d/lgpowercontrol-etherwake"
    _did_legacy_cleanup=true
fi

# Old bscpylgtvcommand binary copied to /usr/local/bin in earliest installs
if [[ -f "/usr/local/bin/bscpylgtvcommand" ]]; then
    echo -e "${YEL}Removing legacy /usr/local/bin/bscpylgtvcommand${RST}"
    sudo rm -f "/usr/local/bin/bscpylgtvcommand"
    _did_legacy_cleanup=true
fi

# Old dbus-events script left in install dir from pre-monitor rename
if [[ -f "/opt/lgpowercontrol/lgpowercontrol-dbus-events.sh" ]]; then
    echo -e "${YEL}Removing legacy /opt/lgpowercontrol/lgpowercontrol-dbus-events.sh${RST}"
    sudo rm -f "/opt/lgpowercontrol/lgpowercontrol-dbus-events.sh"
    _did_legacy_cleanup=true
fi

if $_did_legacy_cleanup; then
    sudo systemctl daemon-reload 2>/dev/null || true
    echo -e "${GRN}Legacy files cleaned up.${RST}"
fi
