#!/bin/sh
# S9-10, inside the run's private namespaces: production's socket binds, then the server and a new-session TUI.
#   inner.sh <run-dir> <store-cli> <plain|e25> <python>
set -eu
R=$1; CLI=$2; V=$3; PY=$4
get() { "$PY" -c 'import json,shlex,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]
print(shlex.join(v) if isinstance(v, list) else v)' "$R/argv.json" "$1"; }
HOME_DIR=$(get home); BRIDGE=$(get bridge); DAEMON=$(get daemon); WT=$(get worktree)
BRIDGE_INSIDE=$(get bridge_inside); SERVER=$(get server); TUI=$(get tui_new); SESSION=$(get session)

mount --make-rprivate /
# A private /tmp, so the daemon rendezvous can't meet the host's own Codex or an earlier run's.
mount -t tmpfs tmpfs /tmp
# BRIDGE_INSIDE's parent (/run) isn't writable here: a private tmpfs over it, the host's entries bound back.
mkdir /tmp/.run
mount --rbind /run /tmp/.run
mount -t tmpfs tmpfs /run
for e in /tmp/.run/* /tmp/.run/.[!.]*; do
    [ -e "$e" ] || [ -L "$e" ] || continue
    n=/run/${e#/tmp/.run/}
    if [ -L "$e" ]; then cp -P "$e" "$n"
    elif [ -d "$e" ]; then mkdir "$n" && mount --rbind "$e" "$n"
    else touch "$n" && mount --bind "$e" "$n"; fi
done
# The two binds plan 4's Codex.facts declares.
mkdir "$BRIDGE_INSIDE"
mount --bind "$BRIDGE" "$BRIDGE_INSIDE"
mkdir "/tmp/codex-daemon-$(id -u)"
mount --bind "$DAEMON" "/tmp/codex-daemon-$(id -u)"
if [ "$V" = e25 ]; then                                 # E25: one fresh empty directory, read-only at each
    mkdir "$R/empty" && chmod 0555 "$R/empty"
    for p in tmp packages; do
        mount --bind "$R/empty" "$HOME_DIR/.codex/$p"
        mount -o remount,bind,ro "$HOME_DIR/.codex/$p"
    done
fi
echo "uid=$(id -u)" > "$R/inside"
awk -v b="$BRIDGE_INSIDE" -v h="$HOME_DIR/.codex/" '$5 == b || $5 ~ /^\/tmp\/codex-daemon-/ ||
    index($5, h) == 1 {print $5, $6}' /proc/self/mountinfo >> "$R/inside"

cd "$WT"
# §4.5's environment: HOME, CODEX_HOME, PATH, TERM and nothing else (tool_env names no values; see the doc).
run() { env -i HOME="$HOME_DIR" CODEX_HOME="$HOME_DIR/.codex" PATH="$CLI/bin:/usr/bin:/bin" \
    TERM=xterm-256color "$@"; }
TRACE="-f -qq -s 300 -xx -e trace=execve,connect,sendto,sendmmsg"
# shellcheck disable=SC2086
run strace $TRACE -o "$R/server.strace" $SERVER > "$R/server.log" 2>&1 &
SP=$!
ready=no
for _ in $(seq 1 20); do
    if "$PY" -c 'import sys
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.spec import SessionLayout
sys.exit(0 if Codex().server_ready(SessionLayout(sys.argv[1])) else 1)' "$SESSION"; then ready=yes; break; fi
    sleep 0.5
done
echo "$ready" > "$R/server_ready"
# shellcheck disable=SC2086
run timeout -k 2 18 strace $TRACE -o "$R/tui.strace" script -qfec "$TUI" "$R/tui.screen" </dev/null \
    > /dev/null 2>&1 || true
kill "$SP" 2>/dev/null || true
wait "$SP" 2>/dev/null || true
for p in tmp packages; do
    find "$HOME_DIR/.codex/$p" -mindepth 1 > "$R/inside.$p" 2>&1 || true
done
