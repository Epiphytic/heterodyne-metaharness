#!/bin/bash
# btq-n27uo probe: on this host's tmux, which failures of the subcommands heterodyne's Tmux wrapper uses
# exit 0? Every server is private: TMUX_TMPDIR is a scratch directory and sockets are `-S` paths under
# it, or `-L hz-probe-*` names inside it. The one case that makes tmux fall back to its default
# directory uses its own `-L` name, and the script kills it by PID. Every server started is killed by
# PID at the end. Prints one TSV row per case: case, exit status, [stdout], stderr (newlines as |).
#
# Usage: probe.sh [SCRATCH]   (default /tmp/hz-tmux-probe; it is removed and recreated)
set -u
B=${1:-/tmp/hz-tmux-probe}; rm -rf "$B"; mkdir -p "$B/tmpdir" "$B/work" "$B/ro"; chmod 500 "$B/ro"
export TMUX_TMPDIR=$B/tmpdir; unset TMUX
pids=()
row() { local name=$1; shift; local out err rc
  out=$("$@" 2> "$B/err"); rc=$?; err=$(tr '\n' '|' < "$B/err"); out=$(printf %s "$out" | tr '\n' '|')
  printf '%s\t%s\t[%s]\t%s\n' "$name" "$rc" "${out:0:40}" "${err:0:100}" | sed -e "s#$B#\$B#g" -e "s#$HOME#~#g"; }
remember() { local p; p=$(tmux "$@" display-message -p '#{pid}' 2>/dev/null) && pids+=("$p"); }
# the production start chain (Tmux.new_session) into the array CHAIN; -P -F (the proposed check) when P=1
chain() { local name=$1 cwd=$2; shift 2; local fmt=(); [ -n "${P:-}" ] && fmt=(-P -F '#{session_name}')
  CHAIN=(start-server \; set-option -g remain-on-exit on \; new-session -d "${fmt[@]}" \
    -s "$name" -x 200 -y 50 -c "$cwd" -- "$@"); }
start() { local sel=$1 arg=$2; shift 2; chain "$@"; tmux "$sel" "$arg" "${CHAIN[@]}"; }
launch() { local sel=$1 arg=$2; shift 2; chain "$@"; env tmux "$sel" "$arg" "${CHAIN[@]}"; }

echo "== A. no server, -S socket directory missing"
row "A1 new-session chain"      start -S "$B/nodir/sock" admin "$B/work" sleep 600
row "A2 has-session"            tmux -S "$B/nodir/sock" has-session -t =admin
row "A3 display-message"        tmux -S "$B/nodir/sock" display-message -p -t =admin: '#{pane_dead}'
row "A4 load-buffer"            sh -c "printf x | tmux -S $B/nodir/sock load-buffer -b hz-1 -"
row "A5 paste-buffer"           tmux -S "$B/nodir/sock" paste-buffer -p -d -b hz-1 -t =admin:
row "A6 send-keys"              tmux -S "$B/nodir/sock" send-keys -t =admin: Enter
row "A7 delete-buffer"          tmux -S "$B/nodir/sock" delete-buffer -b hz-1
row "A8 capture-pane"           tmux -S "$B/nodir/sock" capture-pane -p -J -S -10 -t =admin:
row "A9 kill-session"           tmux -S "$B/nodir/sock" kill-session -t =admin
row "A10 kill-server"           tmux -S "$B/nodir/sock" kill-server
row "A11 chain via a launcher prefix (env)" launch -S "$B/nodir/l.sock" admin "$B/work" sleep 600
echo "== B. no server, -S socket directory not writable"
row "B1 new-session chain"      start -S "$B/ro/sock" admin "$B/work" sleep 600
echo "== C. no server, -L, socket directory present"
for c in "has-session -t =admin" "display-message -p -t =admin: #{pane_dead}" "send-keys -t =admin: Enter" \
         "capture-pane -p -J -S -10 -t =admin:" "delete-buffer -b hz-1" "kill-session -t =admin" "kill-server"; do
  row "C ${c%% *}"              tmux -L hz-probe-none $c; done
