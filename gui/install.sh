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
PREBUILT="prebuilt/lgpowercontrol-gui"

usage() {
    cat <<EOF
Usage: ./install.sh [--uninstall]

Builds gui/ and installs it to \$PREFIX (default \$HOME/.local, no root needed). Needs cmake and
the Qt 6 Widgets development package; Qt 6 itself is already there on any KDE Plasma desktop.

  Arch/CachyOS   sudo pacman -S --needed cmake qt6-base
  Debian/Ubuntu  sudo apt install cmake qt6-base-dev libgl-dev
  Fedora         sudo dnf install cmake qt6-qtbase-devel
  openSUSE       sudo zypper install cmake qt6-base-devel

Where there is no way to build - an image-based system such as Bazzite has no compiler and a
read-only /usr - the copy in prebuilt/ is installed instead. That choice is made on whether the
toolchain works here, not on which distribution this is, so it covers every ostree variant.
EOF
}

die() {
    echo "$@" >&2
    exit 1
}

# Three questions before trusting the shipped binary, because it is the one thing here that was
# built somewhere else: is it for this machine, was it built from the sources next to it, and does
# it load against the Qt this machine has.
use_prebuilt() {
    [ -f "$PREBUILT" ] || die "There is no prebuilt copy in this checkout, and nothing to build with."

    local machine
    machine="$(uname -m)"
    [ "$machine" = "x86_64" ] || die "The prebuilt copy is x86_64 only, and this machine is $machine.
Install cmake and the Qt 6 Widgets development package, and this script will build instead."

    # A prebuilt older than the sources beside it would install a window that behaves like last
    # month's code while looking current. prebuilt/build.sh is what brings the two back in step.
    sha256sum --status -c prebuilt/SOURCE.sha256 2>/dev/null \
        || die "The prebuilt copy was built from different sources than this checkout has.
Rebuild it with ./prebuilt/build.sh, or install cmake and the Qt 6 Widgets development package."

    "$PREBUILT" --check > /dev/null 2>&1 \
        || die "The prebuilt copy does not load on this machine - its Qt is probably older than the
build. Install cmake and the Qt 6 Widgets development package and this script will build instead."

    echo "Using the prebuilt copy: $("$PREBUILT" --check)"
    BINARY="$PREBUILT"
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

# Configuring is the test, rather than looking for cmake and guessing at the Qt development files:
# it asks the exact question that matters and answers it the same way on every distribution. The
# output is kept back so a machine that was never going to build does not open with a wall of CMake
# errors - only the reason is shown, and only when it decides something.
BINARY=""
CONFIGURE_LOG="$(mktemp)"
trap 'rm -f "$CONFIGURE_LOG"' EXIT

if command -v cmake > /dev/null 2>&1 \
        && cmake -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release > "$CONFIGURE_LOG" 2>&1; then
    cmake --build "$BUILD_DIR"
    BINARY="$BUILD_DIR/lgpowercontrol-gui"
else
    if command -v cmake > /dev/null 2>&1; then
        echo "Cannot build here: $(grep -iE "could not be found|Could NOT find" "$CONFIGURE_LOG" \
            | head -1 | sed 's/^ *//')"
    else
        echo "Cannot build here: cmake is not installed."
    fi
    use_prebuilt
fi

# install(1) rather than cmake --install, because both paths end the same way and only one of them
# has a CMake build tree to install from.
$SUDO install -Dm755 "$BINARY" "$PREFIX/bin/lgpowercontrol-gui"
$SUDO install -Dm644 lgpowercontrol-gui.desktop \
    "$PREFIX/share/applications/lgpowercontrol-gui.desktop"
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
