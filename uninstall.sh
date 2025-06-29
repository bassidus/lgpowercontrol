#!/bin/bash

# Exit immediately on any error
set -e

# Ask for user confirmation
read -p "This script will uninstall LGPowerControl and remove all its files. Are you sure? [y/N] " answer
answer=${answer:-N}
if [[ "$answer" =~ ^[Nn]$ ]]; then
    echo "Uninstallation cancelled. No changes were made."
    exit 0
fi

echo "Disabling systemd services..."
sudo systemctl disable lgpowercontrol-boot.service
sudo systemctl disable lgpowercontrol-shutdown.service

echo "Removing systemd service files..."
sudo rm -f /etc/systemd/system/lgpowercontrol-boot.service
sudo rm -f /etc/systemd/system/lgpowercontrol-shutdown.service

# Remove sudoers rule if it exists
if [ -f /etc/sudoers.d/lgpowercontrol-etherwake ]; then
    echo "Removing sudoers rule for ether-wake..."
    sudo rm -f /etc/sudoers.d/lgpowercontrol-etherwake
fi

echo "Removing autostart entry for KDE dbus listener..."
rm -f "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop"

echo "Deleting local installation files..."
rm -rf "$HOME/.local/lgpowercontrol"

echo "Killing all existing processes of lgpowercontrol-dbus-events.sh"
pkill -f lgpowercontrol-dbus-events.sh

echo
echo "LGPowerControl has been successfully uninstalled."

exit 0