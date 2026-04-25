#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh [TV_IP_ADDRESS]

set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo." >&2; exit 1; }

# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

try_install() {
    local label="$1" pkg="$2"
    echo
    confirm "  '$label' is not installed. Install it now?" || die "'$label' is required. Aborting."
    if   has pacman; then pacman -S --noconfirm "$pkg"
    elif has apt;    then apt install -y "$pkg"
    elif has dnf;    then dnf install -y "$pkg"
    else die "Cannot install '$label': no supported package manager found"
    fi || die "Failed to install '$label'"
    mkdir -p /opt/lgpowercontrol
    echo "$pkg" >> /opt/lgpowercontrol/installed_deps
}

# ---- init -------------------------------------------------------------------

LGTV_IP="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sep; info "LGPowerControl Installation"; sep
echo

# ---- validate IP ------------------------------------------------------------

if [[ -z "$LGTV_IP" ]]; then
    read -r -p "Enter TV IP address (e.g. 192.168.1.100): " LGTV_IP
fi
octet='(25[0-5]|2[0-4][0-9]|[01]?[0-9]{1,2})'
[[ "$LGTV_IP" =~ ^${octet}\.${octet}\.${octet}\.${octet}$ ]] \
    || die "'$LGTV_IP' is not a valid IPv4 address"
echo -n "${CYN}Verifying $LGTV_IP is reachable ...${RST}"
ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1 || die "$LGTV_IP is unreachable"
ok

# ---- check dependencies -----------------------------------------------------

echo -n "${CYN}Checking for ip ...${RST}"
has ip || { try_install "iproute2" "iproute2"; has ip || die "'ip' not found after install"; }
ok

echo -n "${CYN}Checking for python3 ...${RST}"
has python3 || { try_install "python3" "python3"; has python3 || die "'python3' not found after install"; }
ok

if has apt; then
    echo -n "${CYN}Checking for python3-venv ...${RST}"
    _venv_tmp=$(mktemp -d)
    if ! python3 -m venv "$_venv_tmp" >/dev/null 2>&1; then
        _pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        rm -rf "$_venv_tmp"
        _venv_pkg="python${_pyver}-venv"
        echo
        if confirm "  'python3-venv' is not functional. Install '$_venv_pkg' now?"; then
            apt install -y "$_venv_pkg" || die "Failed to install '$_venv_pkg'"
            mkdir -p /opt/lgpowercontrol
            echo "$_venv_pkg" >> /opt/lgpowercontrol/installed_deps
        else
            die "python3-venv is required. Install it manually: apt install $_venv_pkg"
        fi
    else
        rm -rf "$_venv_tmp"
        ok
    fi
fi

if has dnf; then
    echo -n "${CYN}Checking for ether-wake ...${RST}"
    has ether-wake || { try_install "net-tools (ether-wake)" "net-tools"; has ether-wake || die "'ether-wake' not found after install"; }
    ok
else
    echo -n "${CYN}Checking for wakeonlan ...${RST}"
    has wakeonlan || { try_install "wakeonlan" "wakeonlan"; has wakeonlan || die "'wakeonlan' not found after install"; }
    ok
fi

# ---- get MAC address --------------------------------------------------------

