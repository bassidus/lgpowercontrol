#!/bin/bash
#
# LGPowerControl Installation Script
# Prepares and installs LG TV control setup by validating dependencies,
# checking network connectivity, and retrieving the TV's MAC address.
# Usage: ./install.sh <TV_IP_ADDRESS>

set -e # Exit immediately if a command exits with a non-zero status

# Set constants
LGTV_IP=$1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PATH="$HOME/.local/lgpowercontrol"
LGCOMMAND="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand -p $INSTALL_PATH/.aiopylgtv.sqlite $LGTV_IP"
TEMP_DIR=$(mktemp -d)

# Check if a command exists in the system PATH
# Arguments:
#   $1 - Command name to check
# Returns:
#   0 if command exists, 1 otherwise
cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check dependencies and provide installation hints
# Arguments:
#   $1 - Package name
#   $2 - Command to test (defaults to package name if not provided)
check_dependency() {
    local pkg="$1"
    local cmd="${2:-$1}"

    echo -n "Checking for $cmd ..."
    if ! cmd_exists "$cmd"; then
        echo
        echo "❌ Error: The '$pkg' package is not installed."
        echo "   Install it $INSTALL_HINT $pkg"
        exit 1
    fi
    echo " ✓ [OK]"
}

# Validate that TV IP address is provided
# Exits with error message if no IP address is given
ip_check() {
    if [ -z "$LGTV_IP" ]; then
        echo
        echo "❌ Error: No IP address provided."
        echo "   Usage: ./install.sh <TV_IP_ADDRESS>"
        echo "   Example: ./install.sh 192.168.1.100"
        echo
        echo "💡 Tip: You can usually find your TV's IP address in its network"
        echo "   settings or through your router's web interface."
        exit 1
    fi
}

# Ensure the script is not run with root privileges
# Exits with error if run as root or with sudo
sudo_check() {
    if [[ $EUID -eq 0 ]]; then
        echo "⚠️  This script must NOT be run as root or with sudo." 1>&2
        exit 1
    fi
}

# Detect package manager and set installation hint
# Sets INSTALL_HINT variable based on detected package manager
set_install_hint() {
    if cmd_exists pacman; then
        INSTALL_HINT="using: sudo pacman -S"
    elif cmd_exists apt; then
        INSTALL_HINT="using: sudo apt install"
    elif cmd_exists dnf; then
        INSTALL_HINT="using: sudo dnf install"
    else
        INSTALL_HINT="with your package manager"
    fi
}

# Check for required tools and packages
# Verifies that all necessary dependencies are installed
check_req_tools() {
    check_dependency "iproute2" "ip"
    check_dependency "python3"
    if cmd_exists apt; then
        check_dependency "python3-venv" 
    fi

    if cmd_exists dnf; then
        # wakeonlan is not available in Fedora, using ether-wake instead
        check_dependency "net-tools" "ether-wake"
    else
        check_dependency "wakeonlan"
    fi
}

