#!/bin/bash
#
# LGPowerControl Uninstallation Script
# Safely removes all LGPowerControl files, services, and configurations

# Exit immediately on any error
set -e

# Safely stop, disable, and remove a systemd service
# Arguments:
#   $1 - Service name to clean up
safe_cleanup_service() {
    local service_name="$1"
    local service_file="/etc/systemd/system/$service_name"
    
    # Check if the unit is known to systemd (even if not enabled)
    if sudo systemctl status "$service_name" >/dev/null 2>&1; then
        echo "⏹️  Found and stopping service: $service_name"
        
        # Stop the service safely
        sudo systemctl stop "$service_name" 2>/dev/null || true
        
        # Disable the service if it's currently enabled
        if sudo systemctl is-enabled --quiet "$service_name"; then
            sudo systemctl disable "$service_name"
        fi
    fi
    
    # Remove the unit file if it exists
    if sudo test -f "$service_file"; then
        echo "🗑️  Removing service file: $service_file"
        sudo rm -f "$service_file"
    fi
}

# Verify script is not run with root privileges
if [[ $EUID -eq 0 ]]; then
  echo "⚠️  This script must NOT be run as root or with sudo." 1>&2
  exit 1
fi

# Ask for user confirmation before proceeding
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🗑️  LGPowerControl Uninstallation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
read -p "This will uninstall LGPowerControl and remove all its files. Are you sure? [y/N] " answer
answer=${answer:-N}
if ! [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
    echo "⏹️  Uninstallation cancelled. No changes were made."
    exit 0
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔧 Systemd Service Cleanup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Service names
BOOT_SERVICE="lgpowercontrol-boot.service"
SHUTDOWN_SERVICE="lgpowercontrol-shutdown.service"
# RESUME_SERVICE="lgpowercontrol-resume.service"

safe_cleanup_service "$BOOT_SERVICE"
safe_cleanup_service "$SHUTDOWN_SERVICE"
# safe_cleanup_service "$RESUME_SERVICE"

# Reload systemd to ensure it forgets the removed files (safely)
sudo systemctl daemon-reload 2>/dev/null || true

# Check and remove NetworkManager dispatcher script
# DISPATCHER_SCRIPT="/etc/NetworkManager/dispatcher.d/pre-down.d/lgpowercontrol-sleep.sh"
# if sudo test -f "$DISPATCHER_SCRIPT"; then
#     echo "Removing NetworkManager dispatcher script..."
#     sudo rm -f "$DISPATCHER_SCRIPT"
# fi

echo "✅ Cleanup Complete"
echo

# Remove sudoers rule if it exists
if sudo test -f /etc/sudoers.d/lgpowercontrol-etherwake; then
    echo "🗑️  Removing sudoers rule for ether-wake..."
    sudo rm -f /etc/sudoers.d/lgpowercontrol-etherwake
fi

echo "🗑️  Removing autostart entry for dbus listener..."
rm -f "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop"

echo "🗑️  Deleting local installation files..."
rm -rf "$HOME/.local/lgpowercontrol"

echo "⏹️  Killing all existing processes of lgpowercontrol-dbus-events.sh"
pkill -f lgpowercontrol-dbus-events.sh 2>/dev/null || true # Suppress error if process isn't running

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ LGPowerControl has been successfully uninstalled."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit 0