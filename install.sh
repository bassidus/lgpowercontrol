#!/bin/bash

set -e

# Check that pacman exists
if ! command -v pacman >/dev/null; then
    echo "ERROR: This script requires an Arch-based system." >&2
    exit 1
fi

# Check for root privileges via sudo
if [ "$(id -u)" -ne 0 ] || [ -z "$SUDO_USER" ]; then
    echo "ERROR: Run this script with sudo as a non-root user." >&2
    exit 1
fi

# Helper function for yes/no prompts
confirm() {
    read -p "$1 [Y/n]" answer
    answer=${answer:-Y}
    [[ "$answer" =~ ^[Yy]$ ]]
}

# Check that all required files exists in current directory
REQ_FILES="config.ini lgtv-btw-shutdown.service lgtv-btw-boot.service lgtv-btw-dbus-events.sh"
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
        if confirm "Install net-tools now?"; then
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

if ! confirm "Do you want to continue?"; then
    echo "Aborted. No changes were made."
    exit 0
fi

# Check for wakeonlan
if ! command -v wakeonlan >/dev/null; then
    if confirm "Install wakeonlan?"; then
        pacman -S --needed wakeonlan
    else
        echo "ERROR: Cannot proceed without wakeonlan." >&2
        exit 1
    fi
fi

# Set SUDO_HOME if not defined
SUDO_HOME=${SUDO_HOME:-$(getent passwd "$SUDO_USER" | cut -d: -f6)}

# Setup install path
INSTALL_PATH="$SUDO_HOME/.local/lgtv-btw"

# Check for bscpylgtv
if ! command -v bscpylgtv >/dev/null; then
    # Set up Python virtual environment and install bscpylgtv
    sudo -u "$SUDO_USER" mkdir -p "$INSTALL_PATH"
    sudo -u "$SUDO_USER" python -m venv "$INSTALL_PATH/bscpylgtv"
    if ! sudo -u "$SUDO_USER" "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
        echo "ERROR: Failed to install bscpylgtv." >&2
        exit 1
    fi

    # Copy bscpylgtvcommand to system-wide location
    cp "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" "/usr/local/bin/bscpylgtvcommand"
fi

# Define commands
PWR_OFF_CMD="$(command -v bscpylgtvcommand) $LGTV_IP power_off"
PWR_ON_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"

# Set up systemd services
cp lgtv-btw-shutdown.service /etc/systemd/system/
cp lgtv-btw-boot.service /etc/systemd/system/

sed -i "s|<PWR_OFF_CMD>|$PWR_OFF_CMD|g" /etc/systemd/system/lgtv-btw-shutdown.service
sed -i "s|<PWR_ON_CMD>|$PWR_ON_CMD|g" /etc/systemd/system/lgtv-btw-boot.service

# Enable systemd services
systemctl daemon-reload
systemctl enable lgtv-btw-boot.service
systemctl enable lgtv-btw-shutdown.service

echo "Systemd services enabled:"
echo "  - lgtv-btw-boot.service (powers on TV at boot)"
echo "  - lgtv-btw-shutdown.service (powers off TV at shutdown)"

echo -e "\nYou can also install a script that turns your TV off when the screen locks, and on when it unlocks (KDE only).\n"

if ! confirm "Do you want to install it?"; then
    echo "Installation complete!"
    echo "You may now turn off your computer and TV to test the power-on behavior."
    exit 0
fi

# Setup KDE autostart .desktop file
AUTOSTART_DIR="$SUDO_HOME/.config/autostart"
LISTEN_SCRIPT="$INSTALL_PATH/lgtv-btw-dbus-events.sh"
DESKTOP_FILE="$AUTOSTART_DIR/listen-for-lock-unlock-events.desktop"

sudo -u "$SUDO_USER" cp lgtv-btw-dbus-events.sh "$LISTEN_SCRIPT"

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
echo "You may now turn off your computer and TV to test the power-on behavior."
exit 0
