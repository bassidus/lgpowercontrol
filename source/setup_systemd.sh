#!/bin/bash

# Set up commands for systemd services
PWR_OFF_CMD="/usr/local/bin/bscpylgtvcommand $LGTV_IP power_off"
PWR_ON_CMD="/usr/bin/wakeonlan -i $LGTV_IP $LGTV_MAC"

# Set up systemd services
echo "Setting up systemd services..."
if [[ -f ./service-files/lgtv-power-off-at-shutdown.service && -f ./service-files/lgtv-power-on-at-boot.service ]]; then
    echo "Copying service files to /etc/systemd/system..."
    cp ./service-files/lgtv-power-off-at-shutdown.service /etc/systemd/system/
    cp ./service-files/lgtv-power-on-at-boot.service /etc/systemd/system/
else
    echo "Error: Service files not found. Ensure lgtv-power-off-at-shutdown.service and lgtv-power-on-at-boot.service are present." >&2
    exit 1
fi

# Configure service files
echo "Configuring systemd services..."
sed -i "s|<PWR_OFF_CMD>|$PWR_OFF_CMD|g" /etc/systemd/system/lgtv-power-off-at-shutdown.service
sed -i "s|<PWR_ON_CMD>|$PWR_ON_CMD|g" /etc/systemd/system/lgtv-power-on-at-boot.service

# Enable systemd services
echo "Enabling systemd services..."
systemctl daemon-reload
systemctl enable lgtv-power-on-at-boot.service
systemctl enable lgtv-power-off-at-shutdown.service

echo "Systemd services enabled:"
echo "  - lgtv-power-on-at-boot.service (powers on TV at boot)"
echo "  - lgtv-power-off-at-shutdown.service (powers off TV at shutdown)"