#!/bin/sh
# S9-10 Part 2 (plan 4c Amendment 1 §4.5): the team lead's run, with a network and the live-test login.
#
#   S9_10_LOGIN=<live-test codex auth.json> sh docs/spikes/s9-10/part2.sh <store-cli> <results-dir>
#
# Same generated argv and socket binds as Part 1, under `unshare -rmpf` (no -n: a thread needs model
# traffic). The login is bind-mounted read-only at <home>/.codex/auth.json inside the namespace only; the
# script never reads or copies it. Budget: HZ_LIVE_SANDBOX_CODEX_MODEL (default gpt-6-luna) with low effort,
# one short prompt per variant. The effort comes from an install's defaults/config.toml, since Q4 leaves no
# profile route and no argv may carry -c. Each variant: hook trust, server, a new session with the prompt,
# then `resume <thread>`; plain, then with E25's binds; capped at 240 s.
# Exits 0 only if both variants pass every check, leave no process and have their home deleted:
# analyze.py fails missing evidence and a capped run.
set -eu
CLI=$(cd "$1" && pwd)
OUT=$2
LOGIN=${S9_10_LOGIN:?set S9_10_LOGIN to the live-test codex login file}
MODEL=${HZ_LIVE_SANDBOX_CODEX_MODEL:-gpt-6-luna}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PY=$REPO/.venv/bin/python
NS=-rmpf
[ "${S9_10_DRY:-}" = 1 ] && NS=-rmnpf        # a harness check: no network, so no model call
mkdir -p "$OUT"
failed=0

for V in plain e25; do
    T=$(mktemp -d -p "$XDG_RUNTIME_DIR" s9-10.XXXXXX)
    mkdir -p "$T/install/defaults"
    printf 'model_reasoning_effort = "low"\n' > "$T/install/defaults/config.toml"
    "$PY" "$HERE/generate.py" "$T" "$CLI" --install "$T/install" --model "$MODEL" > "$OUT/$V.generated.txt"
    rc=0
    timeout -k 5 240 unshare $NS --mount-proc --kill-child \
        sh "$HERE/inner2.sh" "$T" "$CLI" "$V" "$PY" "$LOGIN" "$MODEL" || rc=$?
    echo "$rc" > "$T/rc"
    # No pipe: sh has no pipefail, and the analyzer's status is the run's verdict.
    "$PY" "$HERE/analyze.py" "$T" "$V" --part 2 > "$OUT/$V.result.txt" || failed=1
    cat "$OUT/$V.result.txt"
    for f in argv.json argv-resume.json inside inside.tmp inside.packages trust.txt server_ready server.log \
             composer answered thread resumed tui.screen tui-resume.screen rc result.json \
             server.strace tui.strace tui-resume.strace server.strace.unended tui.strace.unended \
             tui-resume.strace.unended; do
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
echo "S9-10 Part 2: $([ "$failed" = 0 ] && echo PASS || echo FAIL)"
exit "$failed"
