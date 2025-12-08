#!/bin/bash
#
# Setup Sudo Rule for ether-wake
# Configures passwordless sudo access for ether-wake command

set -e

echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔐 ether-wake Sudo Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "'ether-wake' requires elevated privileges (sudo) to run."
echo "To allow your TV to power on automatically, the script can configure"
echo "a rule so that 'ether-wake' can be run without a password prompt."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
read -p "Would you like to set this up now? [Y/n] " answer
answer=${answer:-Y}

echo

if [[ "$answer" =~ ^([Yy]|[Yy][Ee][Ss])$ ]]; then
    SUDOERS_LINE="$USER ALL=(ALL) NOPASSWD: $(command -v ether-wake)"
    TEMP_FILE=$(mktemp)

    echo "$SUDOERS_LINE" > "$TEMP_FILE"

    # Validate with visudo in check mode
    if sudo visudo -c -f "$TEMP_FILE"; then
        sudo cp "$TEMP_FILE" /etc/sudoers.d/lgpowercontrol-etherwake
        sudo chmod 0440 /etc/sudoers.d/lgpowercontrol-etherwake
        echo "✅ Done: 'sudo ether-wake' can now be used without a password."
    else
        echo "❌ Error: Sudoers rule is invalid. Aborting."
    fi

    rm -f "$TEMP_FILE"
else
    echo "⚠️  Important: Automatic TV wake will not work until you allow"
    echo "   'ether-wake' via sudoers."
fi
