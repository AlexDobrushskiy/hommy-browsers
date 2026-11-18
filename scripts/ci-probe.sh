#!/usr/bin/env bash
# Runs the real CI (ci.yml) on the current checkout's working tree, uncommitted and untracked (not ignored)
# files included, without committing anything here: snapshots the tree through a temp index, commits it on
# ci-probe/<label> parented on HEAD, force-pushes, watches the run, then deletes the remote branch.
# The caller's index, branch, HEAD and stash are untouched. Exit code = the CI result.
# First runs scripts/ci-preflight.sh (ci.yml's static checks, locally) and pushes nothing if it fails;
# Every invocation runs local preflight before any snapshot push.
# Usage: scripts/ci-probe.sh <label> (e.g. 01-r1)
set -euo pipefail
REPO=AlexDobrushskiy/hommy-browsers # origin; named so gh never asks for a default repository

[[ $# == 1 && $1 =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
  echo 'usage: scripts/ci-probe.sh <label> (letters, digits, dot, underscore, hyphen)' >&2; exit 2;
}
label=$1
branch=ci-probe/$label
git check-ref-format "refs/heads/$branch" || { echo "ci-probe: bad label: $label" >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"
source scripts/lib/ci-probe.sh
scripts/ci-preflight.sh || { echo "ci-probe: preflight failed; nothing pushed" >&2; exit 1; }

base_workflow=$(git show origin/main:.github/workflows/ci.yml 2>/dev/null || true)
if [[ $base_workflow != *ci-probe/* ]]; then
  echo 'ci-probe: origin/main has no ci-probe/** push trigger; merge the trigger into main first' >&2
  exit 2
fi

idx=$(mktemp)
run_id='' pushed=0 completed=0
# shellcheck disable=SC2329 # run by the EXIT trap
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  # A monitoring failure is not evidence of a failed CI run. Only an explicit
  # interrupt cancels the run; retain its branch if we cannot observe completion.
  local interrupted=0
  [[ $rc == 130 || $rc == 143 ]] && interrupted=1
  if [[ -n $run_id && $completed == 0 && $interrupted == 1 ]]; then
    gh run cancel -R "$REPO" "$run_id" >/dev/null 2>&1 && echo "ci-probe: run $run_id cancelled" >&2
  fi
  if [[ $pushed == 1 ]]; then
    if [[ $completed == 1 || $interrupted == 1 ]]; then
      git push -q origin --delete "refs/heads/$branch" 2>/dev/null ||
        echo "ci-probe: could not delete origin/$branch; delete it by hand" >&2
    else
      echo "ci-probe: monitoring failed; run ${run_id:-unknown} left running; origin/$branch retained for inspection" >&2
    fi
  fi
  rm -f "$idx"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The caller's index as the starting point (keeps its stat cache), then everything in the working tree.
cp "$(git rev-parse --git-path index)" "$idx" 2>/dev/null || rm -f "$idx"
GIT_INDEX_FILE=$idx git add -A :/
tree=$(GIT_INDEX_FILE=$idx git write-tree)
if [[ $(git cat-file -p "$tree:.github/workflows/ci.yml") != *ci-probe/* ]]; then
  echo "ci-probe: this tree's ci.yml has no ci-probe/** push trigger; merge origin/main first" >&2
  exit 2
fi
sha=$(git commit-tree "$tree" -p HEAD -m "ci-probe: $label")

out=$(git push -q -f origin "$sha:refs/heads/$branch" 2>&1) || { echo "$out" >&2; exit 1; }
pushed=1
echo "ci-probe: pushed $branch ($(git rev-parse --short "$sha")), waiting for the run"

for _ in $(seq 60); do
  run_id=$(ci_probe_gh run list -R "$REPO" --commit "$sha" --event push --workflow ci.yml --json databaseId -q '.[0].databaseId')
  [[ -n $run_id ]] && break
  sleep 3
done
[[ -n $run_id ]] || { echo "ci-probe: no ci run appeared for $sha within 3 min" >&2; exit 1; }
url=$(ci_probe_gh run view -R "$REPO" "$run_id" --json url -q .url)
echo "ci-probe: watching $url"

# gh run watch may terminate on a transient API error. Poll the authoritative
# status instead, applying the same bounded retry to every observation.
while true; do
  conclusion=$(ci_probe_gh run view -R "$REPO" "$run_id" --json status,conclusion -q 'if .status == "completed" then .conclusion else .status end')
  case "$conclusion" in
    queued|in_progress|waiting|pending|requested) sleep 15 ;;
    success|failure|cancelled|timed_out|action_required|neutral|skipped|stale|startup_failure) completed=1; break ;;
    *) echo "ci-probe: unrecognized run status: $conclusion" >&2; exit 1 ;;
  esac
done
elapsed=$(( SECONDS / 60 ))m$(( SECONDS % 60 ))s
echo "ci-probe $label: $conclusion in $elapsed  $url"
[[ $conclusion == success ]] && exit 0

ci_probe_gh run view -R "$REPO" "$run_id" --json jobs -q '.jobs[] | select(.conclusion == "failure")
  | "failed: \(.name) / \([.steps[] | select(.conclusion == "failure") | .name] | join(", "))"'
# Without colour codes and the per-line timestamp.
ci_probe_gh run view -R "$REPO" "$run_id" --log-failed 2>/dev/null |
  sed -E $'s/\x1b\\[[0-9;]*m//g; s/\t[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z /\t/' | tail -n 60
exit 1
