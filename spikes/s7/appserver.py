# Drives `codex app-server` over stdio (JSON-RPC lines): initialize, then the requests given as argv.
# Needs AS_ERR (stderr log path). Example: appserver.py 'account/read={"refreshToken": false}' account/rateLimits/read
import json
import os
import subprocess
import sys
import time

p = subprocess.Popen(
    ["codex", "app-server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=open(os.environ["AS_ERR"], "w"),
    text=True,
)


def send(o):
    p.stdin.write(json.dumps(o) + "\n")
    p.stdin.flush()


def recv_until(id_, timeout=20):
    end = time.time() + timeout
    out = []
    while time.time() < end:
        line = p.stdout.readline()
        if not line:
            break
        m = json.loads(line)
        out.append(m)
        if m.get("id") == id_:
            return out
    return out


send(
    {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"clientInfo": {"name": "s7-spike", "version": "0"}},
    }
)
for m in recv_until(0):
    print("init:", json.dumps(m)[:300])
send({"jsonrpc": "2.0", "method": "initialized"})
for i, meth in enumerate(sys.argv[1:], 1):
    meth, _, params = meth.partition("=")  # METHOD, or METHOD=JSON-PARAMS
    send({"jsonrpc": "2.0", "id": i, "method": meth, "params": json.loads(params) if params else None})
    for m in recv_until(i):
        print(meth + ":", json.dumps(m)[:1500])
p.stdin.close()
p.terminate()
try:
    p.wait(5)
except subprocess.TimeoutExpired:
    p.kill()
    p.wait()
