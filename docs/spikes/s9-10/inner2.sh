#!/bin/sh
# S9-10 Part 2, inside `unshare -rmpf` (a network, unlike Part 1): Part 1's binds plus the live-test login
# read-only at <home>/.codex/auth.json, the hook trust, the server, a new session with one short prompt,
# then a resume of its thread.
#   inner2.sh <run-dir> <store-cli> <plain|e25> <python> <login-file> <model>
set -eu
R=$1; CLI=$2; V=$3; PY=$4; LOGIN=$5; MODEL=$6
HERE=$(cd "$(dirname "$0")" && pwd)
get() { "$PY" -c 'import json,shlex,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]
print(shlex.join(v) if isinstance(v, list) else v)' "$2" "$1"; }
HOME_DIR=$(get home "$R/argv.json"); BRIDGE=$(get bridge "$R/argv.json"); DAEMON=$(get daemon "$R/argv.json")
WT=$(get worktree "$R/argv.json"); BRIDGE_INSIDE=$(get bridge_inside "$R/argv.json")
SERVER=$(get server "$R/argv.json"); TUI=$(get tui_new "$R/argv.json"); SESSION=$(get session "$R/argv.json")

mount --make-rprivate /
mount -t tmpfs tmpfs /tmp
mkdir /tmp/.run
mount --rbind /run /tmp/.run
mount -t tmpfs tmpfs /run
for e in /tmp/.run/* /tmp/.run/.[!.]*; do
    [ -e "$e" ] || [ -L "$e" ] || continue
    n=/run/${e#/tmp/.run/}
    if [ -L "$e" ]; then cp -P "$e" "$n"
    elif [ -d "$e" ]; then mkdir -p "$n" && mount --rbind "$e" "$n"
    else touch "$n" && mount --bind "$e" "$n"; fi
done
mkdir "$BRIDGE_INSIDE"
mount --bind "$BRIDGE" "$BRIDGE_INSIDE"
mkdir "/tmp/codex-daemon-$(id -u)"
mount --bind "$DAEMON" "/tmp/codex-daemon-$(id -u)"
if [ "$V" = e25 ]; then
    mkdir "$R/empty" && chmod 0555 "$R/empty"
    for p in tmp packages; do
        mount --bind "$R/empty" "$HOME_DIR/.codex/$p"
        mount -o remount,bind,ro "$HOME_DIR/.codex/$p"
    done
fi
# The login, as the sandbox gets it: the one file, read-only (spec.check_credentials). Never read here.
touch "$HOME_DIR/.codex/auth.json"
mount --bind "$LOGIN" "$HOME_DIR/.codex/auth.json"
mount -o remount,bind,ro "$HOME_DIR/.codex/auth.json"
# RUN_INSIDE holds the adapter's run files (the trust script), as the sandbox does.
mkdir /run/hz
"$PY" "$HERE/part2_steps.py" run-files /run/hz
echo "uid=$(id -u)" > "$R/inside"
awk -v b="$BRIDGE_INSIDE" -v h="$HOME_DIR/.codex/" '$5 == b || $5 ~ /^\/tmp\/codex-daemon-/ ||
    index($5, h) == 1 {print $5, $6}' /proc/self/mountinfo >> "$R/inside"

cd "$WT"
ENV="env -i HOME=$HOME_DIR CODEX_HOME=$HOME_DIR/.codex PATH=$CLI/bin:/usr/bin:/bin TERM=xterm-256color"
TRACE="-f -qq -s 300 -xx -e trace=execve,connect,sendto,sendmmsg"
"$PY" "$HERE/part2_steps.py" trust "$SESSION" "$CLI" "$WT" > "$R/trust.txt" 2>&1 || {
    cat "$R/trust.txt"; exit 1; }
# shellcheck disable=SC2086
$ENV strace $TRACE -o "$R/server.strace" $SERVER > "$R/server.log" 2>&1 &
SP=$!
ready=no
for _ in $(seq 1 20); do
    if "$PY" -c 'import sys
from pathlib import Path
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.spec import SessionLayout
sys.exit(0 if Codex().server_ready(SessionLayout(Path(sys.argv[1]))) else 1)' "$SESSION"; then ready=yes; break; fi
    sleep 0.5
done
echo "$ready" > "$R/server_ready"

tm() { TMUX_TMPDIR=/tmp tmux -L s9-10 -f /dev/null "$@" || true; }
# A pane that outlives its TUI, so an early exit leaves its last screen and code to capture.
pane() { tm new-session -d -x 160 -y 48 "$1; echo \"[s9-10: the TUI exited, code \$?]\"; sleep 300"; }
# until <seconds> <text>: wait for <text> on the pane
until_shown() {
    i=0
    while [ "$i" -lt "$1" ]; do
        tm capture-pane -p -t 0 2>/dev/null | command grep -qF -- "$2" && return 0
        sleep 1; i=$((i + 1))
    done
    return 1
}
PROMPT="Reply with only the word pong."
pane "$ENV strace $TRACE -o $R/tui.strace $TUI"
until_shown 60 "›" && echo yes > "$R/composer" || echo no > "$R/composer"
tm send-keys -t 0 -l "$PROMPT"
sleep 1
tm send-keys -t 0 Enter
# The answer is read from the rollout, not the pane (the prompt itself says "pong").
answered=no
for _ in $(seq 1 90); do
    if "$PY" "$HERE/part2_steps.py" answered "$SESSION" 2>/dev/null; then answered=yes; break; fi
    sleep 1
done
echo "$answered" > "$R/answered"
sleep 3
tm capture-pane -p -t 0 > "$R/tui.screen" 2>&1
tm kill-server 2>/dev/null
sleep 1

THREAD=$("$PY" "$HERE/part2_steps.py" thread "$SESSION" 2> "$R/thread.err") || THREAD=
echo "$THREAD" > "$R/thread"
if [ -n "$THREAD" ]; then
    "$PY" "$HERE/generate.py" "$R" "$CLI" --resume "$THREAD" --model "$MODEL" > "$R/generated-resume.txt"
    RESUME=$(get tui_resume "$R/argv-resume.json")
    pane "$ENV strace $TRACE -o $R/tui-resume.strace $RESUME"
    until_shown 60 "$PROMPT" && echo yes > "$R/resumed" || echo no > "$R/resumed"
    sleep 3
    tm capture-pane -p -t 0 > "$R/tui-resume.screen" 2>&1
    tm kill-server 2>/dev/null
fi
kill "$SP" 2>/dev/null || true
wait "$SP" 2>/dev/null || true
for p in tmp packages; do
    find "$HOME_DIR/.codex/$p" -mindepth 1 > "$R/inside.$p" 2>&1 || true
done
