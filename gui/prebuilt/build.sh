#!/bin/bash
# Rebuilds the binary that ships in this directory. Run it after any change to the window's sources,
# and commit what it writes - install.sh refuses a prebuilt copy whose sources have moved on.
#
# That refusal is the point of SOURCE.sha256. A shipped binary that silently lags the code is the
# same trap this repo already has a scar from: a rig once ran against an old installed copy and
# reported green, twice. A checksum turns that into an error message instead of a wrong answer.
set -euo pipefail

PREBUILT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PREBUILT_DIR/.."

IMAGE="lgpowercontrol-gui-builder"

ENGINE="${ENGINE:-}"
if [ -z "$ENGINE" ]; then
    for candidate in podman docker; do
        if command -v "$candidate" > /dev/null 2>&1; then
            ENGINE="$candidate"
            break
        fi
    done
fi
if [ -z "$ENGINE" ]; then
    echo "Needs podman or docker to build the shipped copy. Set ENGINE= to name one." >&2
    exit 1
fi

# A failed compile would otherwise leave a truncated .new file sitting next to the committed binary.
trap 'rm -f "$PREBUILT_DIR/lgpowercontrol-gui.new"' EXIT

echo "Building the image with $ENGINE (first run downloads Ubuntu 22.04)..."
"$ENGINE" build -t "$IMAGE" prebuilt

# The sources go in read-only and the build happens on the container's own filesystem, with the
# finished binary coming back on stdout. Nothing is written into the work tree, which sidesteps the
# ownership question entirely - under rootless podman the caller maps to root inside the container,
# under docker it does not, so any file the build left behind would belong to a different user
# depending on which engine ran it. Everything but the binary is silenced for the same reason: one
# stray line on stdout would land inside the file.
echo "Compiling in the container..."
"$ENGINE" run --rm -v "$PWD:/src:ro,z" "$IMAGE" sh -c '
    set -e
    cp -r /src /work
    rm -rf /work/build          # a CMakeCache.txt from the host names host paths and stops cmake
    cd /work
    cmake -B build -DCMAKE_BUILD_TYPE=Release > /dev/null
    cmake --build build > /dev/null
    cat build/lgpowercontrol-gui
' > "$PREBUILT_DIR/lgpowercontrol-gui.new"

mv "$PREBUILT_DIR/lgpowercontrol-gui.new" "$PREBUILT_DIR/lgpowercontrol-gui"
chmod 755 "$PREBUILT_DIR/lgpowercontrol-gui"
sha256sum main.cpp conf.cpp conf.h CMakeLists.txt > "$PREBUILT_DIR/SOURCE.sha256"

echo
echo "Wrote prebuilt/lgpowercontrol-gui and prebuilt/SOURCE.sha256:"
ls -l "$PREBUILT_DIR/lgpowercontrol-gui"
"$PREBUILT_DIR/lgpowercontrol-gui" --check
