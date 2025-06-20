#!/bin/bash

# Check and install dependencies
function check_dependencies() {
    local deps_missing=0
    local deps_to_install=""
    
    if ! command -v wakeonlan >/dev/null 2>&1; then
        echo "wakeonlan not installed (needed to power on the TV)."
        deps_missing=1
        deps_to_install="$deps_to_install wakeonlan"
    fi
    
    if ! command -v pip >/dev/null 2>&1; then
        echo "python-pip not installed (needed to install bscpylgtv)."
        deps_missing=1
        deps_to_install="$deps_to_install python-pip"
    fi
    
    if [ $deps_missing -eq 1 ]; then
        read -p "Install missing dependencies ($deps_to_install)? [Y/n] " answer
        answer=${answer:-Y}
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            echo "Installing $deps_to_install..."
            pacman -S --needed --noconfirm $deps_to_install
        else
            echo "Error: Cannot proceed without dependencies." >&2
            exit 1
        fi
    fi
    echo "All dependencies installed."
}

echo "Checking dependencies..."
check_dependencies