#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh <TV_IP_ADDRESS>

set -euo pipefail

readonly RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m'
readonly YEL='\033[0;33m' BLU='\033[0;34m' CYN='\033[0;36m'

readonly LGTV_IP="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly INSTALL_PATH="$HOME/.local/lgpowercontrol"
readonly LGCOMMAND="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand -p $INSTALL_PATH/.aiopylgtv.sqlite $LGTV_IP"
TEMP_DIR=$(mktemp -d)
readonly TEMP_DIR
trap 'rm -rf "$TEMP_DIR"' EXIT

LGTV_MAC="" INSTALL_HINT="" HDMI_INPUT=""
WOL_CMD="" PWR_OFF_CMD="" PWR_ON_CMD=""

die()  { echo -e "${RED}Error: $1${RST}" >&2; exit 1; }
info() { echo -e "${BLU}$1${RST}"; }
ok()   { echo -e " ${GRN}[OK]${RST}"; }

cmd_exists() { command -v "$1" >/dev/null 2>&1; }

check_dependency() {
    local pkg="$1" cmd="${2:-$1}"
    echo -ne "${CYN}Checking for $cmd ...${RST}"
    cmd_exists "$cmd" || die "'$pkg' is not installed. Install it $INSTALL_HINT $pkg"
    ok
}

check_python_venv() {
    echo -ne "${CYN}Checking for python3-venv ...${RST}"
    local d
    d=$(mktemp -d)
    if ! python3 -m venv "$d" >/dev/null 2>&1; then
        rm -rf "$d"
        local v
        v=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        die "python3-venv not functional. Try: sudo apt install python${v}-venv"
    fi
    rm -rf "$d"
    ok
}

set_install_hint() {
    if   cmd_exists pacman; then INSTALL_HINT="using: sudo pacman -S"
    elif cmd_exists apt;    then INSTALL_HINT="using: sudo apt install"
    elif cmd_exists dnf;    then INSTALL_HINT="using: sudo dnf install"
    else                         INSTALL_HINT="with your package manager"
    fi
}

check_req_tools() {
    check_dependency iproute2 ip
    check_dependency python3
    cmd_exists apt && check_python_venv
    if cmd_exists dnf; then
        check_dependency net-tools ether-wake
    else
        check_dependency wakeonlan
    fi
}

