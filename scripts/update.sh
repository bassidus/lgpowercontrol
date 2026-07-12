#!/bin/bash
# Compares the installed version with the latest GitHub release and offers
# to download and install it. Settings and TV pairing survive the update.
set -euo pipefail

repo="bassidus/lgpowercontrol"

[[ $EUID -eq 0 ]] || { echo "This script needs to be run as root or with sudo."; exit 1; }

fetch() {
    if command -v curl &> /dev/null; then
        curl -fsSL "$1"
    elif command -v wget &> /dev/null; then
        wget -qO- "$1"
    else
        echo "curl or wget is required to check for updates." >&2
        exit 1
    fi
}

installed="none"
[[ -r /opt/lgpowercontrol/VERSION ]] && installed=$(< /opt/lgpowercontrol/VERSION)

tag=$(fetch "https://api.github.com/repos/${repo}/releases/latest" |
    grep -m1 '"tag_name"' | cut -d'"' -f4)
[[ -n "$tag" ]] || { echo "Could not determine the latest release. Aborting."; exit 1; }
latest="${tag#v}"

echo "Installed version: ${installed}"
echo "Latest release:    ${latest}"

[[ "$installed" == "$latest" ]] && { echo "Already up to date."; exit 0; }

read -r -p "Update to ${latest}? [y/N] " answer
[[ "$answer" == [yY]* ]] || exit 0

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fetch "https://github.com/${repo}/archive/refs/tags/${tag}.tar.gz" | tar -xz -C "$tmp"

# Keep the current settings; options added in newer versions fall back to
# their defaults.
cp /opt/lgpowercontrol/lgpowercontrol.conf "$tmp"/*/

cd "$tmp"/*/
./install.sh
