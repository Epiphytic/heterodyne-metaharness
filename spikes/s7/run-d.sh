#!/bin/sh
# Scenario D: login binding through CLAUDE_CONFIG_DIR. A dummy .credentials.json (fake tokens) is
# bound read-only; the stub logs a digest of the bearer it receives. Accounts A and B side by side,
# plus A2: account A's file with an access token that has already expired. No network.
set -u
D=$(cd "$(dirname "$0")" && pwd); S7=${S7:-/tmp/s7}; PORT=${PORT:-18999}; CXPORT=${CXPORT:-18998}
R=$S7/runD; rm -rf $R; mkdir -p $R
STUB_MODE=ok STUB_LOG=$R/stub.jsonl python3 $D/stub.py $PORT & SP=$!
sleep 0.5
mk() { # name access expires_ms
  mkdir -p $R/login-$1 $R/$1/home $R/$1/claude $R/$1/work
  python3 -c "import json,sys; json.dump({'claudeAiOauth':{'accessToken':'$2','refreshToken':'dummy-refresh-$1','expiresAt':$3,'scopes':['user:inference','user:profile'],'subscriptionType':'max'}},open('$R/login-$1/.credentials.json','w'))"
  chmod 600 $R/login-$1/.credentials.json
  python3 -c "import json; json.dump({'hasCompletedOnboarding':True,'projects':{'$R/$1/work':{'hasTrustDialogAccepted':True}}},open('$R/$1/claude/.claude.json','w'))"
  touch $R/$1/claude/.credentials.json   # bind target
}
FUT=$(python3 -c "import time; print(int((time.time()+86400)*1000))"); PAST=$(python3 -c "import time; print(int((time.time()-3600)*1000))")
mk A dummy-access-A $FUT; mk B dummy-access-B $FUT; mk A2 dummy-access-A2 $PAST
for n in A B A2; do
  sha256sum $R/login-$n/.credentials.json | cut -c1-16 > $R/$n/cred-before.txt; stat -c %Y $R/login-$n/.credentials.json >> $R/$n/cred-before.txt
  echo "--- $n" >> $R/stub.jsonl
  bwrap --dev-bind / / --ro-bind $R/login-$n/.credentials.json $R/$n/claude/.credentials.json --chdir $R/$n/work \
    env -i PATH="$PATH" HOME=$R/$n/home CLAUDE_CONFIG_DIR=$R/$n/claude TERM=xterm \
      ANTHROPIC_BASE_URL=http://127.0.0.1:$PORT CLAUDE_CODE_MAX_RETRIES=0 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
      sh -c 'claude auth status --json > ../auth-status.json 2>&1; timeout 60 claude -p "Reply OK" --output-format json > ../out.json 2> ../err.txt; echo "rc=$?" > ../rc.txt; find "$CLAUDE_CONFIG_DIR" -maxdepth 1 -newer ../auth-status.json -printf "%f\n" | sort > ../written.txt' < /dev/null
  sha256sum $R/login-$n/.credentials.json | cut -c1-16 > $R/$n/cred-after.txt; stat -c %Y $R/login-$n/.credentials.json >> $R/$n/cred-after.txt
done
kill $SP
