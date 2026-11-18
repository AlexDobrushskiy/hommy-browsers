#!/bin/bash
# The leak gate's scanner (scripts/leak/leak_scan.py; master spec §9). Modes and output: `scripts/leak-scan.sh --help`.
#   scripts/leak-scan.sh [<path>...] | --staged [repo] | --push <local> <remote> [repo] | --history [repo] | --self-test
# Exit 0 clean, 1 hit, 2 no identifiers list where one is required. Never prints a matched text.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
if [ "${1:-}" = --self-test ]; then
  "$ROOT/scripts/tools.sh" ensure gitleaks >/dev/null
  HOMMY_GITLEAKS=$("$ROOT/scripts/tools.sh" path gitleaks)
  export HOMMY_GITLEAKS
fi
exec python3 "$ROOT/scripts/leak/leak_scan.py" "$@"
