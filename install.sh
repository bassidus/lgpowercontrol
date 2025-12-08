#!/bin/bash
#
# LGPowerControl Installation Script
# Prepares and installs LG TV control setup by validating dependencies,
# checking network connectivity, and retrieving the TV's MAC address.
# Usage: ./install.sh <TV_IP_ADDRESS>

set -e # Exit immediately if a command exits with a non-zero status

# Color codes for output messages
readonly COLOR_RESET='\033[0m'
readonly COLOR_RED='\033[0;31m'
readonly COLOR_GREEN='\033[0;32m'
readonly COLOR_YELLOW='\033[0;33m'
readonly COLOR_BLUE='\033[0;34m'
readonly COLOR_CYAN='\033[0;36m'

# Global constants
readonly LGTV_IP=$1
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly INSTALL_PATH="$HOME/.local/lgpowercontrol"
readonly LGCOMMAND="$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand -p $INSTALL_PATH/.aiopylgtv.sqlite $LGTV_IP"
readonly TEMP_DIR=$(mktemp -d)

# Global variables (set during installation)
LGTV_MAC=""
INSTALL_HINT=""
HDMI_INPUT=""
WOL_CMD=""
PWR_OFF_CMD=""
PWR_ON_CMD=""

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

    echo -ne "${COLOR_CYAN}Checking for $cmd ...${COLOR_RESET}"
    if ! cmd_exists "$cmd"; then
        echo
        echo -e "${COLOR_RED}Error: The '$pkg' package is not installed.${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}   Install it $INSTALL_HINT $pkg${COLOR_RESET}"
        exit 1
    fi
    echo -e " ${COLOR_GREEN}[OK]${COLOR_RESET}"
}

# Validate that TV IP address is provided
# Exits with error message if no IP address is given
check_ip_provided() {
    if [ -z "$LGTV_IP" ]; then
        echo
        echo -e "${COLOR_RED}Error: No IP address provided.${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}   Usage: ./install.sh <TV_IP_ADDRESS>${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}   Example: ./install.sh 192.168.1.100${COLOR_RESET}"
        echo
        echo -e "${COLOR_BLUE}Tip: You can usually find your TV's IP address in its network${COLOR_RESET}"
        echo -e "${COLOR_BLUE}   settings or through your router's web interface.${COLOR_RESET}"
        exit 1
    fi
}

# Ensure the script is not run with root privileges
# Exits with error if run as root or with sudo
check_not_root() {
    if [[ $EUID -eq 0 ]]; then
        echo -e "${COLOR_YELLOW}Warning: This script must NOT be run as root or with sudo.${COLOR_RESET}" 1>&2
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
        echo -e "${COLOR_RED}Error: '$LGTV_IP' is not a valid IPv4 address${COLOR_RESET}"
        exit 1
    fi

    # Check if IP is reachable
    echo -ne "${COLOR_CYAN}Verifying IP $LGTV_IP is reachable ...${COLOR_RESET}"
    if ! ping -c 1 -W 1 "$LGTV_IP" >/dev/null 2>&1; then
        echo
        echo -e "${COLOR_RED}Error: $LGTV_IP is unreachable${COLOR_RESET}"
        exit 1
    fi
    echo -e " ${COLOR_GREEN}[OK]${COLOR_RESET}"
}

