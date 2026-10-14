#!/bin/sh
# S9-10 Part 1 (plan 4c Amendment 1 §4.5): the launcher's Codex launch shape, with no network and no login.
#
#   sh docs/spikes/s9-10/part1.sh <store-cli>
#
# <store-cli> is an unpacked codex 0.160.0 package tree (bin/codex, codex-package.json). Runs twice, without
# and then with E25's binds, each in its own `unshare -rmn` namespace (plus a PID namespace, so nothing the
# run starts outlives it). Every argv comes from the candidate adapter (generate.py); the results are judged
# by analyze.py. Needs no Podman, no root and no login file.
set -eu
CLI=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d -p "$XDG_RUNTIME_DIR" s9-10.XXXXXX)
echo "run directory: $T"

for V in plain e25; do
    mkdir "$T/$V"
    uv run python "$HERE/generate.py" "$T/$V" "$CLI" > "$T/$V/generated.txt"
    unshare -rmnpf --mount-proc --kill-child sh "$HERE/inner.sh" "$T/$V" "$CLI" "$V"
    python3 "$HERE/analyze.py" "$T/$V" "$V"
done
