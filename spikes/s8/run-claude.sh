#!/bin/bash
# Claude Code capabilities 1-4 (run under sandbox.sh). Launches exactly as admind does (agent.py:
# --session-id, --permission-mode bypassPermissions, --settings, --name) in a private tmux server,
# against clstub.py with a dummy login. MODE picks the scenario:
#   main     fresh workdir, dialogs pre-accepted: SessionStart, picker denial, Stop after it, the answer
#            turn, Bash and an out-of-workdir Write in bypass mode, then a restart with --resume.
#   prompts  the same launch in default mode instead of bypass: what a permission prompt fires (Notification,
#            PermissionRequest), and whether PermissionRequest can return the decision (ALLOW=1).
#   dialog   one pre-acceptance key left out (DROP=onboarding|trust|bypass): what shows, and whether
#            SessionStart fires with no input.
set -u
D=$(cd "$(dirname "$0")" && pwd); . "$D/lib.sh"
MODE=${MODE:-main}; R=$S8_OUT; PORT=18997; SID=5e8a0c1e-0000-4000-8000-00000000c1a1
mkdir -p "$R/claude" "$R/work"; : > "$R/hooks.jsonl"; : > "$R/stub.jsonl"; rm -f "$R/result.txt"
H="/usr/bin/python3 $D/hook.py $R/hooks.jsonl"
PR=""; [ "$MODE" = prompts ] && PR=", \"PermissionRequest\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"$H${ALLOW:+ --allow}\"}]}]"
cat > "$R/hook-settings.json" <<J
{"hooks": {
 "SessionStart": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "Stop": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "StopFailure": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "Notification": [{"hooks": [{"type": "command", "command": "$H"}]}],
 "PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "$H --deny"}]}]$PR
}}
J
# Pre-acceptance through the CLI's own configuration: onboarding, workspace trust, bypass-mode acceptance.
/usr/bin/python3 - "$R" "${DROP:-}" <<'P'
import json, sys
R, drop = sys.argv[1], sys.argv[2]
cfg = {"hasCompletedOnboarding": True, "theme": "dark", "bypassPermissionsModeAccepted": True,
       "projects": {R + "/work": {"hasTrustDialogAccepted": True}}}
if drop == "onboarding": del cfg["hasCompletedOnboarding"], cfg["theme"]
if drop == "trust": cfg["projects"] = {}
if drop == "bypass": del cfg["bypassPermissionsModeAccepted"]
json.dump(cfg, open(R + "/claude/.claude.json", "w"))
# a dummy subscription login (fake tokens; the stub never checks them)
json.dump({"claudeAiOauth": {"accessToken": "dummy-access", "refreshToken": "dummy-refresh",
           "expiresAt": 4102444800000, "scopes": ["user:inference", "user:profile"],
           "subscriptionType": "max"}}, open(R + "/claude/.credentials.json", "w"))
P
chmod 600 "$R/claude/.credentials.json"
STUB_LOG=$R/stub.jsonl /usr/bin/python3 "$D/clstub.py" $PORT 2>> "$R/stub.err" & sleep 0.5
PM="--permission-mode bypassPermissions"; [ "$MODE" = prompts ] && PM="--permission-mode default"
launch() { # launch FIRST|resume
  local how="--session-id"; [ "$1" = resume ] && how="--resume"
  T -f /dev/null new-session -d -s agent -x 200 -y 50 -c "$R/work" \
    "env CLAUDE_CONFIG_DIR=$R/claude ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude $how $SID $PM --settings $R/hook-settings.json --name admin-agent; sleep 600"
}
turn() { # turn LABEL TEXT: paste TEXT, wait for its Stop
  local n; n=$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')
  paste agent "$2"
  for _ in $(seq 150); do [ "$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')" -gt "$n" ] && break; sleep 0.2; done
  sleep 1; snap agent "$1"
}
launch first
if waitfor "$R/hooks.jsonl" '"SessionStart"' 30; then note "SessionStart (first launch) with no terminal input: yes"
else note "SessionStart (first launch) with no terminal input: NO (30 s)"; fi
sleep 3; snap agent 0-start
if [ "$MODE" = dialog ]; then T kill-server; exit 0; fi
if [ "$MODE" = prompts ]; then
  paste agent "BASH please"; sleep 12; snap agent 1-bash
  note "bash-ran exists: $([ -e "$R/bash-ran" ] && echo yes || echo no)"
  T kill-server; exit 0
fi
turn 1-picker "PICK a deploy target for me"
note "PreToolUse(AskUserQuestion) events: $(count "$R/hooks.jsonl" '"PreToolUse"')"
note "Stop events after the picker turn: $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')"
turn 2-answer "[picker s8p1, answered by alice by reply] production"
note "UserPromptSubmit events: $(count "$R/hooks.jsonl" '"UserPromptSubmit"'), Stop events: $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')"
turn 3-bash "BASH please"
note "bash-ran exists (bypass, no input): $([ -e "$R/bash-ran" ] && echo yes || echo no)"
turn 4-editout "EDITOUT please"
note "written.txt exists (bypass, no input): $([ -e "$R/written.txt" ] && echo yes || echo no)"
note "Notification events: $(count "$R/hooks.jsonl" '"Notification"')"
# interrupt: Esc (admind's !interrupt) while the turn after a denied picker is still running
echo 10 > "$R/delay"; n=$(count "$R/hooks.jsonl" '"hook_event_name": "Stop"')
paste agent "PICK again"; sleep 3; T send-keys -t "=agent:" Escape; sleep 1; snap agent 7-interrupt; sleep 12; rm -f "$R/delay"
note "Stop events from the interrupted turn: $(( $(count "$R/hooks.jsonl" '"hook_event_name": "Stop"') - n ))"
# restart: kill the CLI's pane, relaunch with --resume as admind does once the ID has been seen
T kill-session -t agent; sleep 1
n=$(count "$R/hooks.jsonl" '"SessionStart"'); launch resume
for _ in $(seq 150); do [ "$(count "$R/hooks.jsonl" '"SessionStart"')" -gt "$n" ] && break; sleep 0.2; done
note "SessionStart after restart (--resume), no input: $([ "$(count "$R/hooks.jsonl" '"SessionStart"')" -gt "$n" ] && echo yes || echo NO)"
sleep 3; snap agent 5-restart
turn 6-after "hello again"
note "turn after restart answered: $(count "$R/stub.jsonl" 'hello again')"
T kill-server