# Validate TV IP address format and connectivity
# Checks IPv4 format and network reachability
validate_ip() {
    # Validate IP format
    if [[ ! "$LGTV_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] ||
        ! IFS='.' read -r a b c d <<<"$LGTV_IP" ||
        ((a > 255 || b > 255 || c > 255 || d > 255)); then
        echo "❌ Error: '$LGTV_IP' is not a valid IPv4 address"
        exit 1
    fi

    # Check if IP is reachable
    echo -n "Verifying IP $LGTV_IP is reachable ..."
    if ! ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1; then
        echo -e "\n❌ Error: $LGTV_IP is unreachable"
        exit 1
    fi
    echo " ✓ [OK]"
}

# Retrieve TV MAC address from ARP table
# Uses 'ip neigh' to automatically detect the MAC address
retrieve_mac() {
    echo -n "Retrieving MAC address... "

    LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)

    if [[ ! "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        echo -e "\n❌ Error: Could not detect a valid MAC address for $LGTV_IP."
        echo "⚠️  Action Required: Please ensure the TV is **ON** and reachable"
        echo "   (e.g., ping it) to populate the ARP table."
        echo "   Try manually checking with: ip neigh show $LGTV_IP"
        exit 1
    fi

    echo -n "$LGTV_MAC"
    echo " ✓ [OK]"
}

# Install bscpylgtv Python library in virtual environment
# Creates a venv and installs bscpylgtv if not already present
install_bscpylgtv() {
    if [ ! -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]; then
        echo "📦 Installing bscpylgtv into local Python Virtual Environment..."
        mkdir -p "$INSTALL_PATH"
        python3 -m venv "$INSTALL_PATH/bscpylgtv"
        "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip

        if ! "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
            echo "❌ ERROR: Failed to install bscpylgtv." >&2
            exit 1
        fi
        echo "✅ bscpylgtv installed successfully."
    else
        echo "✅ bscpylgtv already installed in $INSTALL_PATH. Skipping installation."
    fi
}

# Prompt user to select HDMI input port
# Configures automatic HDMI input switching on TV power-on
select_hdmi_input() {
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📺 HDMI Input Selection (Optional)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Select which HDMI port the computer is connected to."
    echo "The TV will automatically switch to this input when powered on."
    echo "Leave empty to skip."
    echo
    read -p "Enter number (1-5): " HDMI_CHOICE

    if [ -n "$HDMI_CHOICE" ]; then
        # Validate input is a single digit 1-5
        if [[ "$HDMI_CHOICE" =~ ^[1-5]$ ]]; then
             local hdmi_input="HDMI_$HDMI_CHOICE"
             
             echo "✅ Configuring automatic switch to $hdmi_input."
             
             # Construct the command
             HDMI_INPUT="$hdmi_input"
        else
             echo "⚠️  Invalid input '$HDMI_CHOICE'. Skipping HDMI input configuration."
        fi
    else
        echo "⏭️  Skipping HDMI input configuration."
    fi
}

# Define power control commands and prepare scripts
# Sets up Wake-on-LAN and power commands, configures lgpowercontrol script
define_power_commands() {
    PWR_OFF_CMD="$INSTALL_PATH/lgpowercontrol OFF"
    PWR_ON_CMD="$INSTALL_PATH/lgpowercontrol ON"

    if cmd_exists wakeonlan; then
        WOL=$(command -v wakeonlan)
        WOL_CMD="$WOL -i $LGTV_IP $LGTV_MAC"
    elif cmd_exists ether-wake; then
        WOL=$(command -v ether-wake)
        WOL_CMD="sudo $WOL $LGTV_MAC"
    else
        echo "❌ Error: Neither 'wakeonlan' nor 'ether-wake' is installed. Cannot continue." >&2
        exit 1
    fi

    cp "$SCRIPT_DIR/lgpowercontrol" "$TEMP_DIR/lgpowercontrol"
    #cp "$SCRIPT_DIR/lgpowercontrol-sleep.sh" "$TEMP_DIR/lgpowercontrol-sleep.sh"

    sed -i "s|LGCOMMAND|$LGCOMMAND|g" "$TEMP_DIR/lgpowercontrol"
    sed -i "s|INPUT|$HDMI_INPUT|g" "$TEMP_DIR/lgpowercontrol"
    sed -i "s|WOL_CMD|$WOL_CMD|g" "$TEMP_DIR/lgpowercontrol"
    #sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" "$TEMP_DIR/lgpowercontrol-sleep.sh"

    cp "$TEMP_DIR/lgpowercontrol" "$INSTALL_PATH/lgpowercontrol"
    #sudo cp "$TEMP_DIR/lgpowercontrol-sleep.sh" "/etc/NetworkManager/dispatcher.d/pre-down.d/lgpowercontrol-sleep.sh"

    chmod +x "$INSTALL_PATH/lgpowercontrol"
    #sudo chmod +x "/etc/NetworkManager/dispatcher.d/pre-down.d/lgpowercontrol-sleep.sh"
}

# Confirm installation with user
# Displays installation path and requests confirmation
confirm_installation() {
    echo "📍 Installation path: $INSTALL_PATH"
    echo
    read -p "✅ All dependencies met. Confirm installation? [Y/n] " answer
    answer=${answer:-Y}
    echo
    if ! [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        echo -e "\n⏹️  Installation aborted by user, no changes were made."
        exit 0
    fi
}

# Clean up temporary directory
# Removes the temporary directory created during installation
cleanup() {
    rm -rf "$TEMP_DIR"
}

# Set up systemd services for boot and shutdown
# Configures and enables systemd services for automatic TV control
systemd_setup() {
    echo "⚙️  Setting up Systemd services..."
    
    # Copy files to TEMP_DIR and perform substitution
    cp "$SCRIPT_DIR/lgpowercontrol-shutdown.service" "$TEMP_DIR/lgpowercontrol-shutdown.service"
    cp "$SCRIPT_DIR/lgpowercontrol-boot.service" "$TEMP_DIR/lgpowercontrol-boot.service"
    # cp "$SCRIPT_DIR/lgpowercontrol-resume.service" "$TEMP_DIR/lgpowercontrol-resume.service"

    sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" "$TEMP_DIR/lgpowercontrol-shutdown.service"
    sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" "$TEMP_DIR/lgpowercontrol-boot.service"
    # sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" "$TEMP_DIR/lgpowercontrol-resume.service"
    
    # Copy modified files to system path using sudo
    sudo cp "$TEMP_DIR/lgpowercontrol-shutdown.service" /etc/systemd/system/
    sudo cp "$TEMP_DIR/lgpowercontrol-boot.service" /etc/systemd/system/
    # sudo cp "$TEMP_DIR/lgpowercontrol-resume.service" /etc/systemd/system/

    # Enable services
    sudo systemctl daemon-reload
    sudo systemctl enable lgpowercontrol-boot.service
    sudo systemctl enable lgpowercontrol-shutdown.service
    # sudo systemctl enable lgpowercontrol-resume.service

    echo
    echo "✅ Systemd services enabled:"
    echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
    echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
    # echo "  - lgpowercontrol-resume.service (powers on TV after sleep)"
    echo
}

# Set up DBus event listener for screen lock/unlock
# Configures automatic TV power control based on screen lock events
dbus_setup() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔔 Optional: DBus Screen Lock Listener Setup"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "This script monitors screen lock/unlock events to automatically"
    echo "power your TV on/off."
    echo
    echo "⚠️  IMPORTANT NOTE ON PASSWORD:"
    echo "   This feature works best if unlocking your screen does NOT"
    echo "   require a password. If a password is required, the TV will"
    echo "   remain off until you successfully enter it, meaning you'll"
    echo "   need to type your password blindly."
    echo
    echo "   Fedora/ether-wake users: You will be prompted to add a"
    echo "   'sudoers' rule to allow Wake-on-LAN without a password."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -p "Would you like to install the DBus listener now? [Y/n] " answer
    answer=${answer:-Y}

    if [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]; then
        if cmd_exists ether-wake; then
            # Setup sudo rule for ether-wake (Fedora/dnf only)
            source "$SCRIPT_DIR/setup-sudo-etherwake.sh"
        fi
        
        # Attempt to auto-detect the desktop environment
        local DESKTOP_ENV="other"
        if [ -n "$XDG_CURRENT_DESKTOP" ]; then
            case "$XDG_CURRENT_DESKTOP" in
                *Cinnamon*|*CINNAMON*) 
                    DESKTOP_ENV="type='signal',interface='org.cinnamon.ScreenSaver',member='ActiveChanged',path='/org/cinnamon/ScreenSaver'"
                    ;;
                *KDE*|*Kde*) 
                    DESKTOP_ENV="type='signal',interface='org.freedesktop.ScreenSaver',member='ActiveChanged',path='/org/freedesktop/ScreenSaver'"
                    ;;
                *GNOME*|*Gnome*) 
                    DESKTOP_ENV="type='signal',interface='org.gnome.ScreenSaver',member='ActiveChanged',path='/org/gnome/ScreenSaver'"
                    ;;
            esac
        fi

        if [[ "$DESKTOP_ENV" != "other" ]]; then
            local autostart_dir="$HOME/.config/autostart"
            local listen_script="$INSTALL_PATH/lgpowercontrol-dbus-events.sh"
            local desktop_file="$autostart_dir/lgpowercontrol-dbus-events.desktop"

            echo "✅ Installing listener for $XDG_CURRENT_DESKTOP..."

            # Copy and substitute the listener script
            cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.sh" "$listen_script"
            sed -i "s|DESKTOP_ENV|$DESKTOP_ENV|g" "$listen_script"
            sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" "$listen_script"
            sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" "$listen_script"
            chmod +x "$listen_script"

            # Setup autostart desktop file
            mkdir -p "$autostart_dir"
            cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.desktop" "$desktop_file"
            sed -i "s|LISTEN_SCRIPT|$listen_script|g" "$desktop_file"
            
            # Start the listener in the background
            nohup "$listen_script" >/dev/null 2>&1 &
            echo "✅ DBus event listener installed and started."
        else
            echo "⚠️  $XDG_CURRENT_DESKTOP not supported. DBus event listener installation skipped."
        fi
    fi
}

# Perform TV authorization handshake
# Sends a test command to the TV requiring user acceptance
handshake() {
    if [ ! -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "🔐 TV Authorization Required"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "The TV requires a one-time authorization for this application."
        echo
        echo "Please be ready with your TV remote to ACCEPT the prompt that"
        echo "appears on your TV screen. If you do not accept, the power"
        echo "control features will not work."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        read -p "Press ENTER to send the test command"
        $LGCOMMAND button INFO >/dev/null 2>&1
        sleep 1
        
        if [ -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]; then
            echo "✅ Authorization complete!"
        else
            echo "❌ Authorization failed. Please run the installation again."
            exit 1
        fi
    fi
}

# Ensure cleanup happens on script exit, even if it fails
trap cleanup EXIT

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📺 LGPowerControl Installation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
ip_check
sudo_check
set_install_hint
check_req_tools
validate_ip
retrieve_mac
confirm_installation
install_bscpylgtv
select_hdmi_input
define_power_commands
systemd_setup
dbus_setup
handshake
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉 Installation complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Your TV will now automatically turn on at boot and off at shutdown."
echo "View logs anytime with: journalctl -t lgpowercontrol"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit 0
