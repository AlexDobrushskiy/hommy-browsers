#!/bin/bash
# The scratch test install of this tree (e2e/README.md): a copy of the working tree (tracked + untracked, not
# ignored) at /tmp/hm-s<N>/home/.local/share/hommy-browsers with a test config.env for the slot's layout B
# (docs/testing.md): lanes B, identity B+1, identity-a B+2, debug B+3…B+5, labels dev.hommy.test-s<N>.browsers.*,
# bundle id dev.hommy.test-s<N>.agent-chrome, state under the scratch HOME. Prints the install's path.
#   scripts/test-install.sh [--slot N]      (default slot 1; the harness's test-env.sh must be up for that slot)
# Re-runs copy only what changed (content compared), so node_modules and the services' state stay.
set -euo pipefail
slot=1
while (($#)); do
  case $1 in
    --slot) slot=${2:?--slot needs a number}; shift 2 ;;
    *) sed -n '6,7s/^# \{0,1\}//p' "$0" >&2; exit 2 ;;
  esac
done
[[ $slot =~ ^[1-9]$ ]] || { echo "test-install: slot 1-9 (slot 0 is the hommy phase checkout's)" >&2; exit 2; }
repo=$(cd "$(dirname "$0")/.." && pwd)
home=/tmp/hm-s$slot/home
[[ -d $home ]] || { echo "test-install: no scratch HOME $home (scripts/test-env.sh up --slot $slot --layout browsers --no-start in the harness first)" >&2; exit 1; }
hb=$home/.local/share/hommy-browsers
base=$((4710 + 10 * slot))
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git -C "$repo" ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' f; do
  [[ -e $repo/$f ]] && printf '%s\0' "$f"
done | (cd "$repo" && tar -c --null -T - -f -) | tar -x -C "$src" -f -
mkdir -p "$hb"
rsync -a --checksum --delete --exclude /node_modules/ --exclude /config.env --exclude /plugin/.mcp.json --exclude /plugin/.hb-home \
  --exclude /captcha/.venv/ --exclude __pycache__/ "$src/" "$hb/"
conf=$src/.config.env
cat >"$conf" <<CONF
# test install, slot $slot (layout B); written by scripts/test-install.sh
HB_PORT_BASE=$base
HB_DEBUG_BASE=$((base + 3))
HB_POOL=1
HB_LABEL_PREFIX=dev.hommy.test-s$slot.browsers
HB_BUNDLE_ID=dev.hommy.test-s$slot.agent-chrome
HB_STATE_DIR=$home/.local/state/hommy-browsers
HB_REAPER_SCOPE=own
HB_LOGIN_EMAIL=owner@example.test
CONF
cmp -s "$conf" "$hb/config.env" || { cp "$conf" "$hb/config.env"; chmod 600 "$hb/config.env"; }
echo "$hb"
