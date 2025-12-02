#!/bin/bash
# Script to prepare for LG TV control setup by validating dependencies,
# checking network connectivity, and retrieving the TV's MAC address.
# Usage: ./install.sh <TV_IP_ADDRESS>

set -e # Exit immediately if a command exits with a non-zero status
LGTV_IP=$1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

clear
echo "LGPowerControl Installation"
echo "----------------------------"
echo

# Ensure IP is provided
if [ -z "$LGTV_IP" ]; then
    echo
    echo "Error: No IP address provided."
    echo "  Usage: ./install.sh <TV_IP_ADDRESS>"
    echo "  Example: ./install.sh 192.168.1.100"
    echo "  Tip: You can usually find your TV's IP address in its network settings or through your router’s web interface."
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
  echo "This script must NOT be run as root or with sudo." 1>&2
  exit 1
fi


INSTALL_PATH="$HOME/.local/lgpowercontrol"

# Function to check if command exists
cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check dependencies and provide install hints
check_dependency() {
    local pkg="$1"
    local cmd="${2:-$1}"

    echo -n "Checking for $cmd ..."
    if ! cmd_exists "$cmd"; then
        echo
        echo "Error: The '$pkg' package is not installed."
        echo "  Install it $INSTALL_HINT $pkg"
        exit 1
    fi
    echo " [OK]"
}

# Set install hint based on distro
if cmd_exists pacman; then
    INSTALL_HINT="using: sudo pacman -S"
elif cmd_exists apt; then
    INSTALL_HINT="using: sudo apt install"
elif cmd_exists dnf; then
    INSTALL_HINT="using: sudo dnf install"
else
    INSTALL_HINT="with your package manager"
fi

# Check required tools
# Example: check_dependency package test_command
check_dependency "iproute2" "ip"
check_dependency "python3"
if cmd_exists apt; then
    check_dependency "python3-venv" 
fi

if [[ "$INSTALL_HINT" == *dnf* ]]; then
    # wakeonlan is not available in Fedora, using ether-wake instead
    check_dependency "net-tools" "ether-wake"
else
    check_dependency "wakeonlan"
fi



# Validate IP format
if [[ ! "$LGTV_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] ||
    ! IFS='.' read -r a b c d <<<"$LGTV_IP" ||
    ((a < 0 || a > 255 || b < 0 || b > 255 || c < 0 || c > 255 || d < 0 || d > 255)); then
    echo "Error: '$LGTV_IP' is not a valid IPv4 address"
    exit 1
fi

# Check if IP is reachable
echo -n "Verifying IP $LGTV_IP is reachable ..."
if ! ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1; then
    echo -e "\nError: $LGTV_IP is unreachable"
    exit 1
fi
echo " [OK]"

# Retrieve MAC address using ip neigh
echo -n "Retrieving MAC address... "

LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)

if [[ ! "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
    echo -e "\nError: Could not detect a valid MAC address."
    echo "Make sure the TV is on and reachable (e.g., ping it first)."
    echo "Try manually checking with: ip neigh show $LGTV_IP"
    exit 1
fi

echo -n "$LGTV_MAC"
echo " [OK]"

# Confirm installation
echo "Installation path: $INSTALL_PATH"
echo
read -p "All dependencies met. Confirm installation? [Y/n] " answer
answer=${answer:-Y}
echo
if ! [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
    echo -e "\nInstallation aborted by user, no changes were made."
    exit 0
fi

# Install bscpylgtv in venv if not already present
if [ ! -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]; then
    mkdir -p "$INSTALL_PATH"
    python3 -m venv "$INSTALL_PATH/bscpylgtv"
    "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip

    if ! "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
        echo "ERROR: Failed to install bscpylgtv." >&2
        exit 1
    fi
fi

# Define power commands
PWR_OFF_CMD="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand $LGTV_IP power_off"

if cmd_exists wakeonlan; then
    WOL=$(command -v wakeonlan)
    PWR_ON_CMD="$WOL -i $LGTV_IP $LGTV_MAC"
elif cmd_exists ether-wake; then
    WOL=$(command -v ether-wake)
    PWR_ON_CMD="sudo $WOL $LGTV_MAC"
else
    echo "Error: Neither 'wakeonlan' nor 'ether-wake' is installed. Cannot continue." >&2
    exit 1
fi

# Setup systemd services
echo "Setting up Systemd services ..."
sudo cp "$SCRIPT_DIR/lgpowercontrol-shutdown.service /etc/systemd/system/"
sudo cp "$SCRIPT_DIR/lgpowercontrol-boot.service /etc/systemd/system/"

sudo sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" /etc/systemd/system/lgpowercontrol-shutdown.service
sudo sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" /etc/systemd/system/lgpowercontrol-boot.service

# Enable services
sudo systemctl daemon-reload
sudo systemctl enable lgpowercontrol-boot.service
sudo systemctl enable lgpowercontrol-shutdown.service

echo
echo "Systemd services enabled:"
echo "  - lgpowercontrol-boot.service (powers on TV at boot)"
echo "  - lgpowercontrol-shutdown.service (powers off TV at shutdown)"
echo

# Optional: KDE DBus event listener setup
echo "Optional: For GNOME / KDE, you can install a script that monitors screen lock events and powers the TV on or off accordingly."
echo
echo "  Note: This only works if unlocking after inactivity does not require a password."
echo "  Otherwise, the screen will remain off, and you'll need to enter your password blindly before the TV turns on."
echo
echo "  On Fedora-based systems using 'ether-wake', you'll also be prompted to add a 'sudoers' rule to allow 'ether-wake' to run without a password."
echo
read -p "Would you like to install it now? [Y/n] " answer
answer=${answer:-Y}

if [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]; then
    if cmd_exists ether-wake; then
        # Setup sudo rule for ether-wake (Fedora only)
        source "$SCRIPT_DIR/setup-sudo-etherwake.sh"
    fi
    AUTOSTART_DIR="$HOME/.config/autostart"
    LISTEN_SCRIPT="$INSTALL_PATH/lgpowercontrol-dbus-events.sh"
    DESKTOP_FILE="$AUTOSTART_DIR/lgpowercontrol-dbus-events.desktop"

    # ask user to select desktop environment.
    while true; do
        echo "Choose your desktop environment:"
        echo "1) KDE"
        echo "2) Gnome"
        echo "3) Other (Skip)"
        read -r de_choice

        case "$de_choice" in
            1)
                DESKTOP_ENV="freedesktop" # KDE
                break
                ;;
            2)
                DESKTOP_ENV="gnome" # GNOME
                break
                ;;
            3)
                DESKTOP_ENV="OTHER"
                break
                ;;
            *)
                echo "Invalid choice. Please try again."
                ;;
        esac
    done

    if [[ "$DESKTOP_ENV" != "OTHER" ]]; then
        cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.sh" "$LISTEN_SCRIPT"
        sed -i "s|DESKTOP_ENV|$DESKTOP_ENV|g" "$LISTEN_SCRIPT"
        sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" "$LISTEN_SCRIPT"
        sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" "$LISTEN_SCRIPT"
        chmod +x "$LISTEN_SCRIPT"

        mkdir -p "$AUTOSTART_DIR"
        cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.desktop" "$DESKTOP_FILE"
        sed -i "s|LISTEN_SCRIPT|$LISTEN_SCRIPT|g" "$DESKTOP_FILE"
        nohup "$LISTEN_SCRIPT" >/dev/null 2>&1 &
    else
        echo "DBus event listener installation skipped: unsupported or custom desktop environment selected."
    fi
fi

echo
echo "Installation complete!"
exit 0
