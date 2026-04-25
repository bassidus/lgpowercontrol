#!/bin/bash
# Shared helpers for install.sh and uninstall.sh — sourced, not executed.

# shellcheck disable=SC2034  # CYN used by install.sh
RST=$'\033[0m' RED=$'\033[0;31m' GRN=$'\033[0;32m' YEL=$'\033[0;33m' BLU=$'\033[0;94m' CYN=$'\033[0;36m'

die()     { echo "${RED}Error: $1${RST}" >&2; exit 1; }
info()    { echo "${BLU}$1${RST}"; }
ok()      { echo " ${GRN}[OK]${RST}"; }
sep()     { echo "${BLU}----------------------------------------------------------------${RST}"; }
has()     { command -v "$1" >/dev/null 2>&1; }
confirm() { local a; read -r -p "$1 [Y/n] " a; echo; [[ "${a:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]; }

# Removes artefacts from previous installs to avoid duplicate processes/services.
cleanup_legacy() {
    local cleaned=false

    for _svc in lgtv-power-on-at-boot.service lgtv-power-off-at-shutdown.service \
                lgtv-btw-boot.service lgtv-btw-shutdown.service \
                lgpowercontrol-sleep.service lgpowercontrol-resume.service; do
        [[ -f "/etc/systemd/system/$_svc" ]] || continue
        echo "${YEL}Removing legacy service: $_svc${RST}"
        systemctl stop    "$_svc" 2>/dev/null || true
        systemctl disable "$_svc" 2>/dev/null || true
        rm -f "/etc/systemd/system/$_svc"
        cleaned=true
    done

    for _df in "$HOME/.config/autostart/lgpowercontrol-dbus-events.desktop" \
               "$HOME/.config/autostart/lgpowercontrol-monitor.desktop"; do
        [[ -f "$_df" ]] || continue
        echo "${YEL}Removing legacy autostart entry: $(basename "$_df")${RST}"
        rm -f "$_df"; cleaned=true
    done

    for _old_dir in "$HOME/.local/lgtv-btw" "$HOME/.local/lgpowercontrol"; do
        [[ -d "$_old_dir" ]] || continue
        echo "${YEL}Removing legacy install directory: $_old_dir${RST}"
        rm -rf "$_old_dir"; cleaned=true
    done

    for _f in /etc/sudoers.d/lgpowercontrol-etherwake \
              /usr/local/bin/bscpylgtvcommand \
              /opt/lgpowercontrol/lgpowercontrol-dbus-events.sh; do
        [[ -f "$_f" ]] || continue
        echo "${YEL}Removing legacy: $_f${RST}"
        rm -f "$_f"; cleaned=true
    done

    if $cleaned; then
        systemctl daemon-reload 2>/dev/null || true
        echo "${GRN}Legacy files cleaned up.${RST}"
    fi
}
