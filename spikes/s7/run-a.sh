#!/bin/sh
# Scenario A: claude -p against the local stub, no network, a dummy API key, a fresh config dir.
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
R=$S7/runA; rm -rf $R; mkdir -p $R/home $R/claude $R/work
cat > $R/claude/settings.json <<J
{"hooks":{
 "StopFailure":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/stopfailure.jsonl"}]}],
 "Stop":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/stop.jsonl"}]}],
 "SessionStart":[{"hooks":[{"type":"command","command":"$D/dump.sh $R/sessionstart.jsonl"}]}]
}}
J
STUB_MODE=429 STUB_LOG=$R/stub.jsonl python3 $D/stub.py $PORT & SP=$!
sleep 0.5
cd $R/work
env -i PATH="$PATH" HOME=$R/home CLAUDE_CONFIG_DIR=$R/claude TERM=xterm \
  ANTHROPIC_API_KEY=dummy-not-a-key ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT \
  CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  timeout 90 claude -p "Reply with OK" --output-format stream-json --verbose --session-id 3f1e7c2a-5b8d-4e6f-9a01-2b3c4d5e6f70 \
  > $R/out.jsonl 2> $R/err.txt < /dev/null
echo "claude rc=$?" > $R/rc.txt
kill $SP
