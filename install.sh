#!/bin/bash
# Kept only because pre-Python (<= 2.13) installs' update.sh invokes "./install.sh" by
# name. lgpowercontrol.conf is the current name again after the lgtvpc revert, so there
# is nothing left to migrate here - just hand off to the real installer.
set -euo pipefail
cd "$(dirname "$0")"
exec ./install.py
