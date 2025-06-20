#!/bin/bash

# Check if running in a KDE environment
if ! command -v kwriteconfig5 >/dev/null 2>&1 && [ -z "$KDE_FULL_SESSION" ]; then
    echo "Warning: KDE environment not detected or kwriteconfig5 not installed." >&2
    read -p "Continue installing listen-for-lock-unlock-events.sh in autostart? [Y/n] " answer
    answer=${answer:-Y}
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        echo "Skipping KDE autostart setup."
        return 0
    fi
fi

# Set up paths
INSTALL_PATH="$SUDO_HOME/.local/lgtv_control"
AUTOSTART_DIR="$SUDO_HOME/.config/autostart"
LISTEN_SCRIPT="$INSTALL_PATH/listen-for-lock-unlock-events.sh"
DESKTOP_FILE="$AUTOSTART_DIR/listen-for-lock-unlock-events.desktop"

# Define commands for listen-for-lock-unlock-events.sh
PWR_OFF_CMD="/usr/local/bin/bscpylgtvcommand $LGTV_IP power_off"
PWR_ON_CMD="/usr/bin/wakeonlan -i $LGTV_IP $LGTV_MAC"

# Copy listen-for-lock-unlock-events.sh to INSTALL_PATH
echo "Copying listen-for-lock-unlock-events.sh to $INSTALL_PATH..."
sudo -u "$SUDO_USER" mkdir -p "$INSTALL_PATH"
if [ -f ./source/listen-for-lock-unlock-events.sh ]; then
    sudo -u "$SUDO_USER" cp ./source/listen-for-lock-unlock-events.sh "$LISTEN_SCRIPT"
else
    echo "Error: listen-for-lock-unlock-events.sh not found in current directory." >&2
    exit 1
fi

# Replace placeholders in listen-for-lock-unlock-events.sh
echo "Configuring listen-for-lock-unlock-events.sh with TV commands..."
sed -i "s|<PWR_OFF_CMD>|$PWR_OFF_CMD|g" "$LISTEN_SCRIPT"
sed -i "s|<PWR_ON_CMD>|$PWR_ON_CMD|g" "$LISTEN_SCRIPT"

# Make the script executable
chmod +x "$LISTEN_SCRIPT"

# Create KDE autostart .desktop file
echo "Creating KDE autostart entry..."
sudo -u "$SUDO_USER" mkdir -p "$AUTOSTART_DIR"
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Type=Application
Name=Listen for LG TV Lock/Unlock Events
Exec=$LISTEN_SCRIPT
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF

echo "KDE autostart configured for listen-for-lock-unlock-events.sh."