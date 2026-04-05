#!/bin/bash
# LGPowerControl configuration writer
# Creates or updates lgpowercontrol.conf with hardware values and behavior settings.
# Called by install.sh; expects LGTV_IP, LGTV_MAC, WOL_CMD, HDMI_INPUT, INSTALL_PATH.

set -euo pipefail

RST='\033[0m' GRN='\033[0;32m' BLU='\033[0;34m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

info() { echo -e "${BLU}$1${RST}"; }
sep()  { echo "$SEP"; }

ask_mode() {
    local _choice
    echo "$1"
    echo "  1) power  — WoL on / fully power off  [default]"
    echo "  2) screen — turn screen on/off (TV stays in standby, faster)"
    echo
    read -r -p "Choice [1/2]: " _choice
    echo
    case "$_choice" in
        2) _answer=screen ;;
        *) _answer=power  ;;
    esac
}

# Hardware values (IP, MAC, WOL, HDMI) are always refreshed.
# Behavior settings are written only on first install to preserve user edits.
if [[ ! -f "$INSTALL_PATH/lgpowercontrol.conf" ]]; then

    sep; info "Power Mode Configuration"; sep

    ask_mode "How should the TV be controlled at boot and shutdown?"
    BOOT_SHUTDOWN_MODE=$_answer
    echo -e "${GRN}Boot/shutdown mode: $BOOT_SHUTDOWN_MODE${RST}"

    ask_mode "How should the TV react when the computer screen sleeps/wakes?"
    MONITOR_MODE=$_answer
    echo -e "${GRN}Monitor mode: $MONITOR_MODE${RST}"
    sep

    info "Writing config to $INSTALL_PATH/lgpowercontrol.conf ..."
    sudo tee "$INSTALL_PATH/lgpowercontrol.conf" >/dev/null <<EOF
# LGPowerControl configuration
# After editing, restart the monitor service to apply changes:
#   sudo systemctl restart lgpowercontrol-monitor.service
# Boot and shutdown services read this file each time they run — no restart needed.

# --- Hardware (updated automatically on reinstall) ----------------------------
LGTV_IP=$LGTV_IP
LGTV_MAC=$LGTV_MAC
WOL_CMD="$WOL_CMD"
HDMI_INPUT=$HDMI_INPUT

# --- Behavior -----------------------------------------------------------------

# BOOT_SHUTDOWN_MODE — controls TV behavior at boot and shutdown
#   power  : power on (WoL) at boot, power off at shutdown  [default]
#   screen : turn screen on at boot, turn screen off at shutdown
#            (TV stays in standby — faster, but requires the TV to remain powered)
BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE

# MONITOR_MODE — controls TV behavior when the computer screen sleeps/wakes
#   power  : power off (full) / power on (WoL) the TV       [default]
#   screen : turn TV screen off/on (TV stays in standby)
MONITOR_MODE=$MONITOR_MODE
EOF
    echo -e "${GRN}Config written: $INSTALL_PATH/lgpowercontrol.conf${RST}"
else
    info "Updating hardware values in existing config ..."
    sudo sed -i \
        -e "s|^LGTV_IP=.*|LGTV_IP=$LGTV_IP|" \
        -e "s|^LGTV_MAC=.*|LGTV_MAC=$LGTV_MAC|" \
        -e 's|^WOL_CMD=.*|WOL_CMD="'"$WOL_CMD"'"|' \
        -e "s|^HDMI_INPUT=.*|HDMI_INPUT=$HDMI_INPUT|" \
        "$INSTALL_PATH/lgpowercontrol.conf"
    echo -e "${GRN}Hardware values updated; behavior settings preserved.${RST}"
fi
