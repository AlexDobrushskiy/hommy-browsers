#!/bin/bash
# The MCP `initialize` handshake on 127.0.0.1:PORT (streamable HTTP): prints the JSON-RPC response (its result names
# the server in serverInfo); exit 1 when the server does not answer with a result. The harness's mcp-call.sh calls
# tools only, so E4's initialize row uses this.
#   scripts/mcp-init.sh PORT
set -euo pipefail
(($# == 1)) || { sed -n '5s/^# \{0,1\}//p' "$0" >&2; exit 2; }
body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"hommy-browsers-e2e","version":"1"}}}'
out=$(curl -sS -m 30 -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d "$body" "http://127.0.0.1:$1/mcp") || { echo "mcp-init: no answer on :$1" >&2; exit 1; }
res=$(sed -n 's/^data: *//p' <<<"$out" | tail -1)
[[ -n $res ]] || res=$out
printf '%s\n' "$res"
grep -q '"serverInfo"' <<<"$res"
