# shellcheck shell=bash disable=SC2034
# LGPowerControl — shared helpers
# Source this file; do not execute it directly.

RST='\033[0m' RED='\033[0;31m' GRN='\033[0;32m' YEL='\033[0;33m' BLU='\033[0;94m' CYN='\033[0;36m'
SEP='━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

die()  { echo -e "${RED}Error: $1${RST}" >&2; exit 1; }
info() { echo -e "${BLU}$1${RST}"; }
ok()   { echo -e " ${GRN}[OK]${RST}"; }
sep()  { echo -e "${BLU}$SEP${RST}"; }
has()  { command -v "$1" >/dev/null 2>&1; }

confirm() {
    local answer
    read -r -p "$1 [Y/n] " answer
    echo
    [[ "${answer:-Y}" =~ ^[Yy]([Ee][Ss])?$ ]]
}
