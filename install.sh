#!/bin/bash
# Transitional shim for updates from the bash versions (<= 2.13). Their update.sh downloads the
# release tarball, copies the installed lgpowercontrol.conf into it and runs ./install.sh, which
# stopped existing in v4.0 - without this, those installs cannot update at all. The conf needs no
# translation: it is the same filename install.py reads, so the settings carry over on their own,
# and the pairing key survives because it has always lived at the same path install.py preserves.
# Fresh installs never reach this file; the README says ./install.py. Delete it, and this note,
# once pre-3.0 installs have died out.
set -euo pipefail
cd "$(dirname "$0")"
exec ./install.py
