#!/bin/bash

# Set up paths
INSTALL_PATH="$SUDO_HOME/.local/lgtv_control"

# Set up Python virtual environment
echo "Creating Python virtual environment..."
sudo -u "$SUDO_USER" mkdir -p "$INSTALL_PATH/logs"
sudo -u "$SUDO_USER" python -m venv "$INSTALL_PATH/bscpylgtv"
echo "Installing bscpylgtv..."
if ! sudo -u "$SUDO_USER" "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
    echo "Error: Failed to install bscpylgtv. Check internet or pip settings." >&2
    exit 1
fi

# Verify bscpylgtvcommand
echo "Verifying bscpylgtv installation..."
if [ ! -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]; then
    echo "Error: bscpylgtvcommand not found after installation." >&2
    exit 1
fi

# Copy bscpylgtvcommand to system-wide location
echo "Copying bscpylgtvcommand to /usr/local/bin..."
cp "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" "/usr/local/bin/bscpylgtvcommand"