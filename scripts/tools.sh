#!/bin/bash
# Pinned tools from their release archives (versions + SHA-256 in scripts/tools.env), never from Homebrew.
#   scripts/tools.sh ensure <tool>...   download, verify and unpack into .dev/tools/ (idempotent); links .dev/tools/<tool>
#   scripts/tools.sh path <tool>        print the tool's path (after ensure)
#   scripts/tools.sh list               the pinned tools and versions
# Tools: gitleaks, shellcheck, tart, goreleaser. HOMMY_TOOLS_DIR overrides .dev/tools.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
# shellcheck source=scripts/tools.env
. "$ROOT/scripts/tools.env"
DIR=${HOMMY_TOOLS_DIR:-$ROOT/.dev/tools}
TOOLS="gitleaks shellcheck tart goreleaser"

die() { echo "tools.sh: $*" >&2; exit 1; }
var() { local u; u=$(echo "$1" | tr '[:lower:]' '[:upper:]'); eval "printf '%s' \"\${${u}_$2:-}\""; }
known() { case " $TOOLS " in *" $1 "*) return 0 ;; esac; die "unknown tool '$1' (known: $TOOLS)"; }

ensure() {
  local t=$1 ver url sum bin dest tmp arc
  known "$t"
  ver=$(var "$t" VERSION) url=$(var "$t" URL) sum=$(var "$t" SHA256) bin=$(var "$t" BIN)
  dest=$DIR/$t-$ver
  if [ ! -x "$dest/$bin" ]; then
    mkdir -p "$DIR"
    tmp=$(mktemp -d "$DIR/.dl.XXXXXX")
    arc=$tmp/${url##*/}
    curl -fsSL --retry 3 -o "$arc" "$url" || { rm -rf "$tmp"; die "$t: download failed: $url"; }
    if ! echo "$sum  $arc" | shasum -a 256 -c - >/dev/null 2>&1; then
      rm -rf "$tmp"; die "$t: SHA-256 mismatch for ${url##*/} (want $sum)"
    fi
    mkdir -p "$tmp/x"
    case $arc in
      *.tar.gz | *.tgz) tar -xzf "$arc" -C "$tmp/x" ;;
      *.tar.xz) tar -xJf "$arc" -C "$tmp/x" ;;
      *) rm -rf "$tmp"; die "$t: unknown archive type" ;;
    esac
    [ -x "$tmp/x/$bin" ] || { rm -rf "$tmp"; die "$t: $bin not in the archive"; }
    rm -rf "$dest"
    mv "$tmp/x" "$dest"
    rm -rf "$tmp"
  fi
  ln -sfn "$t-$ver/$bin" "$DIR/$t"
  echo "$t $ver: $DIR/$t"
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case $cmd in
  ensure) [ $# -gt 0 ] || die "usage: ensure <tool>..."; for t in "$@"; do ensure "$t"; done ;;
  path) [ $# = 1 ] || die "usage: path <tool>"; known "$1"; [ -x "$DIR/$1" ] || die "$1 not installed: scripts/tools.sh ensure $1"; echo "$DIR/$1" ;;
  list) for t in $TOOLS; do echo "$t $(var "$t" VERSION)"; done ;;
  *) echo "usage: scripts/tools.sh ensure <tool>... | path <tool> | list" >&2; exit 2 ;;
esac
