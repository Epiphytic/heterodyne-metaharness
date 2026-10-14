#!/bin/sh
# S9-10, inside the run's private namespaces: production's socket binds, then the server and the TUI.
#   inner.sh <run-dir> <store-cli> <plain|e25> [tui_new|tui_resume]
set -eu
R=$1; CLI=$2; V=$3; TUI_KEY=${4:-tui_new}
get() { python3 -c 'import json,shlex,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]
print(shlex.join(v) if isinstance(v, list) else v)' "$R/argv.json" "$1"; }
HOME_DIR=$(get home); BRIDGE=$(get bridge); DAEMON=$(get daemon); WT=$(get worktree)
BRIDGE_INSIDE=$(get bridge_inside); SERVER=$(get server); TUI=$(get "$TUI_KEY")

mount --make-rprivate /
# A private /tmp, so the daemon rendezvous can't meet the host's own Codex or an earlier run's.
mount -t tmpfs tmpfs /tmp
# /run gets BRIDGE_INSIDE: a private tmpfs over it, with each of the host's /run entries bound back.
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
mkdir -p "$HOME_DIR/.codex/tmp" "$HOME_DIR/.codex/packages"
if [ "$V" = e25 ]; then                                 # E25: one fresh empty directory, read-only at each
    mkdir "$R/empty" && chmod 0555 "$R/empty"
    for p in tmp packages; do
        mount --bind "$R/empty" "$HOME_DIR/.codex/$p"
        mount -o remount,bind,ro "$HOME_DIR/.codex/$p"
    done
fi

cd "$WT"
run() { env -i HOME="$HOME_DIR" CODEX_HOME="$HOME_DIR/.codex" PATH="$CLI/bin:/usr/bin:/bin" \
    TERM=xterm-256color "$@"; }
TRACE="-f -qq -s 300 -xx -e trace=execve,connect,sendto,sendmmsg"
# shellcheck disable=SC2086
run strace $TRACE -o "$R/server.strace" $SERVER > "$R/server.log" 2>&1 &
SP=$!
ready=no
for _ in $(seq 1 40); do
    if [ -S "$BRIDGE/app.sock" ] || find "$DAEMON" -maxdepth 1 -type s | grep -q .; then ready=yes; break; fi
    sleep 0.5
done
echo "$ready" > "$R/server_ready"
# shellcheck disable=SC2086
run timeout -k 3 25 strace $TRACE -o "$R/tui.strace" script -qfec "$TUI" "$R/tui.screen" </dev/null \
    > /dev/null 2>&1 || true
kill "$SP" 2>/dev/null || true
wait "$SP" 2>/dev/null || true
find "$HOME_DIR/.codex/tmp" "$HOME_DIR/.codex/packages" -mindepth 1 > "$R/exec_dirs.inside" 2>&1 || true
