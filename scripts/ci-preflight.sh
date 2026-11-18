#!/bin/bash
# ci.yml's checks, locally, with the same pinned tools: the offline tests (python3 -m unittest, tests/), shellcheck,
# the scanner's unit tests + `leak-scan.sh --self-test`, gitleaks over the tree.
#   scripts/ci-preflight.sh [test|shellcheck|leak|gitleaks]...   (default: all, in that order)
# Exit 0 when every step passed. CI's test and shellcheck jobs run `scripts/ci-preflight.sh test|shellcheck`.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT" || exit 1
steps=("$@"); [ ${#steps[@]} = 0 ] && steps=(test shellcheck leak gitleaks)
fail=()

shell_files() { # tracked + untracked-not-ignored sh/bash scripts (shellcheck does not read zsh)
  git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    case $f in
      *.sh | *.bash) printf '%s\0' "$f" ;;
      *) if head -n 1 "$f" 2>/dev/null | grep -Eq '^#!.*[/ ](ba)?sh([[:space:]]|$)'; then printf '%s\0' "$f"; fi ;;
    esac
  done
}

step() {
  case $1 in
    test) /usr/bin/python3 -m unittest discover -s tests 2>&1 | tail -n 4; return "${PIPESTATUS[0]}" ;;
    shellcheck)
      scripts/tools.sh ensure shellcheck >/dev/null || return 1
      sc=$(scripts/tools.sh path shellcheck)
      "$sc" --version | sed -n 2p
      shell_files >"$ROOT/.dev/shell-files" || return 1
      [ -s "$ROOT/.dev/shell-files" ] || { echo "no shell scripts"; return 0; }
      tr '\0' '\n' <"$ROOT/.dev/shell-files" | sed 's/^/  /'
      xargs -0 "$sc" -x <"$ROOT/.dev/shell-files" ;;
    leak) python3 -m unittest discover -s scripts/leak 2>&1 | tail -n 3 && scripts/leak-scan.sh --self-test ;;
    gitleaks)
      scripts/tools.sh ensure gitleaks >/dev/null || return 1
      "$(scripts/tools.sh path gitleaks)" dir --no-banner --redact -v --no-color --log-level warn -c .gitleaks.toml . ;;
    *) echo "unknown step: $1"; return 2 ;;
  esac
}

mkdir -p "$ROOT/.dev"
for s in "${steps[@]}"; do
  echo "== $s"
  if step "$s"; then echo "== $s: ok"; else echo "== $s: FAILED"; fail+=("$s"); fi
done
if [ ${#fail[@]} = 0 ]; then echo "preflight: ok (${steps[*]})"; exit 0; fi
echo "preflight: FAILED: ${fail[*]}"
exit 1
