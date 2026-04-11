#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh [TV_IP_ADDRESS]

set -euo pipefail

[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

# ---- helpers ----------------------------------------------------------------

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m' YEL='\033[0;33m' BLU='\033[0;94m' CYN='\033[0;36m'
SEP='----------------------------------------------------------------'

die()     { echo -e "${RED}Error: $1${RST}" >&2; exit 1; }
info()    { echo -e "${BLU}$1${RST}"; }
ok()      { echo -e " ${GRN}[OK]${RST}"; }
sep()     { echo -e "${BLU}$SEP${RST}"; }
has()     { command -v "$1" >/dev/null 2>&1; }
confirm() { local a; read -r -p "$1 [Y/n] " a; echo; [[ "${a:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]; }

# ---- init -------------------------------------------------------------------

LGTV_IP="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sep; info "LGPowerControl Installation"; sep
echo

# ---- package manager --------------------------------------------------------

if   has pacman; then INSTALL_HINT="using: pacman -S"
elif has apt;    then INSTALL_HINT="using: apt install"
elif has dnf;    then INSTALL_HINT="using: dnf install"
else                  INSTALL_HINT="with your package manager"
fi

# ---- validate IP ------------------------------------------------------------

if [[ -z "$LGTV_IP" ]]; then
    read -r -p "Enter TV IP address (e.g. 192.168.1.100): " LGTV_IP
fi
octet='(25[0-5]|2[0-4][0-9]|[01]?[0-9]{1,2})'
[[ "$LGTV_IP" =~ ^${octet}\.${octet}\.${octet}\.${octet}$ ]] \
    || die "'$LGTV_IP' is not a valid IPv4 address"
echo -ne "${CYN}Verifying $LGTV_IP is reachable ...${RST}"
ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1 || die "$LGTV_IP is unreachable"
ok

# ---- check dependencies -----------------------------------------------------

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

# ---- get MAC address --------------------------------------------------------

echo -ne "${CYN}Retrieving MAC address ...${RST}"
LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)
[[ "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]] \
    || die "Could not detect MAC for $LGTV_IP. Ensure the TV is ON."
echo -e " $LGTV_MAC${GRN} [OK]${RST}"

# ---- HDMI input selection ---------------------------------------------------

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

# ---- wake-on-LAN command ----------------------------------------------------
if has wakeonlan; then
    WOL_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"
elif has ether-wake; then
    WOL_CMD="$(command -v ether-wake) $LGTV_MAC"
else
    die "Neither 'wakeonlan' nor 'ether-wake' found"
fi

info "Installation path: /opt/lgpowercontrol"

# ---- legacy cleanup ---------------------------------------------------------
# Removes artefacts from previous installs to avoid duplicate processes/services.

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

for _f in /etc/sudoers.d/lgpowercontrol-etherwake \
          /usr/local/bin/bscpylgtvcommand \
          /opt/lgpowercontrol/lgpowercontrol-dbus-events.sh; do
    [[ -f "$_f" ]] || continue
    echo -e "${YEL}Removing legacy: $_f${RST}"
    rm -f "$_f"; _legacy_cleaned=true
done

$_legacy_cleaned && { systemctl daemon-reload 2>/dev/null || true; echo -e "${GRN}Legacy files cleaned up.${RST}"; }

confirm "All dependencies met. Confirm installation?" || { echo -e "${YEL}Aborted.${RST}"; exit 0; }

# ---- install bscpylgtv ------------------------------------------------------

if [[ -f /opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand ]]; then
    echo -e "${GRN}bscpylgtv already installed. Skipping.${RST}"
else
    _pip_log=$(mktemp)
    trap 'rm -f "$_pip_log"' EXIT
    echo -ne "${CYN}Installing bscpylgtv into /opt/lgpowercontrol ...${RST}"
    mkdir -p /opt/lgpowercontrol
    if  python3 -m venv /opt/lgpowercontrol/bscpylgtv              >> "$_pip_log" 2>&1 &&
        /opt/lgpowercontrol/bscpylgtv/bin/pip install --upgrade pip >> "$_pip_log" 2>&1 &&
        /opt/lgpowercontrol/bscpylgtv/bin/pip install bscpylgtv     >> "$_pip_log" 2>&1
    then
        ok
    else
        echo
        echo -e "${RED}Failed to install bscpylgtv. Full output:${RST}" >&2
        cat "$_pip_log" >&2
        exit 1
    fi
fi

# ---- install control script and services ------------------------------------

cp "$SCRIPT_DIR/scripts/lgpowercontrol" /opt/lgpowercontrol/lgpowercontrol
chmod +x /opt/lgpowercontrol/lgpowercontrol

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

# ---- config -----------------------------------------------------------------

ask_mode() {
    local -n _ret=$2
    local _choice
    while true; do
        read -r -p "$(echo -e "  ${GRN}$1 [1/2]: ${RST}")" _choice
        case "$_choice" in
            1|"") _ret=power;  break ;;
            2)    _ret=screen; break ;;
            *)    echo -e "  ${RED}Invalid choice. Enter 1 or 2.${RST}" ;;
        esac
    done
}

sep; info "Power Mode Configuration"; sep
echo
echo -e "  ${GRN}1)${RST}  Full power off. Maximum energy savings; TV takes a few seconds to turn on."
echo -e "  ${GRN}2)${RST}  Screen off only. Wakes instantly; uses slightly more power while idle."
echo
echo -e "  ${YEL}Type 1 or 2 or press Enter to accept the default (Full power off)${RST}"
echo

ask_mode "At startup and shutdown" BOOT_SHUTDOWN_MODE
ask_mode "When the monitor sleeps"  MONITOR_MODE
sep

cat > /opt/lgpowercontrol/lgpowercontrol.conf << EOF
# LGPowerControl configuration

# After editing, restart the monitor service to apply changes:
#   sudo systemctl restart lgpowercontrol-monitor.service

# --- Remote Interface Settings ------------------------------------------------

LGTV_IP=$LGTV_IP
LGTV_MAC=$LGTV_MAC
WOL_CMD=($WOL_CMD)
HDMI_INPUT=$HDMI_INPUT

# --- Behavior -----------------------------------------------------------------

# 'power'  - Full power off. Maximum energy savings; TV takes a few seconds to turn on. [Default]
# 'screen' - Screen off only. Wakes instantly; uses slightly more power while idle.

BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE
MONITOR_MODE=$MONITOR_MODE
EOF
echo -e "${GRN}Config: /opt/lgpowercontrol/lgpowercontrol.conf${RST}"

# ---- screen monitor ---------------------------------------------------------

info "Installing screen state monitor..."
cp "$SCRIPT_DIR/scripts/lgpowercontrol-monitor.sh" /opt/lgpowercontrol/lgpowercontrol-monitor.sh
chmod +x /opt/lgpowercontrol/lgpowercontrol-monitor.sh
cp "$SCRIPT_DIR/systemd/lgpowercontrol-monitor.service" /etc/systemd/system/lgpowercontrol-monitor.service
systemctl daemon-reload >/dev/null 2>&1
systemctl enable --now lgpowercontrol-monitor.service >/dev/null 2>&1
echo -e "${GRN}Screen state monitor installed and started.${RST}"

# ---- TV authorization -------------------------------------------------------

if [[ ! -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]]; then
    sep; info "TV Authorization"; sep
    echo "A dialog will appear on your TV screen — accept it with the remote."
    sep
    read -r -p "Press Enter to trigger the authorization dialog on your TV: "
    /opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" get_power_state >/dev/null 2>&1 \
        || { rm -f /opt/lgpowercontrol/.aiopylgtv.sqlite; die "Unable to pair. Re-run install to try again."; }
    sleep 1
    [[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]] || die "Authorization failed. Re-run install."
    echo -e "${GRN}Authorization complete!${RST}"
fi

echo
sep
echo -e "${GRN}Installation complete!${RST}"
info "Logs: journalctl -t lgpowercontrol"
sep
