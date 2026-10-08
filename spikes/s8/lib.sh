# Helpers for the S8 probes, sourced inside sandbox.sh. tmux runs on its own socket (-L s8spike) and,
# like everything else, dies with the sandbox.
T() { tmux -L s8spike "$@"; }
# paste NAME TEXT: admind's paste (heterodyne.tmux.Tmux.paste): bracketed paste, a pause, then Enter.
paste() { printf %s "$2" | T load-buffer -b s8 -; T paste-buffer -p -d -b s8 -t "=$1:"; sleep 0.3; T send-keys -t "=$1:" Enter; }
# waitfor FILE PATTERN SECONDS: wait until FILE has a line matching PATTERN; rc 1 on timeout.
waitfor() { local i=0; while [ $i -lt $(($3 * 10)) ]; do grep -q -- "$2" "$1" 2>/dev/null && return 0; sleep 0.1; i=$((i + 1)); done; return 1; }
count() { grep -c -- "$2" "$1" 2>/dev/null || true; }
snap() { T capture-pane -p -J -t "=$1:" > "$S8_OUT/$2.pane.txt"; }
note() { echo "$*" | tee -a "$S8_OUT/result.txt"; }
