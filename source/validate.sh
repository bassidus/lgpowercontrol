#!/bin/bash

# Validate IP address
function validate_ip() {
    local ip=$1
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        IFS='.' read -r o1 o2 o3 o4 <<<"$ip"
        if ((o1 > 255 || o2 > 255 || o3 > 255 || o4 > 255)); then
            echo "Error: IP $ip contains invalid numbers (must be 0-255)." >&2
            return 1
        fi
        echo "Checking if IP $ip is reachable..."
        if ! ping -c 1 -W 3 "$ip" >/dev/null 2>&1; then
            echo "Warning: IP $ip is not responding to ping (may be okay if TV is off)." >&2
        fi
    else
        echo "Error: IP $ip has invalid format (expected: xxx.xxx.xxx.xxx)." >&2
        return 1
    fi
    echo "IP $ip validated."
    return 0
}

# Validate MAC address
function validate_mac() {
    local mac=$1
    if [[ ! "$mac" =~ ^([a-fA-F0-9]{2}:){5}[a-fA-F0-9]{2}$ ]]; then
        echo "Error: MAC $mac is invalid (expected: XX:XX:XX:XX:XX:XX)." >&2
        return 1
    fi
    echo "MAC $mac validated."
    return 0
}

# Check for arp command and install net-tools if needed
function arp_check() {
    if ! command -v arp >/dev/null 2>&1; then
        echo "arp command not found, needed to find the TV's MAC address."
        read -p "Install net-tools now? [Y/n] " answer
        answer=${answer:-Y}
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            echo "Installing net-tools..."
            pacman -S --needed --noconfirm net-tools
            return 0
        fi
        echo "net-tools not installed. You must provide the MAC address."
        return 1
    fi
    echo "arp command is available."
    return 0
}

# Load IP and MAC from config.env
echo "Loading TV configuration from config.env..."
export $(grep -v '^#' config.env | xargs)
echo "TV IP: ${LGTV_IP:-Not set}"
echo "TV MAC: ${LGTV_MAC:-Not set}"

# Prompt for IP if not set
if [ ! "$LGTV_IP" ] || [ "$LGTV_IP" == "<your_tv_ip>" ]; then
    echo "No valid IP address in config.env."
    read -p "Enter the IP address of your LG TV (e.g., 192.168.1.100): " LGTV_IP
fi

# Validate IP
if ! validate_ip "$LGTV_IP"; then
    echo "Error: Failed to validate IP address." >&2
    exit 1
fi

# Prompt for MAC if not set or invalid
if [ ! "$LGTV_MAC" ] || [ "$LGTV_MAC" == "<your_tv_mac>" ]; then
    if arp_check; then
        echo "Attempting to retrieve MAC address for IP $LGTV_IP..."
        LGTV_MAC=$(arp -a "$LGTV_IP" | awk '{print $4}')
        if ! validate_mac "$LGTV_MAC"; then
            echo "Could not find a valid MAC address using arp."
            read -p "Enter the MAC address of your LG TV (e.g., 00:1A:2B:3C:4D:5E): " LGTV_MAC
            if ! validate_mac "$LGTV_MAC"; then
                echo "Error: Invalid MAC address." >&2
                exit 1
            fi
        fi
    else
        echo "No MAC address provided and arp is not available."
        read -p "Enter the MAC address of your LG TV (e.g., 00:1A:2B:3C:4D:5E): " LGTV_MAC
        if ! validate_mac "$LGTV_MAC"; then
            echo "Error: Invalid MAC address." >&2
            exit 1
        fi
    fi
fi

# Final MAC validation
if ! validate_mac "$LGTV_MAC"; then
    echo "Error: Invalid MAC address." >&2
    exit 1
fi

# Export validated IP and MAC for other scripts
export LGTV_IP
export LGTV_MAC