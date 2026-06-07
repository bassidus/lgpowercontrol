#!/bin/bash

# Abort immediately if anything fails
set -euo pipefail

# Load configuration variables such as LGTV_IP.
source ./lgpowercontrol.conf

# Exit if the script is not run as root.
[[ $EUID -eq 0 ]] || {
    echo "This script needs to be run as root or with sudo."
    exit 1
}

# Check that the TV is reachable.
if ! ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1; then
    echo "$LGTV_IP is unreachable. Aborting installation" >&2
    exit 1
fi

# Make sure pacman is available before trying to use it.
if ! command -v pacman >/dev/null 2>&1; then
    echo "pacman not found, this installer requires Arch Linux (or an Arch-based distro)."
    exit 1
fi

# Install required wakeonlan package.
pacman -S --noconfirm --needed wakeonlan

# Create the installation directory.
mkdir -p /opt/lgpowercontrol

# Create a Python virtual environment and install the Python dependency.
python3 -m venv /opt/lgpowercontrol/bscpylgtv
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet --upgrade pip
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet bscpylgtv

# Copy configuration, scripts, and systemd units into place.
cp ./lgpowercontrol.conf                     /opt/lgpowercontrol/
cp ./scripts/lgpowercontrol                  /opt/lgpowercontrol/
cp ./scripts/lgpowercontrol-monitor.sh       /opt/lgpowercontrol/
cp ./systemd/lgpowercontrol-shutdown.service /etc/systemd/system/
cp ./systemd/lgpowercontrol-boot.service     /etc/systemd/system/
cp ./systemd/lgpowercontrol-monitor.service  /etc/systemd/system/

# Make the installed scripts executable.
chmod +x /opt/lgpowercontrol/lgpowercontrol
chmod +x /opt/lgpowercontrol/lgpowercontrol-monitor.sh

# Reload systemd so it sees the new unit files.
systemctl daemon-reload

# Enable the boot and shutdown services.
systemctl enable lgpowercontrol-boot.service
systemctl enable lgpowercontrol-shutdown.service

# Enable and start the monitor service immediately.
systemctl enable --now lgpowercontrol-monitor.service

# Remove any stale auth database so the TV re-prompts for authorization.
if [[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]]; then
    rm /opt/lgpowercontrol/.aiopylgtv.sqlite
fi

# Prompt the user before triggering the TV authorization dialog.
echo "TV Authorization"
echo "A dialog will appear on your TV screen — accept it with the remote."
read -r -p "Press Enter to trigger the authorization dialog on your TV: "

# Trigger the authorization dialog by requesting the TV power state.
/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" get_power_state
echo "Installation complete!"