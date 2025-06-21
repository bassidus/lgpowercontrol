#!/bin/bash

set -e

# Check that pacman exists
if ! command -v pacman >/dev/null; then
    echo "ERROR: This script requires an Arch-based system." >&2
    exit 1
fi

# Helper function for yes/no prompts
confirm() {
    read -p "$1" answer
    answer=${answer:-Y}
    [[ "$answer" =~ ^[Yy]$ ]]
}

# Check that all required files exists in current directory
REQ_FILES="config.ini lgtv-power-off-at-shutdown.service lgtv-power-on-at-boot.service listen-for-lock-unlock-events.sh"
for file in $REQ_FILES; do
    if [[ ! -f $file ]]; then
        echo "ERROR: $file missing." >&2
        exit 1
    fi
done

# Get $LGTV_IP and $LGTV_MAC from config file
source <(grep = config.ini)

# Validate IPv4 format
if [[ "$LGTV_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    IFS='.' read -r o1 o2 o3 o4 <<<"$LGTV_IP"
    if ((o1 > 255 || o2 > 255 || o3 > 255 || o4 > 255)); then
        echo "ERROR: $LGTV_IP is invalid: octet out of range" >&2
        exit 1
    fi
    if ! ping -c 1 -W 1 "$LGTV_IP" >/dev/null; then
        echo "Warning: IP $LGTV_IP is not responding to ping..."
    else
        echo "IP $LGTV_IP is valid and responding."
    fi
else
    echo "ERROR: Invalid IP $LGTV_IP. Make sure you edit the 'config.ini' file before running this script." >&2
    exit 1
fi

# Validate MAC address
validate_mac() {
    if [[ ! "$1" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        echo "ERROR: Invalid MAC address: $1. Expected format is XX:XX:XX:XX:XX:XX." >&2
        return 1
    fi
    return 0
}

if ! validate_mac "$LGTV_MAC"; then
    echo "Trying to detect the MAC address for $LGTV_IP using ARP..."
    if ! command -v arp >/dev/null; then
        echo "net-tools (ARP) is not installed. It's needed to auto-detect the MAC address."
        if confirm "Install net-tools now? [Y/n]"; then
            pacman -S --needed net-tools
            LGTV_MAC=$(arp -a "$LGTV_IP" | awk '{print $4}')
            if ! validate_mac "$LGTV_MAC"; then
                echo "ERROR: Could not find a valid MAC address using arp. You must provide the MAC address manually." >&2
                exit 1
            fi
        else
            echo "ERROR: net-tools not installed. You must provide the MAC address manually." >&2
            echo "Tip: You can find the MAC via your router or TV network settings."
            exit 1
        fi
    else
        LGTV_MAC=$(arp -a "$LGTV_IP" | awk '{print $4}')
        if ! validate_mac "$LGTV_MAC"; then
            echo "ERROR: Could not find a valid MAC address using arp. You must provide the MAC address manually." >&2
            echo "Tip: You can find the MAC via your router or TV network settings."
            exit 1
        fi
    fi
fi
echo "MAC $LGTV_MAC is a valid MAC address."

if ! confirm "Do you want to continue? [Y/n]"; then
    echo "Aborted. No changes were made."
    exit 0
fi

# Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Please run this script with sudo." >&2
    exit 1
fi

# Check if run via sudo (not as root user)
if [ ! "$SUDO_USER" ]; then
    echo "ERROR: Run this script with sudo as a regular user, not as root." >&2
    exit 1
fi

# Check if $SUDO_HOME is set
if [ ! "$SUDO_HOME" ]; then
    SUDO_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
fi

# Check and install dependencies
check_dependencies() {
    local DEPS_MISSING=0
    local DEPS_TO_INSTALL=""

    if ! command -v wakeonlan >/dev/null; then
        DEPS_MISSING=1
        DEPS_TO_INSTALL="$DEPS_TO_INSTALL wakeonlan"
    fi

    if ! command -v pip >/dev/null; then
        DEPS_MISSING=1
        DEPS_TO_INSTALL="$DEPS_TO_INSTALL python-pip"
    fi

    if [ $DEPS_MISSING -eq 1 ]; then
        if confirm "Install missing dependencies ($DEPS_TO_INSTALL)? [Y/n]"; then
            pacman -S --needed $DEPS_TO_INSTALL
        else
            echo "ERROR: Cannot proceed without $DEPS_TO_INSTALL." >&2
            exit 1
        fi
    fi
}
check_dependencies

# Setup install path
INSTALL_PATH="$SUDO_HOME/.local/lgtv-btw"

# Set up Python virtual environment
sudo -u "$SUDO_USER" mkdir -p "$INSTALL_PATH"
sudo -u "$SUDO_USER" python -m venv "$INSTALL_PATH/bscpylgtv"
if ! sudo -u "$SUDO_USER" "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
    echo "ERROR: Failed to install bscpylgtv. Check your internet connection or pip configuration." >&2
    exit 1
fi

# Copy bscpylgtvcommand to system-wide location
cp "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" "/usr/local/bin/bscpylgtvcommand"

# Define commands
PWR_OFF_CMD="$(command -v bscpylgtvcommand) $LGTV_IP power_off"
PWR_ON_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"

# Set up systemd services
cp lgtv-power-off-at-shutdown.service /etc/systemd/system/
cp lgtv-power-on-at-boot.service /etc/systemd/system/

sed -i "s|<PWR_OFF_CMD>|$PWR_OFF_CMD|g" /etc/systemd/system/lgtv-power-off-at-shutdown.service
sed -i "s|<PWR_ON_CMD>|$PWR_ON_CMD|g" /etc/systemd/system/lgtv-power-on-at-boot.service

# Enable systemd services
systemctl daemon-reload
systemctl enable lgtv-power-on-at-boot.service
systemctl enable lgtv-power-off-at-shutdown.service

echo "Systemd services enabled:"
echo "  - lgtv-power-on-at-boot.service (powers on TV at boot)"
echo "  - lgtv-power-off-at-shutdown.service (powers off TV at shutdown)"

echo -e "\nYou can also install a script that turns your TV off when the screen locks, and on when it unlocks (KDE only).\n"

if ! confirm "Do you want to install it? [Y/n]"; then
    echo "Installation complete!"
    echo "You may now reboot to test the power-on behavior."
    exit 0
fi

# Setup KDE autostart .desktop file
AUTOSTART_DIR="$SUDO_HOME/.config/autostart"
LISTEN_SCRIPT="$INSTALL_PATH/listen-for-lock-unlock-events.sh"
DESKTOP_FILE="$AUTOSTART_DIR/listen-for-lock-unlock-events.desktop"

sudo -u "$SUDO_USER" cp listen-for-lock-unlock-events.sh "$LISTEN_SCRIPT"

sed -i "s|<PWR_OFF_CMD>|$PWR_OFF_CMD|g" "$LISTEN_SCRIPT"
sed -i "s|<PWR_ON_CMD>|$PWR_ON_CMD|g" "$LISTEN_SCRIPT"
chmod +x "$LISTEN_SCRIPT"
sudo -u "$SUDO_USER" mkdir -p "$AUTOSTART_DIR"
cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Exec=$LISTEN_SCRIPT
Icon=application-x-shellscript
Name=Listen for LG TV Lock/Unlock Events
Type=Application
X-KDE-AutostartScript=true
EOF

echo "Installation complete!"
echo "You may now reboot to test the power-on behavior."
exit 0
