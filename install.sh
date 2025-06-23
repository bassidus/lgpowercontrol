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

# Check that all required files exists in current directory
REQ_FILES="config.ini lgtv-btw-shutdown.service lgtv-btw-boot.service lgtv-btw-dbus-events.sh"
for file in $REQ_FILES; do
    if [[ ! -f $file ]]; then
        echo "ERROR: $file missing." >&2
        exit 1
    fi
done

# Helper function for yes/no prompts
confirm() {
    read -p "$1 [Y/n] " answer
    answer=${answer:-Y}
    [[ "$answer" =~ ^[Yy]$ ]]
}

# Invalid IP message
invalid_ip_msg() {
    echo "ERROR: IP $LGTV_IP is invalid or unreachable." >&2
    echo "Check your config and ensure the TV is ON and connected."
    exit 1
}

# Invalid MAC message
invalid_mac_msg() {
    echo "ERROR: No valid MAC address found for $LGTV_IP." >&2
    echo "Please ensure that 'net-tools' is installed, or manually set the MAC address in the config file before rerunning this script."
    echo "Tip: You can usually find the MAC address in your TV's network settings or your router's device list."
    exit 1
}

# Get $LGTV_IP and $LGTV_MAC from config file
source <(grep -v '^#' config.ini | grep =)

# Validate IP and ping
if [[ "$LGTV_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    IFS='.' read -r o1 o2 o3 o4 <<<"$LGTV_IP"
    ((o1 > 255 || o2 > 255 || o3 > 255 || o4 > 255)) || ping -c 1 -W 1 "$LGTV_IP" >/dev/null || invalid_ip_msg
else
    invalid_ip_msg
fi

# MAC validation
validate_mac() {
    [[ "$1" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]
}

# Check if arp is installed
if command -v arp >/dev/null; then
    ARP_INSTALLED=true
else
    ARP_INSTALLED=false
fi

# Detect MAC via ARP if needed
detect_mac() {
    if ! $ARP_INSTALLED; then
        if confirm "Missing 'net-tools'. Required to auto-detect MAC. Install now?"; then
            if ! pacman -S --needed net-tools; then
                echo "ERROR: Failed to install net-tools." >&2
                exit 1
            fi
        else
            invalid_mac_msg
        fi
    fi

    if ! ping -c 1 "$LGTV_IP" >/dev/null; then
        invalid_ip_msg
    fi

    LGTV_MAC=$(arp -a "$LGTV_IP" | awk '{print $4}')
    if ! validate_mac "$LGTV_MAC"; then
        invalid_mac_msg
    fi
}

if ! validate_mac "$LGTV_MAC" || $ARP_INSTALLED; then
    detect_mac
fi

# Ensure wakeonlan is available
if ! command -v wakeonlan >/dev/null; then
    if confirm "Install wakeonlan?"; then
        if ! pacman -S --needed wakeonlan; then
            echo "ERROR: Failed to install wakeonlan." >&2
            exit 1
        fi
    else
        echo "ERROR: wakeonlan is required for LGTWBtw. Cannot proceed without it." >&2
        exit 1
    fi
fi

# Final confirmation
echo -e "\nLGTVBtw will be installed with the following settings:\n"
echo "  - IP:  $LGTV_IP"

if $ARP_INSTALLED; then
    echo "  - MAC: $LGTV_MAC (validated via ARP)"
else
    echo "  - MAC: $LGTV_MAC (not validated)"
    echo
    echo "  (Note: The MAC address format is valid but was manually set in the config."
    echo "   This script requires net-tools to verify it automatically, so please"
    echo "   double-check that it matches your TV.)"
fi

echo

if ! confirm "Proceed with installation?"; then
    echo "Installation aborted."
    exit 0
fi

# Set SUDO_HOME if not defined and setup install path
SUDO_HOME=${SUDO_HOME:-$(getent passwd "$SUDO_USER" | cut -d: -f6)}
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
