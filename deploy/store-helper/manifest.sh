#!/usr/bin/env bash
# The sums the execution approval pins for step 3 (README, "The root commands"). Run unprivileged, from any
# checkout that has the commit:
#
#   bash deploy/store-helper/manifest.sh --commit <pinned commit> [--repo DIR]
#
# It builds the archive exactly as step 3 does and prints `<sha256>  archive.tar`, then
# `<sha256>  tree/<path>` for each file in it: the lines step 3 checks before it extracts and after.
# Replacement refs are ignored here as there, so the sums are the commit's own bytes.
set -euo pipefail

readonly PATHS=(deploy/store-helper src/heterodyne/harness/resources/store_core.py)

usage() {
    echo "usage: manifest.sh --commit <full commit ID> [--repo DIR]" >&2
    exit 2
}

commit="" repo=""
while (($#)); do
    case "$1" in
        --commit) (($# >= 2)) || usage; commit=$2; shift 2 ;;
        --repo) (($# >= 2)) || usage; repo=$2; shift 2 ;;
        *) usage ;;
    esac
done
[[ -n $commit ]] || usage
[[ $commit =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || { echo "refused: the commit is not a full commit ID" >&2; exit 1; }
if [[ -z $repo ]]; then
    repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
env -i PATH=/usr/bin:/bin GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -C "$repo" -c core.hooksPath=/dev/null archive --format=tar "$commit" -- "${PATHS[@]}" >"$work/archive.tar"
mkdir "$work/tree"
tar -x -C "$work/tree" -f "$work/archive.tar"
cd "$work"
sha256sum archive.tar
find tree -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
