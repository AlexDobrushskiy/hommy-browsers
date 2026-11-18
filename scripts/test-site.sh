#!/bin/bash
# The static test page (e2e/fixtures/static, heading "hommy-browsers test page") on the slot's B+6, served by the
# harness's `hommy-e2e static-site` and detached with its pid file where `test-env.sh down` stops it too.
#   scripts/test-site.sh start|stop [--slot N]      (default slot 1; HOMMY_HARNESS = the hommy worktree)
set -euo pipefail
cmd=${1:-}
shift || true
slot=1
[[ ${1:-} == --slot ]] && slot=${2:?}
repo=$(cd "$(dirname "$0")/.." && pwd)
harness=${HOMMY_HARNESS:-$(dirname "$repo")/hommy-harness}
root=/tmp/hm-s$slot
port=$((4716 + 10 * slot))
pidf=$root/run/static-site.pid
# The pid file is "<pid> <start time>" (detach.py): alive only when that pid still has that start time.
site_pid() {
  local p rest
  [[ -f $pidf ]] && read -r p rest <"$pidf" || return 1
  [[ -n $p && "$(ps -o lstart= -p "$p" 2>/dev/null | xargs)" == "$rest" ]] && echo "$p"
}
case $cmd in
  start)
    [[ -x $harness/bin/hommy-e2e ]] || { echo "test-site: build the harness first ($harness/scripts/dev-build.sh)" >&2; exit 1; }
    if site_pid >/dev/null; then echo "static site already running on :$port"; exit 0; fi
    mkdir -p "$root/run" "$root/logs"
    "$harness/scripts/sx.sh" --slot "$slot" python3 "$harness/scripts/lib/detach.py" --pid-file "$pidf" \
      --log "$root/logs/static-site.log" -- "$harness/bin/hommy-e2e" static-site --addr "127.0.0.1:$port" \
      --dir "$repo/e2e/fixtures/static"
    for _ in $(seq 1 50); do curl -fsS -m 1 -o /dev/null "http://127.0.0.1:$port/" 2>/dev/null && { echo "static site on :$port"; exit 0; }; sleep 0.1; done
    echo "test-site: no answer on :$port" >&2; exit 1 ;;
  stop)
    if p=$(site_pid); then kill "$p"; for _ in $(seq 1 50); do kill -0 "$p" 2>/dev/null || break; sleep 0.1; done; fi
    rm -f "$pidf"; echo "static site stopped" ;;
  *) sed -n '4s/^# \{0,1\}//p' "$0" >&2; exit 2 ;;
esac
