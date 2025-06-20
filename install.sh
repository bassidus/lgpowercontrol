#!/bin/bash

set -e

# Display help message if --help or -h is provided
if [[ "$1" == "--help" || "$1" == "-h" ]]; then
    cat << EOF
Usage: sudo ./install.sh [--help | -h]

This script sets up your Arch Linux system to control an LG TV. It:
- Validates TV IP and MAC addresses
- Installs required dependencies
- Sets up bscpylgtv in a virtual environment
- Configures systemd services to power on/off the TV at boot/shutdown
- Optionally installs a script to control TV on lock/unlock events in KDE

Requirements:
  - Run with sudo on an Arch Linux system with pacman
  - config.env file with LGTV_IP and LGTV_MAC (MAC can be blank if net-tools is installed)
  - lgtv-power-on-at-boot.service, lgtv-power-off-at-shutdown.service, and listen-for-lock-unlock-events.sh in the same directory

Example:
  sudo ./install.sh

See README.md for more details.
EOF
    exit 0
fi

# Check if running on Arch Linux with pacman
if ! command -v pacman >/dev/null 2>&1; then
    echo "Error: This script requires an Arch Linux system with pacman." >&2
    exit 1
fi

# Check for root privileges
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: Please run this script with sudo." >&2
    exit 1
fi

# Check if run via sudo (not as root user)
if [ ! "$SUDO_USER" ]; then
    echo "Error: Run this script with sudo as a regular user, not as root." >&2
    exit 1
fi

# Source modular scripts
echo "Starting installation..."
source ./source/validate.sh
source ./source/dependencies.sh
source ./source/setup_bscpylgtv.sh
source ./source/setup_systemd.sh

# Ask if user wants to install KDE autostart for lock/unlock events
read -p "Install listen-for-lock-unlock-events.sh in KDE autostart? [Y/n] " answer
answer=${answer:-Y}
if [[ "$answer" =~ ^[Yy]$ ]]; then
    source ./source/setup_kde_autostart.sh
fi

echo "Installation complete!"
echo "See README.md for troubleshooting tips."

exit 0