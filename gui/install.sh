#!/bin/bash
# Builds the settings window and installs it for the user who runs this - no root, by default.
#
# That is not a shortcut, it is what this program is: the conf it edits is handed to a single user
# by install.py (0644, owned by whoever ran the installer), so a system-wide copy would give every
# other local user a window that can only read. The repo's own install.py is the one that needs
# root, because it installs a service tree that root executes at boot, at suspend and from a timer.
#
# PREFIX=/usr/local ./install.sh still works, and then sudo is used - but only because that target
# needs it, not because installing does.
set -euo pipefail
cd "$(dirname "$0")"

PREFIX="${PREFIX:-$HOME/.local}"
BUILD_DIR="build"

usage() {
    cat <<EOF
Usage: ./install.sh [--uninstall]

Builds gui/ and installs it to \$PREFIX (default \$HOME/.local, no root needed). Needs cmake and
the Qt 6 Widgets development package; Qt 6 itself is already there on any KDE Plasma desktop.

  Arch/CachyOS   sudo pacman -S --needed cmake qt6-base
  Debian/Ubuntu  sudo apt install cmake qt6-base-dev
  Fedora         sudo dnf install cmake qt6-qtbase-devel
  openSUSE       sudo zypper install cmake qt6-base-devel
EOF
}

# sudo only when the destination actually refuses the user, so the default path never asks.
if mkdir -p "$PREFIX/bin" "$PREFIX/share/applications" 2>/dev/null \
        && [ -w "$PREFIX/bin" ] && [ -w "$PREFIX/share/applications" ]; then
    SUDO=""
else
    SUDO="sudo"
fi

uninstall() {
    $SUDO rm -f "$PREFIX/bin/lgpowercontrol-gui"
    $SUDO rm -f "$PREFIX/share/applications/lgpowercontrol-gui.desktop"
    $SUDO update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
    echo "Removed lgpowercontrol-gui from $PREFIX."
    echo "The configuration itself is untouched - ./install.py --uninstall in the repo root"
    echo "is what removes LGPowerControl."
}

case "${1:-}" in
    --uninstall) uninstall; exit 0 ;;
    -h|--help)   usage; exit 0 ;;
    "")          ;;
    *)           usage; exit 1 ;;
esac

cmake -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR"
$SUDO cmake --install "$BUILD_DIR" --prefix "$PREFIX"
# Only some desktops need the cache nudged, and it is absent on others - never a reason to fail.
$SUDO update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true

echo
echo "Installed. Start it from the menu as \"LGPowerControl Settings\", or run lgpowercontrol-gui."

# The name has to resolve on PATH, and not only for convenience: xdg-desktop-portal checks that the
# running binary is the one the .desktop file's Exec names before it accepts the app id, which is
# what gives the window its icon in the task manager. See the note in README.md.
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo
       echo "Note: $PREFIX/bin is not on your PATH. Add it, or the menu entry and the window's"
       echo "icon will not work. In ~/.bashrc or ~/.zshrc:  export PATH=\"$PREFIX/bin:\$PATH\"" ;;
esac
