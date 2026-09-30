#!/bin/sh
# Usage (inside sandbox): probes.sh <real-home> <token>. Prints one PASS/FAIL line per probe, with evidence.
# 7 probes from the plan (1, 2, 3 strengthened in fix round 1 so that no generic failure can PASS),
# plus probe 8, host-env-not-inherited. The launcher (launch.py --selftest) checks the canary exists
# outside the sandbox before running this, and checks proxy.log for the refusal after it.
REAL_HOME=$1; TOKEN=$2; rc=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1 [$4]"; else echo "FAIL $1 (got $2, want $3) [$4]"; rc=1; fi; }
sock() { python3 -c 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect("/run/hz/session.sock");s.sendall(sys.stdin.buffer.read());print(s.makefile().readline().strip())'; }

# 1. Canary: the real-home path must be absent (ENOENT) or denied (EACCES/EPERM); any other outcome FAILs.
e=$(python3 - "$REAL_HOME/.hz-canary" <<'EOF'
import errno, sys
try:
    open(sys.argv[1]).read(); print('READABLE')
except OSError as x:
    print(errno.errorcode.get(x.errno, str(x.errno)))
EOF
)
case $e in ENOENT|EACCES|EPERM) v=ok ;; *) v=bad ;; esac
check real-home-canary-unreadable $v ok "open(<real-home>/.hz-canary) -> $e"

# 2. Non-allowlisted host: PASS only if our proxy answered the CONNECT with 403 and its marker header.
out=$(curl -sv -o /dev/null --max-time 8 -w 'connect=%{http_connect}' https://example.org 2>&1); crc=$?
code=$(printf '%s' "$out" | sed -n 's/.*connect=\([0-9]*\).*/\1/p' | tail -1)
marker=$(printf '%s\n' "$out" | grep -ci '^< X-HZ-Egress: denied')
v=bad; [ $crc -ne 0 ] && [ "$code" = 403 ] && [ "$marker" -ge 1 ] && v=ok
check non-allowlisted-host-blocked $v ok "curl rc=$crc CONNECT=$code X-HZ-Egress-denied=$marker"

# 3. Direct network: no non-loopback interface, and a raw TCP connect to a literal IP fails with
#    ENETUNREACH/EHOSTUNREACH (no route), not a timeout, refusal or TLS error.
ifs=$(tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' ' | grep -vx lo | tr '\n' ' ')
e=$(python3 - <<'EOF'
import errno, socket
s = socket.socket(); s.settimeout(3)
try:
    s.connect(('1.1.1.1', 443)); print('CONNECTED')
except socket.timeout:
    print('TIMEOUT')
except OSError as x:
    print(errno.errorcode.get(x.errno, str(x.errno)))
EOF
)
v=bad; [ -z "$ifs" ] && { [ "$e" = ENETUNREACH ] || [ "$e" = EHOSTUNREACH ]; } && v=ok
check direct-network-blocked $v ok "non-lo interfaces='${ifs:-none}' connect(1.1.1.1:443) -> $e"  # install-agnostic: allow=ip-port (public anycast probe target, not install-specific)

# 4. Control operation on the session socket, with a valid token, is refused.
r=$(printf '{"token":"%s","type":"approve","payload":{}}\n' "$TOKEN" | sock)
check control-op-rejected "$r" '{"ok": false, "error": "forbidden"}' "reply=$r"

# 5. The wsd socket is not bound in.
test -e /run/hz/wsd.sock; x=$?
check wsd-socket-absent $x 1 "test -e /run/hz/wsd.sock rc=$x; /run/hz: $(ls /run/hz | tr '\n' ' ')"

# 6. Allowlisted host: reachable, and the tunnel was established by the proxy (CONNECT 200).
out=$(curl -s -o /dev/null --max-time 8 -w 'connect=%{http_connect} http=%{http_code}' https://api.openai.com 2>&1); crc=$?
v=bad; [ $crc -eq 0 ] && printf '%s' "$out" | grep -q 'connect=200' && v=ok
check allowlisted-host-reachable $v ok "curl rc=$crc $out"

# 7. Hook event with the valid token is accepted.
r=$(printf '{"token":"%s","type":"hook_event","payload":{}}\n' "$TOKEN" | sock)
check hook-event-accepted "$r" '{"ok": true}' "reply=$r"

# 8. Only the launcher's env allowlist is visible inside.
extra=$(env | cut -d= -f1 | grep -vxE 'HOME|PATH|LANG|TERM|USER|HZ_SESSION_SOCKET|HTTPS_PROXY|HTTP_PROXY|NO_PROXY|PWD|SHLVL|_' | tr '\n' ' ')
check host-env-not-inherited "${extra:-none}" none "unexpected vars: ${extra:-none}"
exit $rc
