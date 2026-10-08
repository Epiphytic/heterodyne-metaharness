#!/bin/sh
# Scenario B: interactive claude in a private tmux server against the local stub, no network.
# AUTH=apikey or AUTH=oauth (a dummy token; the stub never checks it).
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
R=$S7/runB-$AUTH-${MODE:-429}; rm -rf $R; mkdir -p $R/home $R/claude $R/work
cat > $R/claude/settings.json <<J
{"statusLine":{"type":"command","command":"$D/dump.sh $R/statusline.jsonl"},
 "hooks":{
 "StopFailure":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/stopfailure.jsonl"}]}],
 "Stop":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/stop.jsonl"}]}],
 "SessionStart":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/sessionstart.jsonl"}]}],
 "UserPromptSubmit":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/ups.jsonl"}]}]
}}
J
python3 - $R <<'P'
import json,sys
R=sys.argv[1]
json.dump({"hasCompletedOnboarding":True,"theme":"dark","bypassPermissionsModeAccepted":True,
           "projects":{R+"/work":{"hasTrustDialogAccepted":True}}},open(R+"/claude/.claude.json","w"))
P
STUB_MODE=${MODE:-429} STUB_LOG=$R/stub.jsonl python3 $D/stub.py $PORT & SP=$!
sleep 0.5
if [ "$AUTH" = oauth ]; then A="CLAUDE_CODE_OAUTH_TOKEN=dummy-not-a-token"; else A="ANTHROPIC_API_KEY=dummy-not-a-key"; fi
tmux -S $R/tmux.sock -f /dev/null new-session -d -s c -x 200 -y 50 -c $R/work \
  "env -i PATH='$PATH' HOME=$R/home CLAUDE_CONFIG_DIR=$R/claude TERM=xterm-256color $A ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 claude --session-id 7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d; sleep 600"
tmux -S $R/tmux.sock display -p '#{pid}' > $R/tmux.pid
sleep 12
tmux -S $R/tmux.sock capture-pane -p -t c > $R/pane0.txt
tmux -S $R/tmux.sock send-keys -t c "Reply with OK" ; sleep 1; tmux -S $R/tmux.sock send-keys -t c Enter
sleep 15
tmux -S $R/tmux.sock capture-pane -p -t c > $R/pane1.txt
kill $(cat $R/tmux.pid); kill $SP
