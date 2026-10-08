#!/bin/bash
# S8 probe, run inside sandbox.sh: does admind's tmux server report a client attaching and detaching?
# Sets global client-attached and client-detached hooks that append to a file, then attaches a client
# through `script` (a pseudo-terminal, as a person's `tmux attach` would have), detaches it with
# `detach-client`, and attaches and kills a second client by PID.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); . "$D/lib.sh"; R=$S8_OUT; rm -f "$R/result.txt" "$R/attach.log"
T -f /dev/null new-session -d -s agent -x 120 -y 30 "sleep 600"
T set-hook -g client-attached "run-shell 'echo attached #{client_name} #{client_pid} >> $R/attach.log'"
T set-hook -g client-detached "run-shell 'echo detached #{client_name} #{client_pid} >> $R/attach.log'"
script -qfc "tmux -L s8spike attach -t agent" /dev/null > /dev/null & sleep 2
note "clients while attached: $(T list-clients -F '#{client_pid}' | wc -l)"
T detach-client -s agent; sleep 1
script -qfc "tmux -L s8spike attach -t agent" /dev/null > /dev/null & sp=$!; sleep 2
kill "$sp"; sleep 1
c=$(T list-clients -F '#{client_pid}'); [ -n "$c" ] && kill $c; sleep 1
note "events: $(tr '\n' ';' < "$R/attach.log" | sed 's/[0-9]\{2,\}/N/g')"
note "client-attached events: $(count "$R/attach.log" '^attached'), client-detached events: $(count "$R/attach.log" '^detached')"
T kill-server
