#!/bin/bash

set -e

read -p "This script will uninstall LGTVBtw. Are you sure? [y/N] " answer
answer=${answer:-N}
if [[ "$answer" =~ ^[Nn]$ ]]; then
    echo "Aborted. No changes were made."
    exit 0
fi

# Check for root privileges via sudo
if [ "$(id -u)" -ne 0 ] || [ -z "$SUDO_USER" ]; then
    echo "ERROR: Run this script with sudo as a non-root user." >&2
    exit 1
fi

# Set SUDO_HOME if not defined
SUDO_HOME=${SUDO_HOME:-$(getent passwd "$SUDO_USER" | cut -d: -f6)}

systemctl disable lgtv-btw-boot.service
systemctl disable lgtv-btw-shutdown.service

rm -f /etc/systemd/system/lgtv-btw-boot.service
rm -f /etc/systemd/system/lgtv-btw-shutdown.service
rm -f $SUDO_HOME/.config/autostart/lgtv-btw-dbus-events.desktop
rm -rf $SUDO_HOME/.local/lgtv-btw

echo "LGTVBtw Uninstalled successfully"

exit 0
