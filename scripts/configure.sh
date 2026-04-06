#!/bin/bash
# LGPowerControl configuration writer
# Creates or updates lgpowercontrol.conf with hardware values and behavior settings.
# Called by install.sh; expects LGTV_IP, LGTV_MAC, WOL_CMD, HDMI_INPUT, INSTALL_PATH.

set -euo pipefail

RST='\033[0m' GRN='\033[0;32m' BLU='\033[0;94m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

info() { echo -e "${BLU}$1${RST}"; }
sep()  { echo -e "${BLU}$SEP${RST}"; }

ask_mode() {
    local _choice
    echo -e "${BLU}$1${RST}"
    echo -e "  ${GRN}1) power  — Complete power off. Maximizes energy savings but results in slower wake times. [default]${RST}"
    echo -e "  ${GRN}2) screen — Turns off the display only. Faster wake times with slightly higher power draw.${RST}"
    echo
    read -r -p "$(echo -e "${GRN}Choice [1/2]: ${RST}")" _choice
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
    tee "$INSTALL_PATH/lgpowercontrol.conf" >/dev/null <<EOF
# LGPowerControl configuration

# After editing, restart the monitor service to apply changes:
#   sudo systemctl restart lgpowercontrol-monitor.service

# --- Remote Interface Settings ------------------------------------------------

LGTV_IP=$LGTV_IP
LGTV_MAC=$LGTV_MAC
WOL_CMD="$WOL_CMD"
HDMI_INPUT=$HDMI_INPUT

# --- Behavior -----------------------------------------------------------------

# 'power'  - Complete power off. Maximizes energy savings but results in slower wake times. [Default]
# 'screen' - Turns off the display only. Faster wake times with slightly higher power draw.

BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE
MONITOR_MODE=$MONITOR_MODE
EOF
    echo -e "${GRN}Config written: $INSTALL_PATH/lgpowercontrol.conf${RST}"
else
    info "Updating hardware values in existing config ..."
    sed -i \
        -e "s|^LGTV_IP=.*|LGTV_IP=$LGTV_IP|" \
        -e "s|^LGTV_MAC=.*|LGTV_MAC=$LGTV_MAC|" \
        -e 's|^WOL_CMD=.*|WOL_CMD="'"$WOL_CMD"'"|' \
        -e "s|^HDMI_INPUT=.*|HDMI_INPUT=$HDMI_INPUT|" \
        "$INSTALL_PATH/lgpowercontrol.conf"
    echo -e "${GRN}Hardware values updated; behavior settings preserved.${RST}"
fi
