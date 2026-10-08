#!/bin/sh
# Scenario G: codex exec with a dummy ChatGPT login against the 429 stub; hooks on Stop. No network.
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
. "$D/isolate.sh"
R=$S7/runG; rm -rf $R; mkdir -p $R/home $R/codex $R/work
python3 $D/fakeauth.py $R/codex/auth.json A; chmod 600 $R/codex/auth.json
printf 'chatgpt_base_url = "http://127.0.0.1:%s/backend-api/"\n' "$CXPORT" > $R/codex/config.toml
STUB_LOG=$R/stub.jsonl STUB_MODE=429 python3 $D/cxstub.py $CXPORT & SP=$!
sleep 0.5; cd $R/work
env -i PATH="$PATH" HOME=$R/home CODEX_HOME=$R/codex TERM=xterm timeout 90 codex exec --json --skip-git-repo-check "say OK" </dev/null > $R/out.jsonl 2> $R/err.txt; echo "rc=$?" > $R/rc.txt
kill $SP
