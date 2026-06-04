#!/bin/bash

# Abort immediately if anything fails, rather than limping forward with a broken state.
set -euo pipefail

# Exit if the script is not run as root.
[[ $EUID -eq 0 ]] || {
    echo "This script needs to be run as root or with sudo."
    exit 1
}

# Make sure pacman is available before trying to use it.
if ! command -v pacman >/dev/null 2>&1; then
    echo "pacman not found, this installer requires Arch Linux (or an Arch-based distro)."
    exit 1
fi

# Install required system packages.
pacman -S --noconfirm --needed wakeonlan

# Load configuration variables such as LGTV_IP.
source ./lgpowercontrol.conf

# Install location for the project files.
install_dir=/opt/lgpowercontrol
venv_dir="${install_dir}/bscpylgtv"

# Create the installation directory.
mkdir -p "$install_dir"

# Create a Python virtual environment and install the Python dependency.
python3 -m venv "$venv_dir"
"$venv_dir/bin/pip" install --quiet --upgrade pip
"$venv_dir/bin/pip" install --quiet bscpylgtv

# Copy configuration, scripts, and systemd units into place.
cp ./lgpowercontrol.conf                     "$install_dir/"
cp ./scripts/lgpowercontrol                  "$install_dir/"
cp ./scripts/lgpowercontrol-monitor.sh       "$install_dir/"
cp ./systemd/lgpowercontrol-shutdown.service /etc/systemd/system/
cp ./systemd/lgpowercontrol-boot.service     /etc/systemd/system/
cp ./systemd/lgpowercontrol-monitor.service  /etc/systemd/system/

# Make the installed scripts executable.
chmod +x "$install_dir/lgpowercontrol"
chmod +x "$install_dir/lgpowercontrol-monitor.sh"

# Reload systemd so it sees the new unit files.
systemctl daemon-reload

# Enable the boot and shutdown services.
systemctl enable lgpowercontrol-boot.service
systemctl enable lgpowercontrol-shutdown.service

# Enable and start the monitor service immediately.
systemctl enable --now lgpowercontrol-monitor.service

# Remove any old authorization database so the TV prompts again if needed.
if [[ -f "$install_dir/.aiopylgtv.sqlite" ]]; then
    rm "$install_dir/.aiopylgtv.sqlite"
fi

# Prompt the user before triggering the TV authorization dialog.
echo "TV Authorization"
echo "A dialog will appear on your TV screen — accept it with the remote."
read -r -p "Press Enter to trigger the authorization dialog on your TV: "

# Trigger the authorization dialog by requesting the TV power state.
"$venv_dir/bin/bscpylgtvcommand" -p "$install_dir/.aiopylgtv.sqlite" "$LGTV_IP" get_power_state