#!/bin/bash
# Codex capabilities 1-4 (run under sandbox.sh): the interactive TUI in a private tmux server against
# cxstub.py, through a custom model provider with a dummy API key, in a scratch CODEX_HOME. MODE:
#   look     launch and capture the screen only (to see which dialogs show)
#   main     fresh workdir, dialogs pre-accepted: SessionStart, picker denial, Stop after it, the answer
#            turn, a shell command in bypass mode, then a restart with `codex resume`.
#   prompts  the same launch without bypass (approval on-request): what a permission prompt fires.
# TRUST=1 (default) pre-trusts the project. HOOKTRUST pre-accepts the hooks: setup (default; cx-trust-hooks.py
# writes hooks.state trusted_hash entries), flag (--dangerously-bypass-hook-trust) or none.
set -u
D=$(cd "$(dirname "$0")" && pwd); . "$D/lib.sh"
MODE=${MODE:-main}; R=$S8_OUT; PORT=18996
mkdir -p "$R/codex" "$R/work"; : > "$R/hooks.jsonl"; : > "$R/stub.jsonl"; rm -f "$R/result.txt"
H="/usr/bin/python3 $D/hook.py $R/hooks.jsonl"
cat > "$R/codex/hooks.json" <<J
{"hooks": {
 "SessionStart": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "Stop": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "PermissionRequest": [{"hooks": [{"type": "command", "command": "$H${ALLOW:+ --allow}"}]}],
 "Interrupt": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "PreToolUse": [{"matcher": "request_user_input", "hooks": [{"type": "command", "command": "$H --deny"}]}]
}}
J
{
cat <<T
model = "gpt-stub"
model_provider = "stub"
[model_providers.stub]
name = "stub"
base_url = "http://127.0.0.1:$PORT/v1"
env_key = "STUB_API_KEY"
wire_api = "responses"
T
[ "${TRUST:-1}" = 1 ] && printf '[projects."%s"]\ntrust_level = "trusted"\n' "$R/work"
} > "$R/codex/config.toml"
[ "${HOOKTRUST:-setup}" = setup ] && CODEX_HOME=$R/codex /usr/bin/python3 "$D/cx-trust-hooks.py" "$R/work" > "$R/hook-trust.txt"
# RETOUCH=1: after trusting, change one hook's command, as admind does when it writes a new launch nonce
[ "${RETOUCH:-0}" = 1 ] && sed -i 's#hooks.jsonl"}]}],$#hooks.jsonl --launch n2"}]}],#' "$R/codex/hooks.json"
STUB_LOG=$R/stub.jsonl /usr/bin/python3 "$D/cxstub.py" $PORT 2>> "$R/stub.err" & sleep 0.5
FLAGS="--dangerously-bypass-approvals-and-sandbox"; [ "$MODE" = prompts ] && FLAGS="--ask-for-approval on-request --sandbox read-only"
[ "${HOOKTRUST:-setup}" = flag ] && FLAGS="$FLAGS --dangerously-bypass-hook-trust"
launch() { # launch first|resume
  local sub=""; [ "$1" = resume ] && sub="resume --last"
  T -f /dev/null new-session -d -s agent -x 200 -y 50 -c "$R/work" \
    "env CODEX_HOME=$R/codex STUB_API_KEY=dummy-not-a-key codex $sub $FLAGS; sleep 600"
}
turn() { # turn LABEL TEXT: paste TEXT, wait for its Stop
  local n; n=$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')
  paste agent "$2"
  for _ in $(seq 100); do [ "$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')" -gt "$n" ] && break; sleep 0.2; done
  sleep 1; snap agent "$1"
}
launch first
if waitfor "$R/hooks.jsonl" '"SessionStart"' 30; then note "SessionStart (first launch) with no terminal input: yes"
else note "SessionStart (first launch) with no terminal input: NO (30 s)"; fi
sleep 3; snap agent 0-start
note "ready prompt on screen with no input: $(grep -q 'Ask Codex to do anything' "$R/0-start.pane.txt" && echo yes || echo no)"
if [ "$MODE" = look ]; then cp "$R/codex/config.toml" "$R/config-after.toml"; T kill-server; exit 0; fi
if [ "$MODE" = prompts ]; then
  touch "$R/escalate"; paste agent "BASH please"; sleep 12; snap agent 1-bash
  note "bash-ran exists: $([ -e "$R/bash-ran" ] && echo yes || echo no)"
  T kill-server; exit 0
fi
turn 1-picker "PICK a deploy target for me"
note "SessionStart events once the first prompt was sent: $(count "$R/hooks.jsonl" '"SessionStart"')"
note "PreToolUse(request_user_input) events: $(count "$R/hooks.jsonl" '"PreToolUse"')"
note "Stop events after the picker turn: $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')"
turn 2-answer "[picker s8p1, answered by alice by reply] production"
note "UserPromptSubmit events: $(count "$R/hooks.jsonl" '"UserPromptSubmit"'), Stop events: $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')"
turn 3-bash "BASH please"
note "bash-ran exists (bypass, no input): $([ -e "$R/bash-ran" ] && echo yes || echo no)"
# interrupt: Esc (admind's !interrupt) while the turn after a denied picker is still running
echo 10 > "$R/delay"; n=$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')
paste agent "PICK again"; sleep 3; T send-keys -t "=agent:" Escape; sleep 1; snap agent 7-interrupt; sleep 12; rm -f "$R/delay"
note "Stop events from the interrupted turn: $(( $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"') - n ))"
note "Interrupt events: $(count "$R/hooks.jsonl" '"hook_event_name": "Interrupt"')"
# restart: kill the CLI's pane, relaunch with `codex resume --last`
T kill-session -t agent; sleep 1
n=$(count "$R/hooks.jsonl" '"SessionStart"'); launch resume
for _ in $(seq 150); do [ "$(count "$R/hooks.jsonl" '"SessionStart"')" -gt "$n" ] && break; sleep 0.2; done
note "SessionStart after restart (codex resume --last), no input: $([ "$(count "$R/hooks.jsonl" '"SessionStart"')" -gt "$n" ] && echo yes || echo NO)"
sleep 3; snap agent 5-restart
turn 6-after "hello again"
note "SessionStart after restart, once a prompt was sent: $(( $(count "$R/hooks.jsonl" '"SessionStart"') - n ))"
note "turn after restart answered: $(count "$R/stub.jsonl" 'hello again')"
T kill-server
