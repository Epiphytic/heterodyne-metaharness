#!/bin/sh
# S9-10 Part 1 (plan 4c Amendment 1 §4.5): the launcher's Codex launch shape, with no network and no login.
#
#   sh docs/spikes/s9-10/part1.sh <store-cli> <results-dir>
#
# <store-cli> is the unpacked, checksum-verified codex 0.160.0 package (bin/codex, codex-package.json). Runs
# twice, without and then with E25's binds, each in its own `unshare -rmn` namespace plus a PID namespace (so
# nothing a run starts outlives it), capped at 40 s. Every argv comes from the candidate adapter (generate.py);
# analyze.py judges the run. The traces and the summary go to <results-dir>; the run's home is deleted.
# Exits 0 only if both variants pass every check, leave no process and have their home deleted:
# analyze.py fails missing evidence and a capped run.
set -eu
CLI=$(cd "$1" && pwd)
OUT=$2
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY=$REPO/.venv/bin/python
mkdir -p "$OUT"
failed=0

for V in plain e25; do
    T=$(mktemp -d -p "$XDG_RUNTIME_DIR" s9-10.XXXXXX)
    "$PY" "$HERE/generate.py" "$T" "$CLI" $([ "$V" = e25 ] && echo --e25) > "$OUT/$V.generated.txt"
    rc=0
    timeout -k 5 40 unshare -rmnpf --mount-proc --kill-child sh "$HERE/inner.sh" "$T" "$CLI" "$V" "$PY" \
        || rc=$?
    echo "$rc" > "$T/rc"
    # No pipe: sh has no pipefail, and the analyzer's status is the run's verdict.
    "$PY" "$HERE/analyze.py" "$T" "$V" --part 1 > "$OUT/$V.result.txt" || failed=1
    cat "$OUT/$V.result.txt"
    for f in argv.json inside inside.tmp inside.packages server_ready server.log tui.screen rc result.json \
             server.strace tui.strace; do
        [ -e "$T/$f" ] && cp "$T/$f" "$OUT/$V.$f"
    done
    left=$(ps -eo pid=,args= | awk -v t="$T" 'index($0, t) && !/awk/ {print $1}')
    echo "leftover pids: ${left:-none}" >> "$OUT/$V.result.txt"
    [ -z "$left" ] || { failed=1; kill $left; sleep 1; }
    chmod -R u+w "$T" && rm -rf "$T"
    if [ -e "$T" ]; then failed=1; echo "run home NOT deleted" >> "$OUT/$V.result.txt"
    else echo "run home deleted" >> "$OUT/$V.result.txt"; fi
    tail -n 2 "$OUT/$V.result.txt"
done
echo "S9-10 Part 1: $([ "$failed" = 0 ] && echo PASS || echo FAIL)"
exit "$failed"
