#!/bin/bash

set -e

confirm() {
    read -p "$1 [Y/n] " answer
    answer=${answer:-Y}
    [[ "$answer" =~ ^[Yy]$ ]]
}

if ! confirm "Uninstall LGTVBtw?"; then
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

systemctl disable lgtv-power-on-at-boot.service
systemctl disable lgtv-power-off-at-shutdown.service

rm -f /etc/systemd/system/lgtv-power-on-at-boot.service
rm -f /etc/systemd/system/lgtv-power-off-at-shutdown.service
rm -f $SUDO_HOME/.config/autostart/listen-for-lock-unlock-events.desktop
rm -rf $SUDO_HOME/.local/lgtv-btw

echo "LGTVBtw Uninstalled successfully"