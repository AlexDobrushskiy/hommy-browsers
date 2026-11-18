#!/bin/bash
# Installs (or updates) this checkout as a hommy-browsers engine for the current user. Idempotent: a re-run with
# nothing new changes no file, plist or service (same PIDs) and ends with `changed: 0`.
#   ./install.sh [--with-captcha] [--no-launchd] [--uninstall]
#     --with-captcha   also the captcha solver: captcha/.venv and its models (Ollama + Hugging Face, several GB)
#     --no-launchd     no services (and so no smoke test): files only
#     --uninstall      stop and remove this install's services; runtime state (HB_STATE_DIR: profiles with the
#                      user's sign-ins) stays until deleted by hand
# Config: config.env next to this file (lib/hbconfig.py documents every key); without it, the defaults.
set -euo pipefail
# shellcheck source=lib/config.sh
source "$(dirname "$0")/lib/config.sh"
CAPTCHA=0 LAUNCHD=1 UNINSTALL=0
for a in "$@"; do
  case $a in
    --with-captcha) CAPTCHA=1 ;;
    --no-launchd) LAUNCHD=0 ;;
    --uninstall) UNINSTALL=1 ;;
    *) sed -n '4,9s/^# \{0,1\}//p' "$0" >&2; exit 2 ;;
  esac
done
UID_=$(id -u)
LA=$HOME/Library/LaunchAgents
CFG=$HB_HOME/lib/hbconfig.py
changed=0
summary=()

