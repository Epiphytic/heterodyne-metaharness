#!/bin/bash
# Copies the S8 runs' key outputs from $S8 (default /tmp/s8) into spikes/s8/evidence/, masked: 64+ hex
# values (message and account IDs, hook trust hashes) become <hex>, and the repository and sandbox run
# paths become $REPO and $R. Hook payloads keep only the fields the write-up cites.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); REPO=$(dirname "$(dirname "$D")"); S8=${S8:-/tmp/s8}; E=$D/evidence
rm -rf "$E"; mkdir -p "$E"
mask() { sed -E -e "s#$REPO#\$REPO#g" -e "s#$S8/[a-z0-9-]+#\$R#g" -e "s#$HOME#~#g" \
  -e 's/(sha256:)?[0-9a-f]{64,}/<hex>/g' -e 's/[[:space:]]+$//'; }
hooks() { # the cited fields of each hook event
  /usr/bin/python3 -c '
import json, sys
keep = ("hook_event_name", "source", "prompt", "tool_name", "tool_input", "tool_use_id", "message",
        "notification_type", "permission_suggestions", "stop_hook_active", "permission_mode")
for line in open(sys.argv[1]):
    e = json.loads(line)
    print(json.dumps({k: e[k] for k in keep if k in e}))' "$1"
}
for d in c-main c-prompts c-prompts-allow c-drop-onboarding c-drop-trust c-drop-bypass \
         x-main x-prompts x-prompts-allow x-notrust x-flag-notrust x-hookflag x-retouch \
         x-rehook-1 x-rehook-0 x-rehook-nd t-attach; do
  [ -d "$S8/$d" ] || continue
  mkdir -p "$E/$d"
  for f in result.txt hook-trust.txt hook-trust-1.txt hook-trust-2.txt; do
    [ -f "$S8/$d/$f" ] && mask < "$S8/$d/$f" > "$E/$d/$f"; done
  for f in hooks.jsonl L1.jsonl L2.jsonl; do
    [ -s "$S8/$d/$f" ] && hooks "$S8/$d/$f" | mask > "$E/$d/$f"; done
  for f in "$S8/$d"/*.pane.txt; do [ -f "$f" ] && mask < "$f" | cat -s > "$E/$d/$(basename "$f")"; done
done
cp "$S8/x-rehook-ps/ps-after-kill.txt" "$E/x-rehook-1/" 2>/dev/null && mask < "$E/x-rehook-1/ps-after-kill.txt" \
  | cut -c1-200 > "$E/x-rehook-1/ps.tmp" && mv "$E/x-rehook-1/ps.tmp" "$E/x-rehook-1/ps-after-kill.txt"
for a in claude codex codex-nd; do
  [ -f "$S8/e2e-$a.out" ] || continue
  grep -v '^WARNING: proceeding' "$S8/e2e-$a.out" | mask > "$E/e2e-$a.txt"
  [ -f "$S8/e2e-$a/e2e.json" ] && mask < "$S8/e2e-$a/e2e.json" > "$E/e2e-$a.json"
  for f in "$S8/e2e-$a"/*.pane.txt; do [ -f "$f" ] && mask < "$f" | cat -s > "$E/e2e-$a-$(basename "$f")"; done
done
# fail closed if anything that looks like a secret, a home path or an address:port survived
if grep -rEn '[0-9a-f]{64}|/home/|nsec1|npub1|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+' "$E"; then
  echo "collect-evidence.sh: unmasked value above" >&2; exit 1; fi
du -sh "$E"
