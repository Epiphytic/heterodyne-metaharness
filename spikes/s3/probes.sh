#!/bin/sh
# Usage (inside sandbox): probes.sh <real-home> <token>. Prints one PASS/FAIL line per probe (7 from the plan, plus 1 added: host-env-not-inherited).
REAL_HOME=$1; TOKEN=$2; rc=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (got $2, want $3)"; rc=1; fi; }
cat "$REAL_HOME/.hz-canary" >/dev/null 2>&1; check real-home-canary-unreadable $? 1
curl -s -o /dev/null --max-time 8 https://example.org; check non-allowlisted-host-blocked $([ $? -ne 0 ] && echo 1 || echo 0) 1
curl -s -o /dev/null --noproxy '*' --max-time 5 https://1.1.1.1; check direct-network-blocked $([ $? -ne 0 ] && echo 1 || echo 0) 1
r=$(printf '{"token":"%s","type":"approve","payload":{}}\n' "$TOKEN" | python3 -c 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect("/run/hz/session.sock");s.sendall(sys.stdin.buffer.read());print(s.makefile().readline().strip())')
check control-op-rejected "$r" '{"ok": false, "error": "forbidden"}'
test -e /run/hz/wsd.sock; check wsd-socket-absent $? 1
curl -s -o /dev/null --max-time 8 https://api.openai.com; check allowlisted-host-reachable $? 0
r=$(printf '{"token":"%s","type":"hook_event","payload":{}}\n' "$TOKEN" | python3 -c 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect("/run/hz/session.sock");s.sendall(sys.stdin.buffer.read());print(s.makefile().readline().strip())')
check hook-event-accepted "$r" '{"ok": true}'
# Probe 8 (added by the spike): only the launcher's env allowlist is visible inside.
extra=$(env | cut -d= -f1 | grep -vxE 'HOME|PATH|LANG|TERM|USER|HZ_SESSION_SOCKET|HTTPS_PROXY|HTTP_PROXY|NO_PROXY|PWD|SHLVL|_' | tr '\n' ' ')
check host-env-not-inherited "${extra:-none}" none
exit $rc
