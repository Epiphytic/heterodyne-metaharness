#!/bin/bash
# Launcher for the real-account runs (L1-L6 in docs/spikes/s7-accounts-and-usage.md). Runs one CLI in a
# bubblewrap sandbox that sees only /usr, /etc, the CLI's own install, this directory, one synthetic home
# and at most one login file. HOME is a tmpfs, so the caller's real logins and every other home are
# absent. The environment is rebuilt from an allowlist, so no inherited token or endpoint variable gets in.
# The network is shared, because real runs need it. NONET=1 adds --unshare-net for offline checks.
#
# Usage: sandbox.sh claude|codex LOGIN_FILE|- STATE_DIR [ro|rw] [-- COMMAND...]
#   STATE_DIR is the synthetic home: STATE_DIR/home is HOME, STATE_DIR/claude is CLAUDE_CONFIG_DIR (or
#   STATE_DIR/codex is CODEX_HOME), and STATE_DIR/work is the working directory.
#   LOGIN_FILE is bound at STATE_DIR/claude/.credentials.json (or STATE_DIR/codex/auth.json), read-only
#   unless "rw". With "-" nothing is bound, so a login made inside lands in STATE_DIR itself (L1).
#   COMMAND defaults to the CLI with no arguments.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 3 ] || { sed -n '8,13p' "$0" >&2; exit 2; }
cli=$1 login=$2 state=$3; shift 3
mode=ro; case "${1:-}" in ro | rw) mode=$1; shift ;; esac
[ "${1:-}" = -- ] && shift
case $cli in
  claude) cfg=claude target=.credentials.json var=CLAUDE_CONFIG_DIR ;;
  codex) cfg=codex target=auth.json var=CODEX_HOME ;;
  *) echo "sandbox.sh: unknown CLI $cli" >&2; exit 2 ;;
esac
[ $# -gt 0 ] || set -- "$cli"
real=$(readlink -f "$(command -v "$cli")")   # the installed binary; its package dir is bound read-only
pkg=$(dirname "$(dirname "$real")")
mkdir -p "$state/home" "$state/$cfg" "$state/work"
state=$(readlink -f "$state")
binds=(--bind "$state" "$state")
if [ "$login" != - ]; then
  login=$(readlink -f "$login")
  case $login in "$state"/*) echo "sandbox.sh: the login file must live outside STATE_DIR" >&2; exit 2 ;; esac
  [ -e "$state/$cfg/$target" ] || : > "$state/$cfg/$target"   # bind target
  if [ "$mode" = rw ]; then binds+=(--bind "$login" "$state/$cfg/$target")
  else binds+=(--ro-bind "$login" "$state/$cfg/$target"); fi
fi
net=(); [ -n "${NONET:-}" ] && net=(--unshare-net)
exec bwrap --die-with-parent --unshare-pid --unshare-ipc "${net[@]}" \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --symlink usr/sbin /sbin --ro-bind /etc /etc --ro-bind-try /run/systemd/resolve /run/systemd/resolve \
  --proc /proc --dev /dev --tmpfs /tmp --tmpfs "$HOME" \
  --ro-bind "$pkg" "$pkg" --dir /opt/s7/bin --symlink "$real" "/opt/s7/bin/$cli" --ro-bind "$D" "$D" \
  "${binds[@]}" --chdir "$state/work" \
  /usr/bin/env -i PATH=/opt/s7/bin:/usr/bin:/bin HOME="$state/home" "$var=$state/$cfg" \
  TERM="${TERM:-xterm}" LANG=C.UTF-8 "$@"
