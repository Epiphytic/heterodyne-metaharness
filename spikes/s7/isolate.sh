# Sourced first by every run-*.sh: the scenarios must never reach a real provider. Re-execs the calling
# script in a fresh network namespace (loopback only) and refuses to go on unless it is in one.
if [ "${S7_NETNS:-}" != 1 ]; then
  command -v bwrap >/dev/null || { echo "s7: bwrap not found; refusing to run without network isolation" >&2; exit 2; }
  S7_NETNS=1 exec bwrap --dev-bind / / --unshare-net --die-with-parent -- /bin/sh "$0" "$@"
fi
# /proc/net/dev lists the interfaces of this process's own namespace.
if awk 'NR > 2 && $1 != "lo:" { bad = 1 } END { exit !bad }' /proc/net/dev; then
  echo "s7: a non-loopback interface is present; refusing to run" >&2; exit 2
fi
