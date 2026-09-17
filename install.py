#!/usr/bin/env python3
"""Idempotent local deployment; no agents, groups, or tasks are started by install."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def replace(path, content, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return
    if path.exists():
        backup = path.with_name(path.name + '.pre-durable-harness')
        if not backup.exists():
            shutil.copy2(path, backup)
    temp = path.with_suffix(path.suffix + '.new')
    temp.write_text(content)
    temp.chmod(mode)
    temp.replace(path)


def deploy(home, enable=True):
    scripts = home / 'scripts'
    cli = ROOT / 'bin' / 'workstream'
    forward = '#!/usr/bin/env bash\nset -euo pipefail\nexec ' + shlex.quote(str(cli)) + ' "$@"\n'
    replace(scripts / 'workstream', forward)
    replace(Path.home() / '.local/bin/workstream', forward)
    replace(scripts / 'workstream-start', '''#!/usr/bin/env bash
set -euo pipefail
args=(start "${1:?name required}" "${2:?repo required}" "${3:?agent required}")
if [ "$#" -gt 3 ]; then args+=(--prompt "$4"); fi
if [ "$#" -gt 4 ] && [ -n "$5" ]; then args+=(--group "$5"); fi
exec "$(dirname "$0")/workstream" "${args[@]}"
''')
    replace(scripts / 'workstream-stop', '''#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/workstream" stop "${1:?workstream name required}"
''')
    replace(scripts / 'workstream-reconcile', '''#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/workstream" tick
''')
    replace(scripts / 'session-watchdog.py', '''#!/usr/bin/env python3
"""Compatibility: supervision is owned by the singleton systemd service."""
import os
from pathlib import Path
cli = str(Path(__file__).with_name('workstream'))
os.execv(cli, [cli, 'tick'])
''')
    replace(scripts / 'twrap', '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if len(sys.argv) < 6:
    raise SystemExit('usage: twrap SESSION GROUP LABEL LOGFILE -- COMMAND...')
name, group, label, logfile = sys.argv[1:5]
command = sys.argv[5:]
if command[0] == '--': command = command[1:]
if not command: raise SystemExit('command is required')
config = {'argv': command, 'working_directory': os.getcwd(), 'logfile': str(Path(logfile).resolve())}
cli = str(Path(__file__).with_name('workstream'))
os.execv(cli, [cli, 'start', name, '-', 'command', '--group', group,
              '--prompt', label, '--agent-config', json.dumps(config)])
''')
    replace(scripts / 'tmux-progress-relay.sh', '''#!/usr/bin/env bash
set -euo pipefail
echo 'Standalone relay retired. Launch through twrap or workstream start; the supervisor owns reporting.' >&2
exit 1
''')
    replace(scripts / 'watchdog-digest.py', '''#!/usr/bin/env python3
"""Read-only legacy monitor view; consuming this output cannot lose delivery."""
import json, os, sqlite3, sys
from pathlib import Path
db = Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes'))) / 'workstreams/harness.sqlite3'
if db.exists() and len(sys.argv) > 1:
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as con:
        rows = con.execute("SELECT e.kind,e.text FROM events e JOIN runs r ON e.run_id=r.id WHERE r.name=? ORDER BY e.created_at DESC LIMIT 5", (sys.argv[1],)).fetchall()
        for kind, text in reversed(rows): print(f'[{kind}] {text}')
''')
    replace(scripts / 'watchdog-digest.sh', '''#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(dirname "$0")/watchdog-digest.py" "$@"
''')
    soul = home / 'SOUL.md'
    policy = (ROOT / 'policy/SOUL-workstreams.md').read_text().strip()
    content = soul.read_text() if soul.exists() else '# SOUL.md\n'
    begin, end = '<!-- BEGIN DURABLE WORKSTREAM POLICY -->', '<!-- END DURABLE WORKSTREAM POLICY -->'
    block = begin + '\n' + policy + '\n' + end
    if begin in content:
        prefix, rest = content.split(begin, 1)
        _, suffix = rest.split(end, 1)
        content = prefix + block + suffix
    else:
        content = content.rstrip() + '\n\n' + block + '\n'
    replace(soul, content, 0o644)
    replace(home / 'skills/autonomous-ai-agents/coding-delegation/SKILL.md',
            (ROOT / 'policy/coding-delegation.md').read_text(), 0o644)
    path = home / 'workstreams/harness-config.json'
    if not path.exists():
        config = {'tmux_socket': 'hermes-workstreams', 'cli': str(cli), 'marmot': {'timeout': 10}}
        # Reuse the configured household membership; never invent recipients.
        try:
            import yaml
            data = yaml.safe_load((home / 'config.yaml').read_text())
            platform = data.get('platforms', {}).get('marmot', {})
            extra = platform.get('extra', {})
            members = extra.get('welcomer_allowlist', [])
            if isinstance(members, str): members = [x.strip() for x in members.split(',') if x.strip()]
            config['marmot']['members'] = members
            config['ops_group'] = platform.get('home_channel', {}).get('chat_id')
        except (ImportError, OSError, ValueError):
            pass
        replace(path, json.dumps(config, indent=2) + '\n', 0o600)
    config = json.loads(path.read_text())
    config['native_hook_command'] = shlex.quote(str(ROOT / 'bin/workstream-session')) + ' auto'
    replace(path, json.dumps(config, indent=2) + '\n', 0o600)
    unit = Path.home() / '.config/systemd/user/hermes-workstreams.service'
    interpreter = shutil.which('python3')
    service = f'''[Unit]
Description=Durable Hermes workstream supervisor
After=network-online.target wn-agent-hermes.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={interpreter} -m harness.cli --home {home} daemon
Environment="HERMES_HOME={home}"
Environment="PATH={os.environ['PATH']}"
Restart=always
RestartSec=3
KillMode=process
TimeoutStopSec=20
UMask=0077

[Install]
WantedBy=default.target
'''
    replace(unit, service, 0o644)
    if enable:
        from harness.hook_config import install_codex_hook
        codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        for filename in ('hooks.json', 'config.toml'):
            source = codex_home / filename
            backup = source.with_name(source.name + '.pre-durable-harness')
            if source.exists() and not backup.exists(): shutil.copy2(source, backup)
        install_codex_hook(config['native_hook_command'])
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'hermes-workstreams.service'], check=True)
    print(json.dumps({'cli': str(cli), 'service': str(unit), 'enabled': enable}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes'))))
    p.add_argument('--no-enable', action='store_true')
    args = p.parse_args()
    deploy(args.home, not args.no_enable)
