#!/bin/sh
# Scenario C: one synthetic config dir; a session under dummy login A, resumed under dummy login B,
# then a handoff relaunch under B with a generation-specific session ID. Stub in "ok" mode, no network.
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
R=$S7/runC; rm -rf $R; mkdir -p $R/home $R/claude $R/work
STUB_MODE=ok STUB_LOG=$R/stub.jsonl python3 $D/stub.py $PORT & SP=$!
sleep 0.5; cd $R/work
G1=5d0c2e3f-0a1b-4c2d-8e3f-4a5b6c7d8e9f
G2=$(python3 -c "import uuid; print(uuid.uuid5(uuid.UUID('$G1'), '$G1:2'))")
echo "$G2" > $R/g2.txt
run() { tok=$1; shift; echo "--- token $tok: $*" >> $R/stub.jsonl
  env -i PATH="$PATH" HOME=$R/home CLAUDE_CONFIG_DIR=$R/claude TERM=xterm CLAUDE_CODE_OAUTH_TOKEN=dummy-login-$tok \
    ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    timeout 60 claude -p "$@" --output-format json < /dev/null > $R/out-$tok-$(date +%s%N).json 2>>$R/err.txt; echo "rc=$?" >> $R/stub.jsonl; }
run A --session-id $G1 "Remember the word FLAMINGO. Reply OK."
run B --resume $G1 "What was the word?"
run B --session-id $G2 "Handoff: continue the task."
kill $SP
