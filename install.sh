#!/bin/bash
# LGPowerControl installer
# Usage: ./install.sh [TV_IP_ADDRESS]

[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo." >&2; exit 1; }
[[ -n "${1:-}" ]] || { echo "Usage: $0 TV_IP_ADDRESS" >&2; exit 1; }

pacman -S --noconfirm --needed iproute2 wakeonlan

LGTV_IP="$1"
ping -c 1 -W 2 $LGTV_IP &>/dev/null
LGTV_MAC=$(ip neigh show "$LGTV_IP" 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')
WOL_CMD="$(command -v wakeonlan) -i $LGTV_IP $LGTV_MAC"
BOOT_SHUTDOWN_MODE=power
MONITOR_MODE=screen

mkdir -p /opt/lgpowercontrol
python3 -m venv /opt/lgpowercontrol/bscpylgtv &&
/opt/lgpowercontrol/bscpylgtv/bin/pip install --upgrade pip &&
/opt/lgpowercontrol/bscpylgtv/bin/pip install bscpylgtv

cat > /opt/lgpowercontrol/lgpowercontrol.conf << EOF
# LGPowerControl configuration

# After editing, restart the monitor service to apply changes:
#   sudo systemctl restart lgpowercontrol-monitor.service

# --- Remote Interface Settings ------------------------------------------------

LGTV_IP=$LGTV_IP
LGTV_MAC=$LGTV_MAC
WOL_CMD=($WOL_CMD)
HDMI_INPUT=         # e.g. HDMI_1, HDMI_2 ... or empty to disable

# --- Behavior -----------------------------------------------------------------

# 'power'  - Full power off. Maximum energy savings; TV takes a few seconds to turn on.
# 'screen' - Screen off only. Wakes instantly; uses slightly more power while idle. [Default]

BOOT_SHUTDOWN_MODE=$BOOT_SHUTDOWN_MODE
POWER_MODE=$MONITOR_MODE
EOF

cp ./scripts/lgpowercontrol                     /opt/lgpowercontrol/
cp ./scripts/lgpowercontrol-monitor.sh          /opt/lgpowercontrol/
cp ./systemd/lgpowercontrol-shutdown.service    /etc/systemd/system/
cp ./systemd/lgpowercontrol-boot.service        /etc/systemd/system/
cp ./systemd/lgpowercontrol-monitor.service     /etc/systemd/system/

systemctl daemon-reload
systemctl enable lgpowercontrol-boot.service
systemctl enable lgpowercontrol-shutdown.service
systemctl enable lgpowercontrol-monitor.service

if [[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]]; then
    rm /opt/lgpowercontrol/.aiopylgtv.sqlite
fi
echo "TV Authorization"
echo "A dialog will appear on your TV screen — accept it with the remote."
read -r -p "Press Enter to trigger the authorization dialog on your TV: "
/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" get_power_state
