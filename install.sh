#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh <TV_IP_ADDRESS>

set -euo pipefail

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

LGTV_IP="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PATH="/opt/lgpowercontrol"

# shellcheck source=scripts/common.sh
source "$SCRIPT_DIR/scripts/common.sh"

sep; info "LGPowerControl Installation"; sep
echo

# --- Package manager detection -------------------------------------------------
if   has pacman; then INSTALL_HINT="using: pacman -S"
elif has apt;    then INSTALL_HINT="using: apt install"
elif has dnf;    then INSTALL_HINT="using: dnf install"
else                  INSTALL_HINT="with your package manager"
fi

# --- TV IP validation ----------------------------------------------------------
if [[ -z "$LGTV_IP" ]]; then
    read -r -p "Enter TV IP address (e.g. 192.168.1.100): " LGTV_IP
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

echo
sep; info "HDMI Input Selection (Optional)"; sep
info "Select which HDMI port the computer is connected to."
info "The TV will switch to this input on power-on. Press Enter to skip."
echo
read -r -p "$(echo -e "${GRN}Enter number (1-5): ${RST}")" _hdmi_choice
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
bash "$SCRIPT_DIR/scripts/legacy_cleanup.sh"

confirm "All dependencies met. Confirm installation?" || { echo -e "${YEL}Aborted.${RST}"; exit 0; }

# --- Install bscpylgtv ---------------------------------------------------------
if [[ -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]]; then
    echo -e "${GRN}bscpylgtv already installed. Skipping.${RST}"
else
    _pip_log=$(mktemp)
    trap 'rm -f "$_pip_log"' EXIT
    echo -ne "${CYN}Installing bscpylgtv into $INSTALL_PATH ...${RST}"
    mkdir -p "$INSTALL_PATH"
    if  python3 -m venv "$INSTALL_PATH/bscpylgtv"              >> "$_pip_log" 2>&1 &&
        "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip >> "$_pip_log" 2>&1 &&
        "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv     >> "$_pip_log" 2>&1
    then
        ok
    else
        echo
        echo -e "${RED}Failed to install bscpylgtv. Full output:${RST}" >&2
        cat "$_pip_log" >&2
        exit 1
    fi
fi

# --- Install control script ----------------------------------------------------
cp "$SCRIPT_DIR/scripts/lgpowercontrol" "$INSTALL_PATH/lgpowercontrol"
chmod +x "$INSTALL_PATH/lgpowercontrol"

info "Setting up systemd services..."
cp "$SCRIPT_DIR/systemd/lgpowercontrol-shutdown.service" /etc/systemd/system/lgpowercontrol-shutdown.service
cp "$SCRIPT_DIR/systemd/lgpowercontrol-boot.service"     /etc/systemd/system/lgpowercontrol-boot.service
systemctl daemon-reload >/dev/null 2>&1
systemctl enable lgpowercontrol-boot.service >/dev/null 2>&1
systemctl enable lgpowercontrol-shutdown.service >/dev/null 2>&1

echo
echo -e "${GRN}Systemd services enabled:${RST}"
echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
echo

# --- Config file ---------------------------------------------------------------
ask_mode() {
    local -n _ret=$2
    local _choice
    read -r -p "  $1 [power]: " _choice
    case "$_choice" in
        screen|2) _ret=screen ;;
        *)        _ret=power  ;;
    esac
}

if [[ ! -f "$INSTALL_PATH/lgpowercontrol.conf" ]]; then
    sep; info "Power Mode Configuration"; sep
    echo
    echo "  power   Full power off. Maximum energy savings; TV takes a few seconds to turn on."
    echo "  screen  Screen off only. Wakes instantly; uses slightly more power while idle."
    echo
    echo "  Press Enter to accept the default (power), or type 'screen' to change."
    echo

    ask_mode "At startup and shutdown" BOOT_SHUTDOWN_MODE
    ask_mode "When the monitor sleeps"  MONITOR_MODE
    sep

    cp "$SCRIPT_DIR/lgpowercontrol.conf.template" "$INSTALL_PATH/lgpowercontrol.conf"
    sed -i \
        -e "s|^BOOT_SHUTDOWN_MODE=.*|BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE|" \
        -e "s|^MONITOR_MODE=.*|MONITOR_MODE=$MONITOR_MODE|" \
        "$INSTALL_PATH/lgpowercontrol.conf"
else
    info "Existing config found — behavior settings preserved."
fi

sed -i \
    -e "s|^LGTV_IP=.*|LGTV_IP=$LGTV_IP|" \
    -e "s|^LGTV_MAC=.*|LGTV_MAC=$LGTV_MAC|" \
    -e 's|^WOL_CMD=.*|WOL_CMD="'"$WOL_CMD"'"|' \
    -e "s|^HDMI_INPUT=.*|HDMI_INPUT=$HDMI_INPUT|" \
    "$INSTALL_PATH/lgpowercontrol.conf"
echo -e "${GRN}Config: $INSTALL_PATH/lgpowercontrol.conf${RST}"

info "Installing screen state monitor..."
cp "$SCRIPT_DIR/scripts/lgpowercontrol-monitor.sh" "$INSTALL_PATH/lgpowercontrol-monitor.sh"
chmod +x "$INSTALL_PATH/lgpowercontrol-monitor.sh"
cp "$SCRIPT_DIR/systemd/lgpowercontrol-monitor.service" /etc/systemd/system/lgpowercontrol-monitor.service
systemctl daemon-reload >/dev/null 2>&1
systemctl enable --now lgpowercontrol-monitor.service >/dev/null 2>&1
echo -e "${GRN}Screen state monitor installed and started.${RST}"

if [[ ! -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]]; then
    sep; info "TV Authorization"; sep
    echo "A dialog will appear on your TV screen — accept it with the remote."
    sep
    read -r -p "Press Enter to trigger the authorization dialog on your TV: "
    "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" -p "$INSTALL_PATH/.aiopylgtv.sqlite" "$LGTV_IP" get_power_state >/dev/null 2>&1
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
