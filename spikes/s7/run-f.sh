#!/bin/sh
# Scenario F: Codex app-server with a dummy ChatGPT login and chatgpt_base_url at the local stub. No network.
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
. "$D/isolate.sh"
R=$S7/runF${EXPIRES_IN:+-exp$EXPIRES_IN}; rm -rf $R; mkdir -p $R/home $R/codex $R/work
python3 $D/fakeauth.py $R/codex/auth.json A ${EXPIRES_IN:-86400}; chmod 600 $R/codex/auth.json
printf 'chatgpt_base_url = "http://127.0.0.1:%s/backend-api/"\n' "$CXPORT" > $R/codex/config.toml
sha256sum $R/codex/auth.json | cut -c1-16 > $R/auth-before.txt
STUB_LOG=$R/stub.jsonl python3 $D/cxstub.py $CXPORT & SP=$!
sleep 0.5; cd $R/work
env -i PATH="$PATH" HOME=$R/home CODEX_HOME=$R/codex TERM=xterm AS_ERR=$R/as-err.txt timeout 60 python3 $D/appserver.py account/rateLimits/read > $R/as-out.txt 2>&1
sha256sum $R/codex/auth.json | cut -c1-16 > $R/auth-after.txt
kill $SP
