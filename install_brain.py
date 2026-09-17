#!/usr/bin/env python3
"""Reviewed generic session-sync installation. Contract: spec/brain.md."""
import argparse
import json
import os
from pathlib import Path
import shlex
import yaml

from harness import cleanup
from harness.hook_config import MATCHER, _trust_queue_hooks, _write

ROOT = Path(__file__).resolve().parent
EVENTS = [('SessionStart', 'sessionStart', MATCHER),
          ('UserPromptSubmit', 'userPromptSubmit', None), ('Stop', 'stop', None)]


def hook_plan(path, provider):
    data = json.loads(path.read_text()) if path.exists() else {}
    command = shlex.quote(str(ROOT / 'bin/workstream-brain-hook')) + ' ' + provider
    vetted = []
    for event, native, matcher in EVENTS:
        group = {'hooks': [{'type': 'command', 'command': command, 'timeout': 15}]}
        if matcher is not None:
            group['matcher'] = matcher
        groups = data.setdefault('hooks', {}).setdefault(event, [])
        matching = [item for item in groups for hook in item.get('hooks', [])
                    if hook.get('command') == command]
        if matching and matching != [group]:
            raise ValueError('Ambiguous or modified brain hook: ' + event)
        if not matching:
            groups.append(group)
        vetted.append((command, native, matcher, 15))
    return json.dumps(data, indent=2) + '\n', vetted


def plan(home, codex, claude):
    replacements = {}
    for name in ('__init__.py', 'plugin.yaml'):
        replacements[home / 'plugins/session-sync' / name] = (ROOT / 'plugins/session-sync' / name).read_text()
    replacements[home / 'plugins/session-sync/runtime.json'] = json.dumps({'package_root': str(ROOT)}) + '\n'
    config_path = home / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    enabled = config.setdefault('plugins', {}).setdefault('enabled', [])
    if not isinstance(enabled, list):
        raise ValueError('Unexpected enabled plugins schema')
    if 'session-sync' not in enabled:
        enabled.append('session-sync')
    replacements[config_path] = yaml.safe_dump(config, sort_keys=False)
    for provider, path in [('codex', codex / 'hooks.json'), ('claude', claude / 'settings.json')]:
        replacements[path] = hook_plan(path, provider)[0]
    files, creates = [], []
    for path, desired in replacements.items():
        if path.is_symlink():
            raise ValueError('Refusing symlink installation target')
        if path.exists():
            if path.read_text() != desired:
                files.append({'path': str(path), 'sha256': cleanup.digest(path), 'replacement': desired})
        else:
            creates.append({'path': str(path), 'content': desired})
    return {'files': files, 'creates': creates, 'codex_home': str(codex)}


def apply(plan, quarantine):
    # Inspect every creation before any existing source mutation.
    for item in plan['creates']:
        path = Path(item['path'])
        if path.is_symlink() or (path.exists() and path.read_text() != item['content']):
            raise ValueError('New installation path drift')
    result = cleanup.apply(plan, quarantine)
    for item in plan['creates']:
        path = Path(item['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            # Atomic publication avoids half-created files on interruption.
            _write(path, item['content'])
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        if path.read_text() != item['content']:
            raise ValueError('Creation verification failed')
    return result


def memory_plan(home):
    config = yaml.safe_load((home / 'config.yaml').read_text())
    if 'session-sync' not in config.get('plugins', {}).get('enabled', []):
        raise ValueError('Cannot record enabled plugin before deployment')
    path = home / 'memories/MEMORY.md'
    original = path.read_text()
    entries = original.split('§')
    matches = [i for i, entry in enumerate(entries) if entry.strip().startswith('session-sync plugin (')]
    if len(matches) != 1:
        raise ValueError('Expected one session-sync memory entry')
    entries[matches[0]] = '\nShared brain notifications: session-sync is enabled. Current behavior and receipt/checkpoint contract: /home/operator/repos/hermes-workstream-harness/spec/brain.md.\n'
    return {'files': [{'path': str(path), 'sha256': cleanup.digest(path), 'replacement': '§'.join(entries)}],
            'creates': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--claude-home', type=Path)
    parser.add_argument('--apply', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--memory-enabled', action='store_true')
    parser.add_argument('--quarantine', type=Path)
    parser.add_argument('--trust-codex', type=Path)
    args = parser.parse_args()
    if args.trust_codex:
        path = args.trust_codex.resolve()
        updated, vetted = hook_plan(path / 'hooks.json', 'codex')
        if updated != (path / 'hooks.json').read_text():
            raise ValueError('Install reviewed hooks before exact trust')
        if not args.quarantine:
            parser.error('--trust-codex requires private --quarantine')
        config = path / 'config.toml'
        original = config.read_text()
        args.quarantine.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = args.quarantine / ('codex-config-' + cleanup.digest(config))
        if not backup.exists():
            _write(backup, original)
        if backup.read_text() != original:
            raise ValueError('Trust configuration backup mismatch')
        with backup.open('rb') as stream:
            os.fsync(stream.fileno())
        fd = os.open(args.quarantine, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if config.read_text() != original:
            raise ValueError('Trust configuration drift')
        _trust_queue_hooks(path, vetted)
        print(json.dumps({'trusted': 'exact installed brain hook definitions'}))
    elif args.apply:
        if not args.quarantine:
            parser.error('--apply requires --quarantine')
        print(json.dumps(apply(json.loads(args.apply.read_text()), args.quarantine)))
    else:
        if not args.output:
            parser.error('private --output is required; plans contain configuration values')
        if args.memory_enabled:
            if not args.home:
                parser.error('--memory-enabled requires --home')
            prepared = memory_plan(args.home.resolve())
        elif not all((args.home, args.codex_home, args.claude_home)):
            parser.error('plan requires all three explicit homes')
        else:
            prepared = plan(args.home.resolve(), args.codex_home.resolve(), args.claude_home.resolve())
        if args.output.exists():
            raise ValueError('Plan output already exists; preserve reviewed plan')
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write(args.output, json.dumps(prepared, indent=2))
        print(json.dumps({'plan': str(args.output), 'existing_files': len(prepared['files']), 'new_files': len(prepared['creates'])}))


if __name__ == '__main__':
    main()
