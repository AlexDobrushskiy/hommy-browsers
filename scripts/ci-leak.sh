#!/bin/bash
# CI's `leak` and `identities` jobs (.github/workflows/ci.yml; master spec §9).
#   scripts/ci-leak.sh leak         identifiers over the tree and the pushed commits (blobs, path names, headers,
#                                   messages), gitleaks over both; the list from the LEAK_LIST env (Actions secret
#                                   LEAK_IDENTIFIERS) into a 0600 temp file, never echoed
#   scripts/ci-leak.sh identities   author and committer emails of the pushed commits
# The judge is never the diff under judgement: the scanner, .github/commit-identities and .gitleaks.toml come from
# origin/main when it has them (fallback: this tree). Env from the workflow: EVENT, PR_BASE, PR_HEAD, BEFORE, FORK.
set -euo pipefail
job=${1:-}
cd "$(git rev-parse --show-toplevel)"
J=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hm-judge.XXXXXX")
trap 'rm -rf "$J"' EXIT
chmod 700 "$J"

main_sha=$(git rev-parse -q --verify origin/main 2>/dev/null || true)
judge() { # <repo path> <name>: origin/main's copy, else this tree's
  if [ -n "$main_sha" ] && git cat-file -e "origin/main:$1" 2>/dev/null; then
    git show "origin/main:$1" >"$J/$2"; echo "$2: origin/main ${main_sha:0:7}"
  else
    cp "$1" "$J/$2"; echo "$2: worktree"
  fi
}
judge scripts/leak/leak_scan.py leak_scan.py | sed 's/^leak_scan.py/scanner/'
judge .github/commit-identities commit-identities
judge .gitleaks.toml gitleaks.toml

# The pushed range: <head> not reachable from <base>.
zero='^0*$'
if [ "${EVENT:-}" = pull_request ]; then
  head=$PR_HEAD base=$PR_BASE
else
  head=$(git rev-parse HEAD) base=${BEFORE:-}
  if [[ -z $base || $base =~ $zero ]] || ! git cat-file -e "$base^{commit}" 2>/dev/null; then
    base=${main_sha:-}   # a new branch: what main does not have
  fi
fi
if [ -z "$base" ] || [ "$base" = "$head" ]; then base=$(git rev-parse "$head~1" 2>/dev/null || echo "0000000000000000000000000000000000000000"); fi
echo "range: ${base:0:12}..${head:0:12} ($(git rev-list --count "$head" "^$base" 2>/dev/null || git rev-list --count "$head") commits)"

scan() { python3 "$J/leak_scan.py" --identities "$J/commit-identities" --images scripts/leak/images.txt "$@"; }
rc=0
case $job in
  identities)
    scan --identities-only --push "$head" "$base" . || rc=1 ;;
  leak)
    list=()
    if [ -n "${LEAK_LIST:-}" ]; then
      (umask 077; printf '%s\n' "$LEAK_LIST" >"$J/ids.txt")
      list=(--list "$J/ids.txt" --required)
    elif [ "${EVENT:-}" = pull_request ] && [ "${FORK:-}" = true ]; then
      echo "leak: a fork PR gets no secrets: identifier scan skipped (gitleaks and identities still run)"
      list=(--list "$J/none.txt")
    else
      echo "leak: secret LEAK_IDENTIFIERS is not set for this repo; refusing (gh secret set LEAK_IDENTIFIERS)" >&2
      exit 1
    fi
    echo "--- tree ---"
    scan "${list[@]}" --no-identities . || rc=1
    echo "--- pushed commits ---"
    scan "${list[@]}" --no-identities --push "$head" "$base" . || rc=1
    rm -f "$J/ids.txt"
    echo "--- gitleaks ---"
    scripts/tools.sh ensure gitleaks >/dev/null
    gl=$(scripts/tools.sh path gitleaks)
    "$gl" dir --no-banner --redact -v --no-color --log-level warn -c "$J/gitleaks.toml" . || rc=1
    "$gl" git --no-banner --redact -v --no-color --log-level warn -c "$J/gitleaks.toml" \
      --log-opts="$base..$head" . || rc=1 ;;
  *) echo "usage: scripts/ci-leak.sh leak|identities" >&2; exit 2 ;;
esac
[ $rc = 0 ] && echo "$job: ok" || echo "$job: FAILED" >&2
exit $rc