echo -n "${CYN}Retrieving MAC address ...${RST}"
LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)
[[ "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]] \
    || die "Could not detect MAC for $LGTV_IP. Ensure the TV is ON."
echo " $LGTV_MAC${GRN} [OK]${RST}"

# ---- HDMI input selection ---------------------------------------------------

echo
sep; info "HDMI Input Selection (Optional)"; sep
info "Select which HDMI port the computer is connected to."
info "The TV will switch to this input on power-on. Press Enter to skip."
echo
read -r -p "${GRN}Enter number (1-5): ${RST}" _hdmi_choice
if [[ "$_hdmi_choice" =~ ^[1-5]$ ]]; then
    HDMI_INPUT="HDMI_$_hdmi_choice"
    echo "${GRN}Will switch to $HDMI_INPUT on power-on.${RST}"
else
    HDMI_INPUT=""
    [[ -n "$_hdmi_choice" ]] && echo "${YEL}Invalid input. Skipping HDMI configuration.${RST}"
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

cleanup_legacy

confirm "All dependencies met. Confirm installation?" || { echo "${YEL}Aborted.${RST}"; exit 0; }

# ---- install bscpylgtv ------------------------------------------------------

if [[ -f /opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand ]]; then
    echo "${GRN}bscpylgtv already installed. Skipping.${RST}"
else
    _pip_log=$(mktemp)
    trap 'rm -f "$_pip_log"' EXIT
    echo -n "${CYN}Installing bscpylgtv into /opt/lgpowercontrol ...${RST}"
    mkdir -p /opt/lgpowercontrol
    if  python3 -m venv /opt/lgpowercontrol/bscpylgtv              >> "$_pip_log" 2>&1 &&
        /opt/lgpowercontrol/bscpylgtv/bin/pip install --upgrade pip >> "$_pip_log" 2>&1 &&
        /opt/lgpowercontrol/bscpylgtv/bin/pip install bscpylgtv     >> "$_pip_log" 2>&1
    then
        ok
    else
        echo
        echo "${RED}Failed to install bscpylgtv. Full output:${RST}" >&2
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
echo "${GRN}Systemd services enabled:${RST}"
echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
echo

# ---- config -----------------------------------------------------------------

sep; info "Power Mode Configuration"; sep
echo
echo "  ${GRN}1)${RST}  Screen off only   — wakes instantly; uses slightly more power while idle."
echo "  ${GRN}2)${RST}  Full power off     — maximum energy savings; TV takes a few seconds to turn on."
echo
read -r -p "  ${GRN}At startup and shutdown [1=screen off (default), 2=full power off]: ${RST}" _choice
[[ "$_choice" == "2" ]] && BOOT_SHUTDOWN_MODE=power || BOOT_SHUTDOWN_MODE=screen
read -r -p "  ${GRN}When the monitor sleeps [1=screen off (default), 2=full power off]: ${RST}" _choice
[[ "$_choice" == "2" ]] && MONITOR_MODE=power || MONITOR_MODE=screen
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

# 'power'  - Full power off. Maximum energy savings; TV takes a few seconds to turn on.
# 'screen' - Screen off only. Wakes instantly; uses slightly more power while idle. [Default]

BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE
MONITOR_MODE=$MONITOR_MODE

# --- Logging ------------------------------------------------------------------

# 'info'  - Normal operation events [Default]
# 'debug' - Verbose output; logs every poll cycle and all TV responses

LOG_LEVEL=info
EOF
echo "${GRN}Config: /opt/lgpowercontrol/lgpowercontrol.conf${RST}"

# ---- screen monitor ---------------------------------------------------------

info "Installing screen state monitor..."
cp "$SCRIPT_DIR/scripts/lgpowercontrol-monitor.sh" /opt/lgpowercontrol/lgpowercontrol-monitor.sh
chmod +x /opt/lgpowercontrol/lgpowercontrol-monitor.sh
cp "$SCRIPT_DIR/systemd/lgpowercontrol-monitor.service" /etc/systemd/system/lgpowercontrol-monitor.service
systemctl daemon-reload >/dev/null 2>&1
systemctl enable lgpowercontrol-monitor.service >/dev/null 2>&1
systemctl restart lgpowercontrol-monitor.service >/dev/null 2>&1
echo "${GRN}Screen state monitor installed and (re)started.${RST}"

# ---- TV authorization -------------------------------------------------------

if [[ ! -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]]; then
    sep; info "TV Authorization"; sep
    echo "A dialog will appear on your TV screen — accept it with the remote."
    sep
    read -r -p "Press Enter to trigger the authorization dialog on your TV: "
    /opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" get_power_state >/dev/null 2>&1 \
        || { rm -f /opt/lgpowercontrol/.aiopylgtv.sqlite; die "Unable to pair. Re-run install to try again."; }
    sleep 1
    [[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]] || die "Authorization failed. Re-run install to try again."
    echo "${GRN}Authorization complete!${RST}"
fi

echo
sep
echo "${GRN}Installation complete!${RST}"
sep
echo "  ${GRN}✔${RST}  TV turns ${GRN}on${RST} at boot, ${GRN}off${RST} at shutdown"
echo "  ${GRN}✔${RST}  TV turns ${GRN}off${RST} when display sleeps, ${GRN}on${RST} when it wakes"
echo "  ${YEL}✘${RST}  Screen lock does ${YEL}not${RST} turn off the TV"
echo "     (use your desktop's display sleep timer to blank the screen)"
echo
info "Logs: journalctl -t lgpowercontrol"
sep