validate_ip() {
    [[ -z "$LGTV_IP" ]] && die "No IP address provided. Usage: ./install.sh <TV_IP_ADDRESS>"

    if [[ ! "$LGTV_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        die "'$LGTV_IP' is not a valid IPv4 address"
    fi
    IFS='.' read -r a b c d <<< "$LGTV_IP"
    ((a > 255 || b > 255 || c > 255 || d > 255)) && die "'$LGTV_IP' is not a valid IPv4 address"

    echo -ne "${CYN}Verifying $LGTV_IP is reachable ...${RST}"
    ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1 || die "$LGTV_IP is unreachable"
    ok
}

retrieve_mac() {
    echo -ne "${CYN}Retrieving MAC address ...${RST}"
    LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)
    [[ "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]] \
        || die "Could not detect MAC for $LGTV_IP. Ensure the TV is ON."
    echo -e " $LGTV_MAC${GRN} [OK]${RST}"
}

install_bscpylgtv() {
    if [[ -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]]; then
        echo -e "${GRN}bscpylgtv already installed. Skipping.${RST}"
        return
    fi
    info "Installing bscpylgtv into local venv..."
    mkdir -p "$INSTALL_PATH"
    python3 -m venv "$INSTALL_PATH/bscpylgtv"
    "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip
    "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv || die "Failed to install bscpylgtv"
    echo -e "${GRN}bscpylgtv installed.${RST}"
}

select_hdmi_input() {
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "HDMI Input Selection (Optional)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Select which HDMI port the computer is connected to."
    echo "The TV will switch to this input on power-on. Leave empty to skip."
    echo
    local choice
    read -r -p "Enter number (1-5): " choice
    if [[ "$choice" =~ ^[1-5]$ ]]; then
        HDMI_INPUT="HDMI_$choice"
        echo -e "${GRN}Will switch to $HDMI_INPUT on power-on.${RST}"
    elif [[ -n "$choice" ]]; then
        echo -e "${YEL}Invalid input. Skipping HDMI configuration.${RST}"
    fi
}

define_power_commands() {
    PWR_OFF_CMD="$INSTALL_PATH/lgpowercontrol OFF"
    PWR_ON_CMD="$INSTALL_PATH/lgpowercontrol ON"

    if cmd_exists wakeonlan; then
        WOL_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"
    elif cmd_exists ether-wake; then
        WOL_CMD="sudo $(command -v ether-wake) $LGTV_MAC"
    else
        die "Neither 'wakeonlan' nor 'ether-wake' found"
    fi

    sed -e "s|LGCOMMAND|$LGCOMMAND|g" \
        -e "s|INPUT|$HDMI_INPUT|g" \
        -e "s|WOL_CMD|$WOL_CMD|g" \
        "$SCRIPT_DIR/lgpowercontrol" > "$INSTALL_PATH/lgpowercontrol"
    chmod +x "$INSTALL_PATH/lgpowercontrol"
}

confirm_installation() {
    info "Installation path: $INSTALL_PATH"
    echo
    local answer
    read -r -p "All dependencies met. Confirm installation? [Y/n] " answer
    echo
    [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo -e "${YEL}Aborted.${RST}"; exit 0; }
}

setup_systemd_services() {
    info "Setting up systemd services..."

    sed "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" \
        "$SCRIPT_DIR/lgpowercontrol-shutdown.service" > "$TEMP_DIR/lgpowercontrol-shutdown.service"
    sed "s|PWR_ON_CMD|$PWR_ON_CMD|g" \
        "$SCRIPT_DIR/lgpowercontrol-boot.service" > "$TEMP_DIR/lgpowercontrol-boot.service"

    sudo cp "$TEMP_DIR/lgpowercontrol-shutdown.service" /etc/systemd/system/
    sudo cp "$TEMP_DIR/lgpowercontrol-boot.service" /etc/systemd/system/

    sudo systemctl daemon-reload
    sudo systemctl enable lgpowercontrol-boot.service
    sudo systemctl enable lgpowercontrol-shutdown.service

    echo
    echo -e "${GRN}Systemd services enabled:${RST}"
    echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
    echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
    echo
}

setup_sudo_etherwake() {
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "ether-wake Sudo Configuration"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "'ether-wake' requires sudo. A passwordless sudoers rule can be added."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    local answer
    read -r -p "Set this up now? [Y/n] " answer
    echo

    if [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        local tmp
        tmp=$(mktemp)
        echo "$USER ALL=(ALL) NOPASSWD: $(command -v ether-wake)" > "$tmp"
        if sudo visudo -c -f "$tmp"; then
            sudo cp "$tmp" /etc/sudoers.d/lgpowercontrol-etherwake
            sudo chmod 0440 /etc/sudoers.d/lgpowercontrol-etherwake
            echo -e "${GRN}Done: 'sudo ether-wake' now passwordless.${RST}"
        else
            echo -e "${RED}Sudoers rule invalid. Skipping.${RST}"
        fi
        rm -f "$tmp"
    else
        echo -e "${YEL}Automatic TV wake will not work until ether-wake is in sudoers.${RST}"
    fi
}

setup_dbus_listener() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Optional: Screen State Monitor"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Monitors display DPMS state to power the TV on/off automatically."
    echo "Works with GNOME, KDE, Cinnamon on both X11 and Wayland."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    local answer
    read -r -p "Install the screen state monitor? [Y/n] " answer

    if [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        cmd_exists dnf && cmd_exists ether-wake && setup_sudo_etherwake

        local listen_script="$INSTALL_PATH/lgpowercontrol-dbus-events.sh"
        local autostart_dir="$HOME/.config/autostart"

        info "Installing screen state monitor..."

        sed -e "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" \
            -e "s|PWR_ON_CMD|$PWR_ON_CMD|g" \
            "$SCRIPT_DIR/lgpowercontrol-dbus-events.sh" > "$listen_script"
        chmod +x "$listen_script"

        mkdir -p "$autostart_dir"
        sed "s|LISTEN_SCRIPT|$listen_script|g" \
            "$SCRIPT_DIR/lgpowercontrol-dbus-events.desktop" > "$autostart_dir/lgpowercontrol-dbus-events.desktop"

        nohup "$listen_script" >/dev/null 2>&1 &
        echo -e "${GRN}Screen state monitor installed and started.${RST}"
    fi
}

perform_tv_handshake() {
    [[ -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]] && return
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "TV Authorization Required"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Accept the prompt on your TV with the remote."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -r -p "Press ENTER to send the test command"
    $LGCOMMAND button INFO >/dev/null 2>&1
    sleep 1
    [[ -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]] || die "Authorization failed. Re-run install."
    echo -e "${GRN}Authorization complete!${RST}"
}

# --- main ---

[[ $EUID -eq 0 ]] && die "Do not run as root/sudo"

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "LGPowerControl Installation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

set_install_hint
validate_ip
check_req_tools
retrieve_mac
echo
confirm_installation
install_bscpylgtv
select_hdmi_input
define_power_commands
setup_systemd_services
setup_dbus_listener
perform_tv_handshake

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GRN}Installation complete!${RST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "TV will power on at boot and off at shutdown."
info "Logs: journalctl -t lgpowercontrol"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"