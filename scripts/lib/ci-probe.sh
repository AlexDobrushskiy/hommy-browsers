#!/usr/bin/env bash
# Read-only GitHub monitoring calls: retry transport / 5xx errors five times, with
# bounded backoff. Authentication, permissions and missing-run errors fail immediately.
ci_probe_gh() {
  local attempt=1 output rc
  while true; do
    if output=$(gh "$@" 2>&1); then
      printf '%s\n' "$output"
      return 0
    else
      rc=$?
    fi
    if ((attempt >= 5)) || grep -Eiq 'HTTP[^0-9]*4[0-9]{2}' <<<"$output" || ! grep -Eiq 'HTTP[^0-9]*5[0-9]{2}|error connecting|connection (reset|refused|closed|lost)|network is unreachable|no such host|could not resolve host|temporary failure in name resolution|TLS handshake timeout|i/o timeout|operation timed out|Client.Timeout|context deadline exceeded|unexpected EOF|(^|[[:space:]])EOF($|[[:space:]])' <<<"$output"; then
      printf '%s\n' "$output" >&2
      return "$rc"
    fi
    echo "ci-probe: transient GitHub error; retry $attempt/4 in $((attempt * 3))s" >&2
    sleep "$((attempt * 3))"
    attempt=$((attempt + 1))
  done
}
