#!/bin/bash
# Script: lg-tv-poweroff

# This script runs via NetworkManager dispatcher before the network is disabled.
# $1 = Interface Name
# $2 = Action (e.g., pre-down, down, up)

# Use NetworkManager's built-in variables for reliable context.
# INTERFACE="$1"
ACTION="$2"

if [[ "$ACTION" == "pre-down" ]]; then
    PWR_OFF_CMD
fi

exit 0