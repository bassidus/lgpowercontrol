#!/bin/bash
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }

systemctl disable --now \
    lgpowercontrol-boot.service \
    lgpowercontrol-shutdown.service \
    lgpowercontrol-monitor.service

systemctl --global disable lgpowercontrol-notify.service 2> /dev/null
if [[ -n "${SUDO_USER:-}" ]]; then
    systemctl --machine="${SUDO_USER}@" --user stop lgpowercontrol-notify.service 2> /dev/null
fi

rm -rf /opt/lgpowercontrol
rm -f /etc/systemd/system/lgpowercontrol*
rm -f /etc/systemd/user/lgpowercontrol*
systemctl daemon-reload

echo "LGPowerControl uninstalled."