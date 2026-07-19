#!/bin/bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }

source ./lgpowercontrol.conf

if [[ -z "${LGTV_IP:-}" ]]; then
    echo "LGTV_IP is not set. Edit lgpowercontrol.conf and enter your TV's IP address,"
    echo "then run the installer again."
    exit 1
fi

# Probe the WebOS API port.
timeout 2 bash -c "cat < /dev/null > /dev/tcp/$LGTV_IP/3001" 2> /dev/null \
    || { echo "$LGTV_IP is unreachable on port 3001. Make sure the TV is on. Aborting installation"; exit 1; }

# Debian/Ubuntu split venv out of the python3 package; installing is a no-op
# when already present, and apt resolves the right versioned package.
command -v apt &> /dev/null && apt-get install -y python3-venv

if [[ -z "$LGTV_MAC" ]]; then
    LGTV_MAC=$(ip neigh show "$LGTV_IP" | grep -m1 -ioE '([0-9a-f]{2}:){5}[0-9a-f]{2}') \
        || { echo "Could not detect MAC for $LGTV_IP. Set LGTV_MAC in lgpowercontrol.conf"; exit 1; }
    echo "Detected TV MAC address: $LGTV_MAC"
fi

# Preserve the TV pairing database across reinstalls and updates.
keydb=""
if [[ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]]; then
    keydb=$(mktemp)
    cp /opt/lgpowercontrol/.aiopylgtv.sqlite "$keydb"
fi

./uninstall.sh --quiet # Fresh start: remove any existing installation and legacy leftovers.

# Creates /opt/lgpowercontrol too. On failure, python prints the actual error
# and set -e aborts the install.
python3 -m venv /opt/lgpowercontrol/bscpylgtv
/opt/lgpowercontrol/bscpylgtv/bin/pip install --quiet bscpylgtv
# pip is only needed during install; removing it shrinks the venv from ~15 MB to ~2 MB.
/opt/lgpowercontrol/bscpylgtv/bin/pip uninstall --quiet -y pip

# Restore the TV pairing database
[[ -n "$keydb" ]] && mv "$keydb" /opt/lgpowercontrol/.aiopylgtv.sqlite

cp -v ./VERSION                                 /opt/lgpowercontrol/
cp -v ./lgpowercontrol.conf                     /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol                  /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-monitor.sh       /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-notify.sh        /opt/lgpowercontrol/
cp -v ./scripts/lgpowercontrol-update-check.sh  /opt/lgpowercontrol/
cp -v ./scripts/update.sh                       /opt/lgpowercontrol/
cp -v ./scripts/authorize.sh                    /opt/lgpowercontrol/
cp -v ./scripts/lgpc-wol.py                     /opt/lgpowercontrol/
cp -v ./systemd/lgpowercontrol-notify.service       /etc/systemd/user/
cp -v ./systemd/lgpowercontrol-update-check.service /etc/systemd/user/
cp -v ./systemd/lgpowercontrol-update-check.timer   /etc/systemd/user/
cp -v ./systemd/lgpowercontrol-shutdown.service /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-boot.service     /etc/systemd/system/
cp -v ./systemd/lgpowercontrol-monitor.service  /etc/systemd/system/

if [[ -d /etc/NetworkManager/dispatcher.d ]]; then
    mkdir -p /etc/NetworkManager/dispatcher.d/pre-down.d
    cp -v ./scripts/90-lgpowercontrol /etc/NetworkManager/dispatcher.d/
    chmod 755 /etc/NetworkManager/dispatcher.d/90-lgpowercontrol
    ln -sfv ../90-lgpowercontrol /etc/NetworkManager/dispatcher.d/pre-down.d/90-lgpowercontrol
fi

mkdir -p /usr/lib/systemd/system-sleep
cp -v ./scripts/lgpowercontrol-sleep /usr/lib/systemd/system-sleep/lgpowercontrol
chmod 755 /usr/lib/systemd/system-sleep/lgpowercontrol

sed -i "s|^LGTV_MAC=\"\"|LGTV_MAC=\"$LGTV_MAC\"|" /opt/lgpowercontrol/lgpowercontrol.conf

chmod +x /opt/lgpowercontrol/{lgpowercontrol,lgpowercontrol-monitor.sh,lgpowercontrol-notify.sh,lgpowercontrol-update-check.sh,update.sh,authorize.sh,lgpc-wol.py}

systemctl daemon-reload
systemctl enable lgpowercontrol-boot.service lgpowercontrol-shutdown.service
systemctl enable --now lgpowercontrol-monitor.service

# The notify service must run inside the desktop session, so it's a user unit.
# The update-check timer is also per-user (the notification needs the user's
# D-Bus session) but runs independent of the desktop session's lifetime.
systemctl --global enable lgpowercontrol-notify.service
systemctl --global enable lgpowercontrol-update-check.timer
if [[ -n "${SUDO_USER:-}" ]]; then
    systemctl --machine="${SUDO_USER}@" --user daemon-reload 2> /dev/null || true
    systemctl --machine="${SUDO_USER}@" --user start lgpowercontrol-notify.service 2> /dev/null || true
    systemctl --machine="${SUDO_USER}@" --user start lgpowercontrol-update-check.timer 2> /dev/null || true
fi

echo

/opt/lgpowercontrol/authorize.sh

echo; echo "Installation complete!"
