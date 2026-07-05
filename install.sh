#!/bin/bash
set -euo pipefail
source ./lgpowercontrol.conf
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }
ping -c 1 -W 1 "$LGTV_IP" &> /dev/null || { echo "$LGTV_IP is unreachable. Aborting installation"; exit 1; }

command -v pacman    &> /dev/null || { echo "pacman not found. Aborting installation"; exit 1; }
command -v python3   &> /dev/null || pacman -S --needed python
command -v wakeonlan &> /dev/null || pacman -S --needed wakeonlan

mkdir -p /opt/lgpowercontrol
python3 -m venv /opt/lgpowercontrol/bscpylgtv
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet --upgrade pip
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet bscpylgtv

cp -v ./lgpowercontrol.conf                     /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol                  /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-monitor.sh       /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-notify.sh        /opt/lgpowercontrol/
cp -v ./systemd/lgpowercontrol-notify.service   /etc/systemd/user/
cp -v ./systemd/lgpowercontrol-shutdown.service /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-boot.service     /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-monitor.service  /etc/systemd/system/

chmod +x /opt/lgpowercontrol/lgpowercontrol
chmod +x /opt/lgpowercontrol/lgpowercontrol-monitor.sh
chmod +x /opt/lgpowercontrol/lgpowercontrol-notify.sh

systemctl daemon-reload
systemctl enable lgpowercontrol-boot.service lgpowercontrol-shutdown.service lgpowercontrol-monitor.service
systemctl restart lgpowercontrol-monitor.service # applies updates if already running

# The notify service must run inside the desktop session, so it's a user unit.
# The --machine calls fail harmlessly when there is no desktop session (e.g. SSH).
systemctl --global enable lgpowercontrol-notify.service
if [[ -n "${SUDO_USER:-}" ]]; then
    systemctl --machine="${SUDO_USER}@" --user daemon-reload 2> /dev/null || true
    systemctl --machine="${SUDO_USER}@" --user restart lgpowercontrol-notify.service 2> /dev/null || true
fi

rm -f /opt/lgpowercontrol/.aiopylgtv.sqlite # remove old database so the TV re-prompts for authorization.
echo "TV Authorization - A dialog will appear on your TV screen - accept it with the remote."
read -r -p "Press Enter to trigger the authorization dialog on your TV: "
/opt/lgpowercontrol/bscpylgtv/bin/bscpylgtvcommand \
    -p /opt/lgpowercontrol/.aiopylgtv.sqlite "$LGTV_IP" \
    get_power_state &> /dev/null

echo "Installation complete!"