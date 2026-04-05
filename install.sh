#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh <TV_IP_ADDRESS>

set -euo pipefail

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
YEL='\033[0;33m' BLU='\033[0;34m' CYN='\033[0;36m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

LGTV_IP="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PATH="/opt/lgpowercontrol"

die()        { echo -e "${RED}Error: $1${RST}" >&2; exit 1; }
info()       { echo -e "${BLU}$1${RST}"; }
ok()         { echo -e " ${GRN}[OK]${RST}"; }
sep()        { echo "$SEP"; }
has()        { command -v "$1" >/dev/null 2>&1; }

confirm() {
    local answer
    read -r -p "$1 [Y/n] " answer
    echo
    [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

sep; info "LGPowerControl Installation"; sep
echo

# --- Package manager detection -------------------------------------------------
if   has pacman; then INSTALL_HINT="using: sudo pacman -S"
elif has apt;    then INSTALL_HINT="using: sudo apt install"
elif has dnf;    then INSTALL_HINT="using: sudo dnf install"
else                  INSTALL_HINT="with your package manager"
fi

# --- TV IP validation ----------------------------------------------------------
if [[ -z "$LGTV_IP" ]]; then
    read -r -p "Enter TV IP address: " LGTV_IP
fi
octet='(25[0-5]|2[0-4][0-9]|[01]?[0-9]{1,2})'
[[ "$LGTV_IP" =~ ^${octet}\.${octet}\.${octet}\.${octet}$ ]] \
    || die "'$LGTV_IP' is not a valid IPv4 address"
echo -ne "${CYN}Verifying $LGTV_IP is reachable ...${RST}"
ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1 || die "$LGTV_IP is unreachable"
ok

# --- Dependency checks ---------------------------------------------------------
echo -ne "${CYN}Checking for ip ...${RST}"
has ip || die "'iproute2' is not installed. Install it $INSTALL_HINT iproute2"
ok

echo -ne "${CYN}Checking for python3 ...${RST}"
has python3 || die "'python3' is not installed. Install it $INSTALL_HINT python3"
ok

if has apt; then
    echo -ne "${CYN}Checking for python3-venv ...${RST}"
    _venv_tmp=$(mktemp -d)
    python3 -m venv "$_venv_tmp" >/dev/null 2>&1 || {
        _pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        rm -rf "$_venv_tmp"
        die "python3-venv not functional. Try: sudo apt install python${_pyver}-venv"
    }
    rm -rf "$_venv_tmp"
    ok
fi

if has dnf; then
    echo -ne "${CYN}Checking for ether-wake ...${RST}"
    has ether-wake || die "'net-tools' is not installed. Install it $INSTALL_HINT net-tools"
    ok
else
    echo -ne "${CYN}Checking for wakeonlan ...${RST}"
    has wakeonlan || die "'wakeonlan' is not installed. Install it $INSTALL_HINT wakeonlan"
    ok
fi

# --- MAC address retrieval -----------------------------------------------------
echo -ne "${CYN}Retrieving MAC address ...${RST}"
LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)
[[ "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]] \
    || die "Could not detect MAC for $LGTV_IP. Ensure the TV is ON."
echo -e " $LGTV_MAC${GRN} [OK]${RST}"

# --- HDMI input selection ------------------------------------------------------
echo
sep; info "HDMI Input Selection (Optional)"; sep
echo "Select which HDMI port the computer is connected to."
echo "The TV will switch to this input on power-on. Leave empty to skip."
echo
read -r -p "Enter number (1-5): " _hdmi_choice
if [[ "$_hdmi_choice" =~ ^[1-5]$ ]]; then
    HDMI_INPUT="HDMI_$_hdmi_choice"
    echo -e "${GRN}Will switch to $HDMI_INPUT on power-on.${RST}"
else
    HDMI_INPUT=""
    [[ -n "$_hdmi_choice" ]] && echo -e "${YEL}Invalid input. Skipping HDMI configuration.${RST}"
fi

# --- Wake-on-LAN command -------------------------------------------------------
# Services run as root, so no sudo prefix needed regardless of WoL tool.
if has wakeonlan; then
    WOL_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"
elif has ether-wake; then
    WOL_CMD="$(command -v ether-wake) $LGTV_MAC"
else
    die "Neither 'wakeonlan' nor 'ether-wake' found"
fi

info "Installation path: $INSTALL_PATH"

# --- Legacy cleanup ------------------------------------------------------------
bash "$SCRIPT_DIR/legacy_cleanup.sh"

confirm "All dependencies met. Confirm installation?" || { echo -e "${YEL}Aborted.${RST}"; exit 0; }

# --- Install bscpylgtv ---------------------------------------------------------
if [[ -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]]; then
    echo -e "${GRN}bscpylgtv already installed. Skipping.${RST}"
else
    info "Installing bscpylgtv into $INSTALL_PATH..."
    sudo mkdir -p "$INSTALL_PATH"
    sudo python3 -m venv "$INSTALL_PATH/bscpylgtv"
    sudo "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip
    sudo "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv || die "Failed to install bscpylgtv"
    echo -e "${GRN}bscpylgtv installed.${RST}"
fi

# --- Install control script ----------------------------------------------------
sudo cp "$SCRIPT_DIR/lgpowercontrol" "$INSTALL_PATH/lgpowercontrol"
sudo chmod +x "$INSTALL_PATH/lgpowercontrol"

# --- Boot/shutdown systemd services --------------------------------------------
info "Setting up systemd services..."
sudo cp "$SCRIPT_DIR/lgpowercontrol-shutdown.service" /etc/systemd/system/lgpowercontrol-shutdown.service
sudo cp "$SCRIPT_DIR/lgpowercontrol-boot.service"     /etc/systemd/system/lgpowercontrol-boot.service
sudo systemctl daemon-reload
sudo systemctl enable lgpowercontrol-boot.service
sudo systemctl enable lgpowercontrol-shutdown.service

echo
echo -e "${GRN}Systemd services enabled:${RST}"
echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
echo

# --- Config file ---------------------------------------------------------------
# Hardware values (IP, MAC, WOL, HDMI) are always refreshed.
# Behavior settings are written only on first install to preserve user edits.
if [[ ! -f "$INSTALL_PATH/lgpowercontrol.conf" ]]; then

    sep; info "Power Mode Configuration"; sep
    echo "How should the TV be controlled at boot and shutdown?"
    echo "  1) power  — WoL on at boot, fully power off at shutdown  [default]"
    echo "  2) screen — turn screen on/off (TV stays in standby, faster)"
    echo
    read -r -p "Choice [1/2]: " _boot_choice
    echo
    case "${_boot_choice:-1}" in
        2) BOOT_SHUTDOWN_MODE=screen ;;
        *) BOOT_SHUTDOWN_MODE=power  ;;
    esac
    echo -e "${GRN}Boot/shutdown mode: $BOOT_SHUTDOWN_MODE${RST}"

    echo
    echo "How should the TV react when the computer screen sleeps/wakes?"
    echo "  1) screen — turn TV screen off/on (TV stays in standby, faster)  [default]"
    echo "  2) power  — fully power off / WoL on"
    echo
    read -r -p "Choice [1/2]: " _monitor_choice
    echo
    case "${_monitor_choice:-1}" in
        2) MONITOR_MODE=power  ;;
        *) MONITOR_MODE=screen ;;
    esac
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
#   screen : turn TV screen off/on (TV stays in standby)    [default]
#   power  : power off (full) / power on (WoL) the TV
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

