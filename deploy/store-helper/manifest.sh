#!/usr/bin/env bash
# The sums the execution approval pins for the installer (README, "Running the installer"). Run unprivileged,
# from any checkout that has the commit:
#
#   bash deploy/store-helper/manifest.sh --commit <pinned commit> [--repo DIR]
#
# It builds the archive exactly as deploy/install.sh does, and prints `<sha256>  archive.tar` (the pinned
# form's --expect-sha256), then `<sha256>  deploy/install.sh` from the commit's blob (the pinned form's check
# of its root-only copy), then `<sha256>  tree/<path>` for each file in the archive (deploy/uninstall.sh's
# among them). Replacement refs are ignored here as there, so the sums are the commit's own bytes. PATHS is
# install.sh's, written out here too, since neither script may source a file to get it.
set -euo pipefail

readonly PATHS=(deploy src/heterodyne/harness/resources/store_core.py)

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

git_() {
    env -i PATH=/usr/bin:/bin GIT_NO_REPLACE_OBJECTS=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        git -C "$repo" -c core.hooksPath=/dev/null "$@"
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git_ archive --format=tar "$commit" -- "${PATHS[@]}" >"$work/archive.tar"
git_ cat-file blob "$commit:deploy/install.sh" >"$work/install.sh"
mkdir "$work/tree"
tar -x -C "$work/tree" -f "$work/archive.tar"
cd "$work"
sha256sum archive.tar
installer=$(sha256sum <install.sh)
echo "${installer%% *}  deploy/install.sh"
find tree -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
