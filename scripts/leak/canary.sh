#!/bin/bash
# The leak gate's canaries, built at run time from parts so the repo never holds them contiguous.
#   print-word    the canary word (category `canary` in the identifiers list)
#   print-phone   the canary phone (same category; matched like a phone: separators ignored)
#   print-token   a fresh random Telegram-bot-token-shaped string (gitleaks must catch it); never stored
# Use only inside $(…); never echo a canary into a file that stays.
set -euo pipefail
case ${1:-} in
  print-word) printf '%s%s%s\n' zorb lax quent ;;
  print-phone) printf '+%s %s %s %s\n' '1' '555' '01''0' '01''99' ;;   # quoted apart: no phone-like run here
  print-token)
    set +o pipefail   # head closes the pipe on purpose
    digits=$(LC_ALL=C tr -dc '0-9' </dev/urandom | head -c 9)
    tail=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 33)
    printf '%s%s:%s%s\n' "${digits:0:1}" "${digits:1}" AA "$tail" ;;
  *) echo "usage: scripts/leak/canary.sh print-word|print-phone|print-token" >&2; exit 2 ;;
esac
