#!/bin/bash
# One MCP session kept across commands on 127.0.0.1:PORT (streamable HTTP, Mcp-Session-Id), so a row can call
# several tools in the same lane context with other commands in between (the harness's mcp-call.sh opens a new
# session per call, and a lane gives every session its own throwaway context).
#   scripts/mcp-session.sh PORT start            initialize; the session id goes to $TMPDIR/hb-mcp-<PORT>.sid
#   scripts/mcp-session.sh PORT call TOOL JSON   one tools/call in that session; prints the result JSON
#                                                (exit 1 on an error or isError)
#   scripts/mcp-session.sh PORT end              ends the session (DELETE), which frees a lane's context
set -euo pipefail
(($# >= 2)) || { sed -n '4,8s/^# \{0,1\}//p' "$0" >&2; exit 2; }
port=$1 cmd=$2
url=http://127.0.0.1:$port/mcp
sidf=${TMPDIR:-/tmp}/hb-mcp-$port.sid
hdrs=$(mktemp)
trap 'rm -f "$hdrs"' EXIT

rpc() { # BODY [SID]: one message; prints the JSON-RPC response (from JSON or an SSE stream)
  local extra=() out
  [[ -n ${2:-} ]] && extra=(-H "Mcp-Session-Id: $2")
  out=$(curl -sS -m 120 -D "$hdrs" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H 'MCP-Protocol-Version: 2025-06-18' ${extra[@]+"${extra[@]}"} -d "$1" "$url") || { echo "mcp-session: no answer at $url" >&2; exit 1; }
  if grep -qi '^content-type: *text/event-stream' "$hdrs"; then
    sed -n 's/^data: *//p' <<<"$out" | jq -c 'select(.id != null)' | tail -1
  else
    printf '%s\n' "$out"
  fi
}

case $cmd in
  start)
    init=$(rpc '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hommy-browsers-e2e","version":"1"}}}')
    jq -e '.result' >/dev/null <<<"$init" 2>/dev/null || { echo "mcp-session: initialize failed: $init" >&2; exit 1; }
    sid=$(sed -n 's/^[Mm][Cc][Pp]-[Ss]ession-[Ii]d: *\([^[:space:]]*\).*/\1/p' "$hdrs" | head -1)
    [[ -n $sid ]] || { echo "mcp-session: the server gave no session id" >&2; exit 1; }
    rpc '{"jsonrpc":"2.0","method":"notifications/initialized"}' "$sid" >/dev/null
    printf '%s\n' "$sid" >"$sidf"
    echo "session started on :$port" ;;
  call)
    (($# == 4)) || { echo "usage: mcp-session.sh PORT call TOOL JSON" >&2; exit 2; }
    [[ -s $sidf ]] || { echo "mcp-session: no session on :$port (start first)" >&2; exit 1; }
    body=$(jq -cn --arg t "$3" --argjson a "$4" '{jsonrpc: "2.0", id: 2, method: "tools/call", params: {name: $t, arguments: $a}}')
    res=$(rpc "$body" "$(cat "$sidf")")
    jq -e '.result' >/dev/null <<<"$res" 2>/dev/null || { echo "mcp-session: $3 failed: $res" >&2; exit 1; }
    jq -c '.result' <<<"$res"
    jq -e '.result.isError != true' >/dev/null <<<"$res" ;;
  end)
    [[ -s $sidf ]] || { echo "no session on :$port"; exit 0; }
    curl -sS -m 30 -o /dev/null -X DELETE -H "Mcp-Session-Id: $(cat "$sidf")" "$url" || true
    rm -f "$sidf"
    echo "session ended on :$port" ;;
  *) sed -n '4,8s/^# \{0,1\}//p' "$0" >&2; exit 2 ;;
esac