# Retrieve TV MAC address from ARP table
# Uses 'ip neigh' to automatically detect the MAC address
retrieve_mac() {
    echo -ne "${COLOR_CYAN}Retrieving MAC address... ${COLOR_RESET}"

    LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' || true)

    if [[ ! "$LGTV_MAC" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        echo
        echo -e "${COLOR_RED}Error: Could not detect a valid MAC address for $LGTV_IP.${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}Action Required: Please ensure the TV is **ON** and reachable${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}   (e.g., ping it) to populate the ARP table.${COLOR_RESET}"
        echo -e "${COLOR_BLUE}   Try manually checking with: ip neigh show $LGTV_IP${COLOR_RESET}"
        exit 1
    fi

    echo -e "$LGTV_MAC ${COLOR_GREEN}[OK]${COLOR_RESET}"
}

# Install bscpylgtv Python library in virtual environment
# Creates a venv and installs bscpylgtv if not already present
install_bscpylgtv() {
    if [ ! -f "$INSTALL_PATH/bscpylgtv/bin/bscpylgtvcommand" ]; then
        echo -e "${COLOR_BLUE}Installing bscpylgtv into local Python Virtual Environment...${COLOR_RESET}"
        mkdir -p "$INSTALL_PATH"
        python3 -m venv "$INSTALL_PATH/bscpylgtv"
        "$INSTALL_PATH/bscpylgtv/bin/pip" install --upgrade pip

        if ! "$INSTALL_PATH/bscpylgtv/bin/pip" install bscpylgtv; then
            echo -e "${COLOR_RED}ERROR: Failed to install bscpylgtv.${COLOR_RESET}" >&2
            exit 1
        fi
        echo -e "${COLOR_GREEN}bscpylgtv installed successfully.${COLOR_RESET}"
    else
        echo -e "${COLOR_GREEN}bscpylgtv already installed in $INSTALL_PATH. Skipping installation.${COLOR_RESET}"
    fi
}

# Prompt user to select HDMI input port
# Configures automatic HDMI input switching on TV power-on
select_hdmi_input() {
    local hdmi_choice
    
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${COLOR_BLUE}HDMI Input Selection (Optional)${COLOR_RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Select which HDMI port the computer is connected to."
    echo "The TV will automatically switch to this input when powered on."
    echo "Leave empty to skip."
    echo
    read -r -p "Enter number (1-5): " hdmi_choice

    if [ -n "$hdmi_choice" ]; then
        # Validate input is a single digit 1-5
        if [[ "$hdmi_choice" =~ ^[1-5]$ ]]; then
             local hdmi_input="HDMI_$hdmi_choice"
             
             echo -e "${COLOR_GREEN}Configuring automatic switch to $hdmi_input.${COLOR_RESET}"
             
             # Set the global variable
             HDMI_INPUT="$hdmi_input"
        else
             echo -e "${COLOR_YELLOW}Invalid input '$hdmi_choice'. Skipping HDMI input configuration.${COLOR_RESET}"
        fi
    else
        echo -e "${COLOR_BLUE}Skipping HDMI input configuration.${COLOR_RESET}"
    fi
}

# Define power control commands and prepare scripts
# Sets up Wake-on-LAN and power commands, configures lgpowercontrol script
define_power_commands() {
    local wol
    
    PWR_OFF_CMD="$INSTALL_PATH/lgpowercontrol OFF"
    PWR_ON_CMD="$INSTALL_PATH/lgpowercontrol ON"

    if cmd_exists wakeonlan; then
        wol=$(command -v wakeonlan)
        WOL_CMD="$wol -i $LGTV_IP $LGTV_MAC"
    elif cmd_exists ether-wake; then
        wol=$(command -v ether-wake)
        WOL_CMD="sudo $wol $LGTV_MAC"
    else
        echo -e "${COLOR_RED}Error: Neither 'wakeonlan' nor 'ether-wake' is installed. Cannot continue.${COLOR_RESET}" >&2
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
    local answer
    
    echo -e "${COLOR_BLUE}Installation path: $INSTALL_PATH${COLOR_RESET}"
    echo
    read -r -p "All dependencies met. Confirm installation? [Y/n] " answer
    answer=${answer:-Y}
    echo
    if ! [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]; then
        echo -e "${COLOR_YELLOW}Installation aborted by user, no changes were made.${COLOR_RESET}"
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
setup_systemd_services() {
    echo -e "${COLOR_BLUE}Setting up Systemd services...${COLOR_RESET}"
    
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
    echo -e "${COLOR_GREEN}Systemd services enabled:${COLOR_RESET}"
    echo "   • lgpowercontrol-boot.service (powers on TV at boot)"
    echo "   • lgpowercontrol-shutdown.service (powers off TV at shutdown)"
    # echo "  - lgpowercontrol-resume.service (powers on TV after sleep)"
    echo
}

# Setup Sudo Rule for ether-wake
# Configures passwordless sudo access for ether-wake command
setup_sudo_etherwake() {
    local answer
    local sudoers_line
    local temp_file
    
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${COLOR_BLUE}ether-wake Sudo Configuration${COLOR_RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "'ether-wake' requires elevated privileges (sudo) to run."
    echo "To allow your TV to power on automatically, the script can configure"
    echo "a rule so that 'ether-wake' can be run without a password prompt."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -r -p "Would you like to set this up now? [Y/n] " answer
    answer=${answer:-Y}

    echo

    if [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]; then
        sudoers_line="$USER ALL=(ALL) NOPASSWD: $(command -v ether-wake)"
        temp_file=$(mktemp)

        echo "$sudoers_line" > "$temp_file"

        # Validate with visudo in check mode
        if sudo visudo -c -f "$temp_file"; then
            sudo cp "$temp_file" /etc/sudoers.d/lgpowercontrol-etherwake
            sudo chmod 0440 /etc/sudoers.d/lgpowercontrol-etherwake
            echo -e "${COLOR_GREEN}Done: 'sudo ether-wake' can now be used without a password.${COLOR_RESET}"
        else
            echo -e "${COLOR_RED}Error: Sudoers rule is invalid. Aborting.${COLOR_RESET}"
        fi

        rm -f "$temp_file"
    else
        echo -e "${COLOR_YELLOW}Important: Automatic TV wake will not work until you allow${COLOR_RESET}"
        echo -e "${COLOR_YELLOW}   'ether-wake' via sudoers.${COLOR_RESET}"
    fi
}

# Set up DBus event listener for screen lock/unlock
# Configures automatic TV power control based on screen lock events
setup_dbus_listener() {
    local answer
    local desktop_env="other"
    local autostart_dir
    local listen_script
    local desktop_file
    
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${COLOR_BLUE}Optional: DBus Screen Lock Listener Setup${COLOR_RESET}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "This script monitors screen lock/unlock events to automatically"
    echo "power your TV on/off."
    echo
    echo -e "${COLOR_YELLOW}IMPORTANT NOTE ON PASSWORD:${COLOR_RESET}"
    echo "   This feature works best if unlocking your screen does NOT"
    echo "   require a password. If a password is required, the TV will"
    echo "   remain off until you successfully enter it, meaning you'll"
    echo "   need to type your password blindly."
    echo
    echo "   Fedora/ether-wake users: You will be prompted to add a"
    echo "   'sudoers' rule to allow Wake-on-LAN without a password."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -r -p "Would you like to install the DBus listener now? [Y/n] " answer
    answer=${answer:-Y}

    if [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]; then
        if cmd_exists dnf && cmd_exists ether-wake; then
            # Setup sudo rule for ether-wake (Fedora/dnf only)
            setup_sudo_etherwake
        fi
        
        # Attempt to auto-detect the desktop environment
        if [ -n "$XDG_CURRENT_DESKTOP" ]; then
            case "$XDG_CURRENT_DESKTOP" in
                *Cinnamon*|*CINNAMON*) 
                    desktop_env="type='signal',interface='org.cinnamon.ScreenSaver',member='ActiveChanged',path='/org/cinnamon/ScreenSaver'"
                    ;;
                *KDE*|*Kde*) 
                    desktop_env="type='signal',interface='org.freedesktop.ScreenSaver',member='ActiveChanged',path='/org/freedesktop/ScreenSaver'"
                    ;;
                *GNOME*|*Gnome*) 
                    desktop_env="type='signal',interface='org.gnome.ScreenSaver',member='ActiveChanged',path='/org/gnome/ScreenSaver'"
                    ;;
            esac
        fi

        if [[ "$desktop_env" != "other" ]]; then
            autostart_dir="$HOME/.config/autostart"
            listen_script="$INSTALL_PATH/lgpowercontrol-dbus-events.sh"
            desktop_file="$autostart_dir/lgpowercontrol-dbus-events.desktop"

            echo -e "${COLOR_GREEN}Installing listener for $XDG_CURRENT_DESKTOP...${COLOR_RESET}"

            # Copy and substitute the listener script
            cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.sh" "$listen_script"
            sed -i "s|DESKTOP_ENV|$desktop_env|g" "$listen_script"
            sed -i "s|PWR_OFF_CMD|$PWR_OFF_CMD|g" "$listen_script"
            sed -i "s|PWR_ON_CMD|$PWR_ON_CMD|g" "$listen_script"
            chmod +x "$listen_script"

            # Setup autostart desktop file
            mkdir -p "$autostart_dir"
            cp "$SCRIPT_DIR/lgpowercontrol-dbus-events.desktop" "$desktop_file"
            sed -i "s|LISTEN_SCRIPT|$listen_script|g" "$desktop_file"
            
            # Start the listener in the background
            nohup "$listen_script" >/dev/null 2>&1 &
            echo -e "${COLOR_GREEN}DBus event listener installed and started.${COLOR_RESET}"
        else
            echo -e "${COLOR_YELLOW}$XDG_CURRENT_DESKTOP not supported. DBus event listener installation skipped.${COLOR_RESET}"
        fi
    fi
}

# Perform TV authorization handshake
# Sends a test command to the TV requiring user acceptance
perform_tv_handshake() {
    if [ ! -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo -e "${COLOR_BLUE}TV Authorization Required${COLOR_RESET}"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "The TV requires a one-time authorization for this application."
        echo
        echo "Please be ready with your TV remote to ACCEPT the prompt that"
        echo "appears on your TV screen. If you do not accept, the power"
        echo "control features will not work."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        read -r -p "Press ENTER to send the test command"
        $LGCOMMAND button INFO >/dev/null 2>&1
        sleep 1
        
        if [ -f "$INSTALL_PATH/.aiopylgtv.sqlite" ]; then
            echo -e "${COLOR_GREEN}Authorization complete!${COLOR_RESET}"
        else
            echo -e "${COLOR_RED}Authorization failed. Please run the installation again.${COLOR_RESET}"
            exit 1
        fi
    fi
}

# Ensure cleanup happens on script exit, even if it fails
trap cleanup EXIT

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${COLOR_BLUE}LGPowerControl Installation${COLOR_RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

check_ip_provided
check_not_root
set_install_hint
check_req_tools
validate_ip
retrieve_mac

echo

confirm_installation
install_bscpylgtv
select_hdmi_input
define_power_commands
setup_systemd_services
setup_dbus_listener
perform_tv_handshake

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${COLOR_GREEN}Installation complete!${COLOR_RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Your TV will now automatically turn on at boot and off at shutdown."
echo -e "${COLOR_BLUE}View logs anytime with: journalctl -t lgpowercontrol${COLOR_RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit 0
