"""Inside the sandbox: Claude PreToolUse hook that forwards the event to the session socket. Spike S3.

Usage (settings.json hook command): python3 /run/hz/hook.py <token>
"""
import json
import os
import socket
import sys

event = json.load(sys.stdin)
s = socket.socket(socket.AF_UNIX)
s.connect(os.environ.get('HZ_SESSION_SOCKET', '/run/hz/session.sock'))
s.sendall((json.dumps({'token': sys.argv[1], 'type': 'hook_event', 'payload': event}) + '\n').encode())
reply = json.loads(s.makefile().readline())
sys.exit(0 if reply.get('ok') else 2)   # exit 2 blocks the tool call if the socket refuses