# --- Screen state monitor ------------------------------------------------------
info "Installing screen state monitor..."
sudo cp "$SCRIPT_DIR/lgpowercontrol-monitor.sh" "$INSTALL_PATH/lgpowercontrol-monitor.sh"
sudo chmod +x "$INSTALL_PATH/lgpowercontrol-monitor.sh"
sudo cp "$SCRIPT_DIR/lgpowercontrol-monitor.service" /etc/systemd/system/lgpowercontrol-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now lgpowercontrol-monitor.service
echo -e "${GRN}Screen state monitor installed and started.${RST}"

# --- TV authorization handshake ------------------------------------------------
if [[ ! -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]]; then
    sep; info "TV Authorization Required"; sep
    echo "Accept the prompt on your TV with the remote."
    sep
    read -r -p "Press ENTER to send the test command"
    sudo "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" -p "$INSTALL_PATH/.aiopylgtv.sqlite" "$LGTV_IP" get_power_state >/dev/null 2>&1
    sleep 1
    [[ -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]] || die "Authorization failed. Re-run install."
    echo -e "${GRN}Authorization complete!${RST}"
fi

echo
sep
echo -e "${GRN}Installation complete!${RST}"
sep
echo "TV will power on at boot and off at shutdown."
info "Logs: journalctl -t lgpowercontrol"
sep
