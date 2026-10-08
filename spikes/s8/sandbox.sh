#!/bin/bash
# Runs COMMAND for an S8 probe in a bubblewrap sandbox: no network but loopback, private pid and ipc
# namespaces, and only /usr, /etc, the two CLIs' install directories, spikes/ (read-only) and RUN_DIR
# (read-write) visible, plus src/, tests/fakes/ and .venv/ read-only for e2e.py. HOME and /tmp are
# tmpfs, so no real login, agent home or Marmot home exists inside. The environment is rebuilt from
# an allowlist. Everything started inside, tmux server included, dies with the sandbox. Refuses to run
# (rc 2) unless the network namespace has only lo.
#
# Usage: sandbox.sh RUN_DIR -- COMMAND...   (RUN_DIR must be under /tmp; it becomes $S8_OUT)
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
SPIKES=$(dirname "$D"); REPO=$(dirname "$SPIKES")
[ $# -ge 3 ] && [ "$2" = -- ] || { sed -n '/^# Usage:/p' "$0" >&2; exit 2; }
mkdir -p "$1"; R=$(readlink -f "$1"); shift 2
case $R in /tmp/?*) ;; *) echo "sandbox.sh: RUN_DIR must be under /tmp" >&2; exit 2 ;; esac
binds=()
for cli in claude codex; do
  real=$(readlink -f "$(command -v "$cli")"); pkg=$(dirname "$(dirname "$real")")
  binds+=(--ro-bind "$pkg" "$pkg" --symlink "$real" "/opt/s8/bin/$cli")
done
exec bwrap --die-with-parent --unshare-pid --unshare-ipc --unshare-net --unshare-uts \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --symlink usr/sbin /sbin --ro-bind /etc /etc --proc /proc --dev /dev --tmpfs /tmp --tmpfs "$HOME" \
  --dir /opt/s8/bin "${binds[@]}" --ro-bind "$SPIKES" "$SPIKES" \
  --ro-bind "$REPO/src" "$REPO/src" --ro-bind "$REPO/tests/fakes" "$REPO/tests/fakes" \
  --ro-bind-try "$REPO/.venv" "$REPO/.venv" \
  --bind "$R" "$R" --chdir "$R" \
  /usr/bin/env -i PATH=/opt/s8/bin:/usr/bin:/bin HOME="$R/home" TERM=xterm-256color LANG=C.UTF-8 \
  S8_OUT="$R" S8_REPO="$REPO" \
  /bin/bash -c 'if awk "NR > 2 && \$1 != \"lo:\" { bad = 1 } END { exit !bad }" /proc/net/dev; then
      echo "sandbox.sh: a non-loopback interface is present; refusing to run" >&2; exit 2; fi
    mkdir -p "$S8_OUT/home"; exec "$@"' s8 "$@"
