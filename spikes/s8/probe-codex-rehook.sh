#!/bin/bash
# S8 probe, run inside sandbox.sh: does Codex pick up changed hook commands on relaunch? Launch 1's hooks
# log to L1.jsonl. After the session is killed, hooks.json is rewritten so that launch 2's hooks log to
# L2.jsonl (as admind's per-launch nonce changes the command), the new hooks are trusted, and Codex is
# relaunched: with `codex resume --last` (RESUME=1, the default) or as a fresh session (RESUME=0).
# NODAEMON=1 adds --no-daemon, so the TUI runs its own app server instead of the shared daemon.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); . "$D/lib.sh"; R=$S8_OUT; PORT=18996
mkdir -p "$R/codex" "$R/work"; : > "$R/stub.jsonl"; rm -f "$R/result.txt"
config() { # config N: launch N's hooks.json and config.toml, then trust the hooks
  local H="/usr/bin/python3 $D/hook.py $R/L$1.jsonl"
  printf '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "%s"}]}],
 "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "%s"}]}],
 "Stop": [{"hooks": [{"type": "command", "command": "%s"}]}]}}\n' "$H" "$H" "$H" > "$R/codex/hooks.json"
  printf 'model = "gpt-stub"\nmodel_provider = "stub"\n[model_providers.stub]\nname = "stub"\nbase_url = "http://127.0.0.1:%s/v1"\nenv_key = "STUB_API_KEY"\nwire_api = "responses"\n[projects."%s"]\ntrust_level = "trusted"\n' \
    "$PORT" "$R/work" > "$R/codex/config.toml"
  CODEX_HOME=$R/codex /usr/bin/python3 "$D/cx-trust-hooks.py" "$R/work" > "$R/hook-trust-$1.txt"
}
launch() {
  T -f /dev/null new-session -d -s agent -x 200 -y 50 -c "$R/work" \
    "env CODEX_HOME=$R/codex STUB_API_KEY=dummy-not-a-key codex $1 --dangerously-bypass-approvals-and-sandbox${NODAEMON:+ --no-daemon}; sleep 600"
}
STUB_LOG=$R/stub.jsonl /usr/bin/python3 "$D/cxstub.py" $PORT 2>> "$R/stub.err" & sleep 0.5
config 1; launch ""; sleep 6
paste agent "hello one"; waitfor "$R/L1.jsonl" '"Stop"' 20; sleep 1
T kill-session -t agent; sleep 2
note "codex processes left after kill-session: $(pgrep -c -x codex || true)"; ps -eo pid,ppid,args | grep -v "ps -eo" | cut -c1-160 > "$R/ps-after-kill.txt"
config 2
if [ "${RESUME:-1}" = 1 ]; then launch "resume --last"; else launch ""; fi
sleep 6; snap agent relaunched
paste agent "hello two"; sleep 8; snap agent after
note "relaunch: $([ "${RESUME:-1}" = 1 ] && echo 'codex resume --last' || echo fresh)"
note "launch 2 prompt answered: $(count "$R/stub.jsonl" 'hello two')"
note "L1 events in total: $(wc -l < "$R/L1.jsonl"); L1 events naming 'hello two': $(count "$R/L1.jsonl" 'hello two')"
note "L2 events in total: $(wc -l < "$R/L2.jsonl" 2>/dev/null || echo 0)"
T kill-server
