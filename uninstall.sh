#!/bin/bash
# LGPowerControl uninstaller
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo." >&2; exit 1; }
systemctl disable --now lgpowercontrol-boot.service lgpowercontrol-shutdown.service lgpowercontrol-monitor.service
rm -rf /opt/lgpowercontrol
rm -f /etc/systemd/system/lgpowercontrol*
systemctl daemon-reload
echo "LGPowerControl uninstalled."