echo "== D. -L with TMUX_TMPDIR missing: tmux falls back to its default directory"
row "D1 new-session chain"      env TMUX_TMPDIR=$B/nodir/x bash -c "$(declare -f chain start); start -L hz-probe-fallback admin $B/work sleep 600"
p=$(env -u TMUX_TMPDIR tmux -L hz-probe-fallback display-message -p '#{pid} #{socket_path}' 2>/dev/null)
row "D2 server found in the default directory" echo "${p:+yes, under /tmp/tmux-UID}"
[ -n "$p" ] && { pids+=("${p%% *}"); fallback_sock=${p#* }; }
echo "== E. a running server (-S \$B/ok.sock)"
S="-S $B/ok.sock"
row "E1 new-session chain"      start -S "$B/ok.sock" admin "$B/work" sleep 600; remember $S
row "E2 duplicate session"      start -S "$B/ok.sock" admin "$B/work" sleep 600
row "E3 chain, invalid option"  tmux $S start-server \; set-option -g no-such-option on \; new-session -d -s other -- sleep 600
row "E4 cwd missing"            start -S "$B/ok.sock" nocwd "$B/missing" sleep 600
row "E5 cwd missing: start|current path" tmux $S display-message -p -t =nocwd: '#{pane_start_path}|#{pane_current_path}'
row "E6 argv not found"         start -S "$B/ok.sock" badargv "$B/work" /nonexistent/binary
sleep 0.5
row "E7 argv not found: pane_dead status" tmux $S display-message -p -t =badargv: '#{pane_dead} #{pane_dead_status}'
row "E8 name with a colon"      start -S "$B/ok.sock" 'a:b' "$B/work" sleep 600
row "E9 list-sessions"          tmux $S list-sessions -F '#{session_name}'
row "E10 display-message, missing session" tmux $S display-message -p -t =ghost: '#{pane_dead}'
row "E11 display-message, live pane" tmux $S display-message -p -t =admin: '#{pane_dead}'
row "E12 load-buffer"           sh -c "printf x | tmux $S load-buffer -b hz-1 -"
row "E13 paste-buffer, missing buffer" tmux $S paste-buffer -p -d -b hz-missing -t =admin:
row "E14 paste-buffer, missing session" tmux $S paste-buffer -p -b hz-1 -t =ghost:
row "E15 send-keys, missing session" tmux $S send-keys -t =ghost: Enter
row "E16 send-keys, unknown key name" tmux $S send-keys -t =badargv: NoSuchKeyName
row "E17 capture-pane, missing session" tmux $S capture-pane -p -J -S -10 -t =ghost:
row "E18 delete-buffer, missing" tmux $S delete-buffer -b hz-missing
row "E19 kill-session, missing" tmux $S kill-session -t =ghost
echo "== F. the proposed check: new-session -P -F '#{session_name}'"
export P=1
row "F1 ok"                     start -S "$B/p.sock" admin "$B/work" sleep 600; remember -S "$B/p.sock"
row "F2 duplicate"              start -S "$B/p.sock" admin "$B/work" sleep 600
row "F3 name with a colon"      start -S "$B/p.sock" 'a:b' "$B/work" sleep 600
row "F4 argv exits at once"     start -S "$B/p.sock" quick "$B/work" /bin/true
row "F5 socket directory missing" start -S "$B/nodir/sock" admin "$B/work" sleep 600
row "F6 socket directory not writable" start -S "$B/ro/sock" admin "$B/work" sleep 600
row "F7 via a launcher prefix (env), ok" launch -S "$B/l.sock" admin "$B/work" sleep 600; remember -S "$B/l.sock"
row "F8 via a launcher prefix (env), directory missing" launch -S "$B/nodir/l.sock" admin "$B/work" sleep 600
unset P
echo "== cleanup"
for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; sleep 0.5; chmod 700 "$B/ro"
[ -n "${fallback_sock:-}" ] && [ -S "$fallback_sock" ] && rm -f "$fallback_sock"
left=$(pgrep -f "^tmux (-S $B|-L hz-probe-(none|fallback))" || true)
echo "servers left: ${left:-none}"; echo "tmux $(tmux -V | cut -d' ' -f2)"
