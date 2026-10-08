# Hook command for e2e.py: forwards the event's payload and this launch's nonce (argv[2], written by the
# harness, never taken from the payload) to the harness socket (argv[1]), and prints what it answers.
# Like admind's hook, it never fails the agent: any error means no output and exit 0.
import json
import socket
import sys

try:
    payload = json.load(sys.stdin)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(8)
        s.connect(sys.argv[1])
        s.sendall(json.dumps({"launch": sys.argv[2], "payload": payload}).encode() + b"\n")
        reply = json.loads(s.makefile().readline() or "{}")
    if reply.get("stdout"):
        print(reply["stdout"])
except Exception:  # noqa: BLE001
    pass