step() { printf '== %s\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }
note() { summary+=("$(printf '  %-40s %s' "$1" "$2")"); }
loaded() { launchctl print "gui/$UID_/$1" >/dev/null 2>&1; }
our_loaded() { launchctl list | awk -v p="$HB_LABEL_PREFIX." 'index($3, p) == 1 { print $3 }'; }

bootout() { # bootout LABEL: unload and wait until launchd forgot it
  launchctl bootout "gui/$UID_/$1" 2>/dev/null || true
  for _ in $(seq 1 50); do loaded "$1" || return 0; sleep 0.2; done
  die "launchd did not unload $1"
}

bootstrap() { # bootstrap PLIST LABEL (retries: launchd refuses a label it is still tearing down)
  for _ in 1 2 3 4 5; do
    launchctl bootstrap "gui/$UID_" "$1" 2>/dev/null && return 0
    loaded "$2" && return 0
    sleep 1
  done
  die "launchd refused $1"
}

if ((UNINSTALL)); then
  step "uninstall: services with prefix $HB_LABEL_PREFIX"
  n=0
  for label in $( (/usr/bin/python3 "$CFG" labels; our_loaded) | sort -u); do
    if loaded "$label" || [[ -e $LA/$label.plist ]]; then
      bootout "$label"
      rm -f "$LA/$label.plist"
      n=$((n + 1))
      echo "  removed $label"
    fi
  done
  echo "removed: $n services; runtime state kept in $HB_STATE_DIR (delete it to forget the profiles)"
  exit 0
fi

step "prerequisites"
[[ -d $HB_CHROME_APP ]] || die "Google Chrome not found at $HB_CHROME_APP (install it first: Agent Chrome is a clone of it)"
node=${HB_NODE:-$(command -v node || true)}
[[ -n $node && -x $node ]] || die "node ≥ 20 not found (install Node.js, or set HB_NODE in config.env)"
node=$(/usr/bin/python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$node")
major=$("$node" -p 'process.versions.node.split(".")[0]')
((major >= 20)) || die "node $major found at $node; hommy-browsers needs node ≥ 20"
npm=$(dirname "$node")/npm
[[ -x $npm ]] || npm=$(command -v npm || true)
[[ -n $npm ]] || die "npm not found (it comes with node)"
for tool in uv ffmpeg; do command -v "$tool" >/dev/null || die "$tool not found (brew install $tool)"; done
if ((CAPTCHA)); then command -v ollama >/dev/null || die "ollama not found (brew install ollama), needed by --with-captcha"; fi
echo "  chrome, node $major ($node), npm, uv, ffmpeg$( ((CAPTCHA)) && echo ', ollama')"

step "Playwright MCP (node_modules)"
lock_sum=$(shasum -a 256 "$HB_HOME/package-lock.json" | cut -c1-64)
stamp=$HB_HOME/node_modules/.hb-lock.sha256
if [[ -f $HB_HOME/node_modules/@playwright/mcp/cli.js && -f $stamp && $(cat "$stamp") == "$lock_sum" ]]; then
  echo "  up to date"
else
  (cd "$HB_HOME" && PATH="$(dirname "$node"):$PATH" "$npm" ci --no-audit --no-fund --no-update-notifier --loglevel=error)
  echo "$lock_sum" >"$stamp"
  changed=$((changed + 1))
  note node_modules "installed"
fi

step "Agent Chrome ($HB_BUNDLE_ID)"
before=$(readlink "$HB_STATE_DIR/agent-chrome/current" 2>/dev/null || true)
"$HB_HOME/agent-chrome" >/dev/null
after=$(readlink "$HB_STATE_DIR/agent-chrome/current")
if [[ $before != "$after" ]]; then
  changed=$((changed + 1))
  note "Agent Chrome" "built $after"
fi
echo "  $("$HB_HOME/agent-chrome" --print-app)"

if ((CAPTCHA)); then
  step "captcha solver (captcha/.venv, models)"
  [[ -x $HB_HOME/captcha/.venv/bin/python ]] || { uv venv -q --python 3.12 "$HB_HOME/captcha/.venv"; changed=$((changed + 1)); }
  uv pip install -q --python "$HB_HOME/captcha/.venv/bin/python" -r "$HB_HOME/captcha/requirements.txt"
  ollama list 2>/dev/null | awk '{print $1}' | grep -qx 'qwen3.5:4b' || { ollama pull qwen3.5:4b; changed=$((changed + 1)); }
  "$HB_HOME/captcha/.venv/bin/python" - <<'PY'
from transformers import Owlv2ForObjectDetection, Owlv2Processor
n = "google/owlv2-base-patch16-ensemble"
Owlv2ForObjectDetection.from_pretrained(n); Owlv2Processor.from_pretrained(n); print("  owlv2 ok")
PY
fi

step "plugin MCP registrations (plugin/.mcp.json)"
if [[ $(/usr/bin/python3 "$CFG" render-mcp "$HB_HOME/plugin/.mcp.json") == changed ]]; then
  changed=$((changed + 1))
  note "plugin/.mcp.json" "written"
fi
if [[ $(cat "$HB_HOME/plugin/.hb-home" 2>/dev/null) != "$HB_HOME" ]]; then   # the lease hook's way back to this engine
  printf '%s\n' "$HB_HOME" >"$HB_HOME/plugin/.hb-home"
  changed=$((changed + 1))
  note "plugin/.hb-home" "written"
fi
/usr/bin/python3 "$CFG" ports | while read -r name port dbg; do echo "  $name :$port (debug :$dbg)"; done

if ((LAUNCHD)); then
  step "launchd services ($HB_LABEL_PREFIX.*)"
  mkdir -p "$LA" "$HB_STATE_DIR/logs"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  /usr/bin/python3 "$CFG" render-plists "$tmp" "$node"
  want=()
  for f in "$tmp"/*.plist; do
    label=$(basename "$f" .plist)
    want+=("$label")
    dst=$LA/$label.plist
    if ! cmp -s "$f" "$dst"; then
      state=created
      [[ -e $dst ]] && state=updated
      loaded "$label" && bootout "$label"
      cp "$f" "$dst"
      bootstrap "$dst" "$label"
      changed=$((changed + 1))
      note "$label" "$state"
    elif ! loaded "$label"; then
      bootstrap "$dst" "$label"
      changed=$((changed + 1))
      note "$label" "loaded"
    fi
  done
  for label in $(our_loaded); do   # a service the config no longer has (e.g. a smaller pool)
    [[ " ${want[*]} " == *" $label "* ]] && continue
    bootout "$label"
    rm -f "$LA/$label.plist"
    changed=$((changed + 1))
    note "$label" "removed"
  done

  if [[ -d $HB_IDENTITY_PROFILE/Default ]] && (($(/usr/bin/python3 "$CFG" get HB_POOL) > 0)); then
    step "identity pool: copies of the master profile"
    seeded=$("$HB_HOME/pw-identity" seed --unseeded || true)
    [[ -n $seeded ]] && printf '  %s\n' "${seeded//$'\n'/$'\n'  }"
    n=$(grep -c ': seeded from' <<<"$seeded" || true)
    if ((n > 0)); then changed=$((changed + n)); note "identity pool" "$n seeded"; fi
  fi

  step "smoke test (MCP initialize on every server)"
  body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hommy-browsers-install","version":"1"}}}'
  bad=0 deadline=$((SECONDS + 90))
  while read -r name port _; do
    ok=0
    while ((SECONDS < deadline)); do
      if curl -s -m 5 -X POST "http://127.0.0.1:$port/mcp" -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' -d "$body" 2>/dev/null | grep -q serverInfo; then
        ok=1
        break
      fi
      sleep 1
    done
    if ((ok)); then echo "  $name :$port ok"; else echo "  $name :$port NOT answering (log: $HB_STATE_DIR/logs/$name.log)"; bad=1; fi
  done < <(/usr/bin/python3 "$CFG" ports)
  ((bad)) && die "smoke test failed: a server does not answer MCP initialize"
fi

step "summary"
echo "  services: $(/usr/bin/python3 "$CFG" labels | tr '\n' ' ')"
((${#summary[@]})) && printf '%s\n' "${summary[@]}"
echo "changed: $changed"
