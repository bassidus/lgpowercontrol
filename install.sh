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
cmd_exists() { command -v "$1" >/dev/null 2>&1; }

confirm() {
    local answer
    read -r -p "$1 [Y/n] " answer
    echo
    [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]
}

sep; info "LGPowerControl Installation"; sep
echo

# --- Package manager detection -------------------------------------------------
if   cmd_exists pacman; then INSTALL_HINT="using: sudo pacman -S"
elif cmd_exists apt;    then INSTALL_HINT="using: sudo apt install"
elif cmd_exists dnf;    then INSTALL_HINT="using: sudo dnf install"
else                         INSTALL_HINT="with your package manager"
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

LGCOMMAND="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand -p $INSTALL_PATH/.aiopylgtv.sqlite $LGTV_IP"

# --- Dependency checks ---------------------------------------------------------
echo -ne "${CYN}Checking for ip ...${RST}"
cmd_exists ip || die "'iproute2' is not installed. Install it $INSTALL_HINT iproute2"
ok

echo -ne "${CYN}Checking for python3 ...${RST}"
cmd_exists python3 || die "'python3' is not installed. Install it $INSTALL_HINT python3"
ok

if cmd_exists apt; then
    echo -ne "${CYN}Checking for python3-venv ...${RST}"
    _venv_tmp=$(mktemp -d)
    _venv_ok=true
    python3 -m venv "$_venv_tmp" >/dev/null 2>&1 || _venv_ok=false
    rm -rf "$_venv_tmp"
    if ! "$_venv_ok"; then
        _pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        die "python3-venv not functional. Try: sudo apt install python${_pyver}-venv"
    fi
    ok
fi

if cmd_exists dnf; then
    echo -ne "${CYN}Checking for ether-wake ...${RST}"
    cmd_exists ether-wake || die "'net-tools' is not installed. Install it $INSTALL_HINT net-tools"
    ok
else
    echo -ne "${CYN}Checking for wakeonlan ...${RST}"
    cmd_exists wakeonlan || die "'wakeonlan' is not installed. Install it $INSTALL_HINT wakeonlan"
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
if cmd_exists wakeonlan; then
    WOL_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"
elif cmd_exists ether-wake; then
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
sed -e "s|LGCOMMAND|$LGCOMMAND|g" \
    -e "s|INPUT|$HDMI_INPUT|g" \
    -e "s|WOL_CMD|$WOL_CMD|g" \
    "$SCRIPT_DIR/lgpowercontrol" | sudo tee "$INSTALL_PATH/lgpowercontrol" >/dev/null
sudo chmod +x "$INSTALL_PATH/lgpowercontrol"

# --- Boot/shutdown systemd services --------------------------------------------
info "Setting up systemd services..."

sed "s|PWR_OFF_CMD|$INSTALL_PATH/lgpowercontrol OFF|g" \
    "$SCRIPT_DIR/lgpowercontrol-shutdown.service" \
    | sudo tee /etc/systemd/system/lgpowercontrol-shutdown.service >/dev/null
sed "s|PWR_ON_CMD|$INSTALL_PATH/lgpowercontrol ON|g" \
    "$SCRIPT_DIR/lgpowercontrol-boot.service" \
    | sudo tee /etc/systemd/system/lgpowercontrol-boot.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable lgpowercontrol-boot.service
sudo systemctl enable lgpowercontrol-shutdown.service

echo
echo -e "${GRN}Systemd services enabled:${RST}"
echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
echo

# Optional: screen state monitor (system service)
sep; info "Optional: Screen State Monitor"; sep
echo "Runs as a system service. Blanks/unblanks the TV when screens sleep/wake."
echo "Works system-wide across all logged-in users (X11 and Wayland)."
sep

if confirm "Install the screen state monitor?"; then
    info "Installing screen state monitor..."

    sed -e "s|SCREEN_OFF_CMD|$LGCOMMAND turn_screen_off|g" \
        -e "s|SCREEN_ON_CMD|$LGCOMMAND turn_screen_on|g" \
        "$SCRIPT_DIR/lgpowercontrol-monitor.sh" \
        | sudo tee "$INSTALL_PATH/lgpowercontrol-monitor.sh" >/dev/null
    sudo chmod +x "$INSTALL_PATH/lgpowercontrol-monitor.sh"

    sed "s|MONITOR_SCRIPT|$INSTALL_PATH/lgpowercontrol-monitor.sh|g" \
        "$SCRIPT_DIR/lgpowercontrol-monitor.service" \
        | sudo tee /etc/systemd/system/lgpowercontrol-monitor.service >/dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable --now lgpowercontrol-monitor.service
    echo -e "${GRN}Screen state monitor installed and started.${RST}"
fi

# --- TV authorization handshake ------------------------------------------------
if [[ ! -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]]; then
    sep; info "TV Authorization Required"; sep
    echo "Accept the prompt on your TV with the remote."
    sep
    read -r -p "Press ENTER to send the test command"
    sudo $LGCOMMAND button INFO >/dev/null 2>&1
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
