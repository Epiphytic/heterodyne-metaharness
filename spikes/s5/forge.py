"""Negative control only (S5_AGENT_INJECT=forge): after the real probe failed, a process inside the
sandbox that is not the probe claims every check passed over the result channel. The launcher runs it
with `sandbox exec` and must reject it."""

import json
import socket

NAMES = ['real-home-canary-unreadable', 'non-allowlisted-host-blocked', 'direct-network-blocked',
         'control-op-rejected', 'wsd-socket-absent', 'allowlisted-host-reachable', 'model-host-agent-path',
         'hook-event-accepted', 'host-env-not-inherited', 'openshell-control-material-unreadable',
         'other-accounts']
s = socket.socket(socket.AF_UNIX)
s.connect('/run/hz/probe.sock')
try:
    for n in NAMES:
        s.sendall((json.dumps({'check': n, 'ok': True, 'evidence': 'forged'}) + '\n').encode())
    s.sendall(b'{"done": 0}\n')
    print('forge: sent a forged all-PASS result')
except OSError as e:
    print(f'forge: channel closed on us ({e.strerror})')
