#!/bin/bash
set -euo pipefail
source ./lgpowercontrol.conf
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }

if [[ -z "${LGTV_IP:-}" ]]; then
    echo "LGTV_IP is not set. Edit lgpowercontrol.conf and enter your TV's IP address,"
    echo "then run the installer again."
    exit 1
fi
ping -c 1 -W 1 "$LGTV_IP" &> /dev/null || { echo "$LGTV_IP is unreachable. Make sure the TV is on. Aborting installation"; exit 1; }

if   command -v pacman &> /dev/null; then pkg() { pacman -S --needed "$@"; }; py_pkg=python;  wol_pkg=wakeonlan
elif command -v apt    &> /dev/null; then pkg() { apt install -y "$@"; };     py_pkg=python3; wol_pkg=wakeonlan
elif command -v dnf    &> /dev/null; then pkg() { dnf install -y "$@"; };     py_pkg=python3; wol_pkg=net-tools # provides ether-wake
else echo "No supported package manager found (pacman/apt/dnf). Aborting installation"; exit 1
fi

command -v python3 &> /dev/null || pkg "$py_pkg"
command -v wakeonlan &> /dev/null || command -v ether-wake &> /dev/null || pkg "$wol_pkg"
command -v apt &> /dev/null && pkg python3-venv

if [[ -z "$LGTV_MAC" ]]; then
    LGTV_MAC=$(ip neigh show "$LGTV_IP" | grep -m1 -ioE '([0-9a-f]{2}:){5}[0-9a-f]{2}') \
        || { echo "Could not detect MAC for $LGTV_IP. Set LGTV_MAC in lgpowercontrol.conf"; exit 1; }
    echo "Detected TV MAC address: $LGTV_MAC"
fi


./uninstall.sh --quiet # Fresh start: remove any existing installation and legacy leftovers.

python3 -m venv /opt/lgpowercontrol/bscpylgtv
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet bscpylgtv
/opt/lgpowercontrol/bscpylgtv/bin/pip uninstall --quiet -y pip

cp -v ./lgpowercontrol.conf                     /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol                  /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-monitor.sh       /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-notify.sh        /opt/lgpowercontrol/
cp -v ./systemd/lgpowercontrol-notify.service   /etc/systemd/user/
cp -v ./systemd/lgpowercontrol-shutdown.service /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-boot.service     /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-monitor.service  /etc/systemd/system/

if [[ -d /etc/NetworkManager/dispatcher.d ]]; then
    mkdir -p /etc/NetworkManager/dispatcher.d/pre-down.d
    cp -v ./scripts/90-lgpowercontrol /etc/NetworkManager/dispatcher.d/
    chmod 755 /etc/NetworkManager/dispatcher.d/90-lgpowercontrol
    ln -sfv ../90-lgpowercontrol /etc/NetworkManager/dispatcher.d/pre-down.d/90-lgpowercontrol
fi

sed -i "s|^LGTV_MAC=.*|LGTV_MAC=\"$LGTV_MAC\"|" /opt/lgpowercontrol/lgpowercontrol.conf

chmod +x /opt/lgpowercontrol/{lgpowercontrol,lgpowercontrol-monitor.sh,lgpowercontrol-notify.sh}

systemctl daemon-reload
systemctl enable lgpowercontrol-boot.service lgpowercontrol-shutdown.service
systemctl enable --now lgpowercontrol-monitor.service
systemctl --global enable lgpowercontrol-notify.service
if [[ -n "${SUDO_USER:-}" ]]; then
    systemctl --machine="${SUDO_USER}@" --user daemon-reload 2> /dev/null || true
    systemctl --machine="${SUDO_USER}@" --user start lgpowercontrol-notify.service 2> /dev/null || true
fi

echo "TV Authorization - A dialog will appear on your TV screen - accept it with the remote."
read -r -p "Press Enter to trigger the authorization dialog on your TV: "
/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand \
    -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" \
    get_power_state &> /dev/null

echo "Installation complete!"
