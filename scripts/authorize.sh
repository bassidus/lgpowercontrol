#!/bin/bash
# Verifies the TV pairing, triggering a new authorization dialog when the
# key is missing or invalid (e.g. after a factory reset). get_power_state
# both triggers the dialog and validates the key; a denied dialog leaves a
# broken key file behind, so remove it and retry.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }
[[ -r /opt/lgpowercontrol/lgpowercontrol.conf ]] \
    || { echo "LGPowerControl is not installed."; exit 1; }

source /opt/lgpowercontrol/lgpowercontrol.conf

[[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]] \
    || echo "TV Authorization - A dialog will appear on your TV screen - accept it with the remote."

until /opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand \
        -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" \
        get_power_state &> /dev/null; do
    rm -f /opt/lgpowercontrol/.aiopylgtv.sqlite
    echo "Authorization failed or was denied on the TV."
    read -r -p "Press Enter to show a new dialog on the TV (Ctrl+C to abort): "
done

echo "TV authorization OK!"
