#!/bin/bash

# Exit if the script is not run as root.
[[ $EUID -eq 0 ]] || {
    echo "This script needs to be run as root or with sudo." >&2
    exit 1
}

# Disable and stop all LGPowerControl systemd services.
systemctl disable --now \
    lgpowercontrol-boot.service \
    lgpowercontrol-shutdown.service \
    lgpowercontrol-monitor.service

# Remove the installation directory and all its contents.
rm -rf /opt/lgpowercontrol

# Remove all systemd unit files for LGPowerControl.
rm -f /etc/systemd/system/lgpowercontrol*

# Reload systemd so it drops the removed unit files from its configuration.
systemctl daemon-reload

# Confirm that uninstallation is complete.
echo "LGPowerControl uninstalled."