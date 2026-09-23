"""Unit rendering only; install.py owns operator-authorized deployment."""
import json
import os


def units(root, home, interpreter):
    command = ' '.join(json.dumps(str(part)) for part in
                       (interpreter, '-m', 'harness.cli', '--home', home, 'manager-task', 'pickup'))
    return {'hermes-manager-pickup.service': f'''[Unit]
Description=Deterministic approval and escalation manager pickup
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={command}
Environment={json.dumps("PATH=" + os.environ["PATH"])}
UMask=0077
''', 'hermes-manager-pickup.timer': '''[Unit]
Description=Check manager approval and escalation beads once per minute

[Timer]
OnBootSec=60s
OnUnitActiveSec=60s
AccuracySec=1s
Unit=hermes-manager-pickup.service

[Install]
WantedBy=timers.target
'''}
