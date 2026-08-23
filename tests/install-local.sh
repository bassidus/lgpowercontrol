#!/usr/bin/env bash
# Installs THIS working tree onto THIS machine, taking the settings from the installation already
# on it rather than from the repo's conf template - so reinstalling your own clone never means
# editing a tracked file to hold your TV's address. Afterwards it runs check_installation.py.
#
# Why install.py alone is not enough for this: install() wipes /opt and copies the REPO conf into
# place, deliberately, so a reinstall lands on exactly what the repo says. The repo conf is a
# template with an empty LGTV_IP, so a plain run stops and asks you to fill it in - and then your
# TV's address and MAC sit in a tracked file. This stages a copy of the tree with the LIVE conf as
# its template instead, installs from the copy, and throws the copy away. (The bash versions'
# update.sh did the same thing for the same reason; see install.sh.)
#
# It lives under tests/ rather than at the repo root on purpose: ./install.py is the install path
# this project documents, and this one wipes and rebuilds /opt/lgpowercontrol. Nothing that does
# that should sit where it can be run by mistake - hence the location and the confirmation below.
#
# NOT a test runner. Run ./tests/run_all.py first (as root, or the three Wake-on-LAN cases are
# skipped) - rigs before hardware. What runs at the end here checks the installation, not the code.
#
# Run it as yourself, not under sudo: the staging copy and the checks afterwards have to belong to
# your user. It calls sudo once, for install.py, and the password is typed at that prompt. Nothing
# here reads one from a file, an environment variable or a pipe, and nothing should ever add that.
#
#   ./tests/install-local.sh              ask, stage, install, verify
#   ./tests/install-local.sh --yes        skip the question
#   ./tests/install-local.sh --dry-run    stage and preflight only, stop before sudo
#
# To check an installation without touching it: ./tests/check_installation.py

set -uo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
LIVE_CONF=/opt/lgpowercontrol/lgpowercontrol.conf

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else B=; G=; R=; Y=; N=; fi

DRY=false
ASSUME_YES=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=true ;;
        --yes|-y)  ASSUME_YES=true ;;
        *) echo "usage: ${0##*/} [--dry-run] [--yes]" >&2; exit 64 ;;
    esac
done

[ "$(id -u)" -eq 0 ] && {
    echo "Run this as yourself, not with sudo: the staging copy and the checks afterwards" >&2
    echo "have to belong to your user. It calls sudo itself, once." >&2
    exit 1
}

# Default no, and asked before anything is staged: the sudo prompt further down is not a safety
# net, since by then the answer is muscle memory.
if ! $DRY && ! $ASSUME_YES; then
    printf '%sThis removes and reinstalls /opt/lgpowercontrol from %s.%s\n' "$Y" "$REPO" "$N"
    printf 'Settings and pairing key are carried over. Continue? [y/N] '
    read -r answer
    case "$answer" in [Yy]*) ;; *) echo "Nothing done."; exit 0 ;; esac
fi

STAGE=
trap '[ -n "$STAGE" ] && rm -rf "$STAGE"' EXIT

printf '\n%s=== what is about to be installed ===%s\n' "$B" "$N"
printf '  commit    %s\n' "$(git -C "$REPO" log -1 --format='%h %s' 2>/dev/null || echo '(no git)')"
dirty=$(git -C "$REPO" status --porcelain 2>/dev/null)
if [ -n "$dirty" ]; then
    printf '  %sthe working tree, not the commit - uncommitted:%s\n' "$Y" "$N"
    printf '            %s\n' $(echo "$dirty" | awk '{print $2}')
else
    printf '  tree      clean\n'
fi

STAGE=$(mktemp -d) || exit 1
tar cf - --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude=build \
         --exclude=.ruff_cache --exclude=CLAUDE.local.md --exclude='*.egg-info' \
         -C "$REPO" . | tar xf - -C "$STAGE" || exit 1

# The live conf becomes the template the installer copies into place, so nothing set on this
# machine is lost to the repo's empty defaults. Without one, the repo template is all there is.
if [ -f "$LIVE_CONF" ]; then
    cp "$LIVE_CONF" "$STAGE/lgpowercontrol.conf" || exit 1
    printf '  conf      %s (live settings carried over)\n' "$LIVE_CONF"
else
    printf '  %sconf      nothing installed to carry settings from - using the repo template%s\n' "$Y" "$N"
fi

tv_ip=$(sed -n 's/^LGTV_IP="\(.*\)".*/\1/p' "$STAGE/lgpowercontrol.conf")
[ -z "$tv_ip" ] && {
    echo "LGTV_IP is empty - set it in $LIVE_CONF, or in the repo conf on a first install." >&2
    exit 1
}

printf '\n%s=== preflight ===%s\n' "$B" "$N"
# /dev/tcp rather than nc, which is not installed everywhere: a missing binary would read as a
# dead TV and send you looking at the wrong thing.
if timeout 3 bash -c "cat < /dev/null > /dev/tcp/$tv_ip/3001" 2>/dev/null; then
    printf '  %sok%s    TV answers on %s:3001 (install.py aborts if it does not)\n' "$G" "$N" "$tv_ip"
else
    printf '  %sFAIL%s  TV does not answer on %s:3001 - turn it on; the installer would abort here\n' "$R" "$N" "$tv_ip"
    exit 1
fi
if [ -f /opt/lgpowercontrol/.aiopylgtv.sqlite ]; then
    printf '  %sok%s    pairing key present - install() carries it across, no dialog on the TV\n' "$G" "$N"
else
    printf '  %sskip%s  no pairing key: the installer will pair, which pops a dialog on the TV\n' "$Y" "$N"
fi

$DRY && { printf '\ndry run: staged in %s, stopping before sudo.\n' "$STAGE"; exit 0; }

printf '\n%s=== sudo ./install.py ===%s\n' "$B" "$N"
( cd "$STAGE" && sudo ./install.py ) || {
    printf '\n%sinstall.py failed - checking now would only report the old installation.%s\n' "$R" "$N" >&2
    exit 1
}

# The staged conf goes along so the checker can tell whether the installer carried the settings
# through. It works everything else out on its own, which is why it stands alone.
"$REPO/tests/check_installation.py" --conf-was "$STAGE/lgpowercontrol.conf"
rc=$?

echo
echo "  What this cannot give you is a real suspend with the TV on. After the next one:"
echo "    lgpowercontrol log 20"
echo "  Silence from the OFF path means it worked; 'TV left on - OFF exited N' means it did not."
exit $rc
