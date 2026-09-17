"""Install one vetted native-identity hook without replacing existing hooks.

Codex computes its own hook hash through hooks/list. Only this exact command's
hash is trusted, and no session/model request is started during installation.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import tomllib

MATCHER = 'startup|resume|compact|clear'


def session_hook(command):
    return {'matcher': MATCHER, 'hooks': [{'type': 'command', 'command': command, 'timeout': 15}]}


def claude_settings(command):
    return json.dumps({'hooks': {'SessionStart': [session_hook(command)]}}, separators=(',', ':'))


def claude_args(extra_args, command):
    """Merge one identity hook into explicit Claude settings without dropping policy."""
    args = list(extra_args)
    slots = []
    for index, arg in enumerate(args):
        if arg == '--settings':
            if index + 1 >= len(args):
                raise ValueError('--settings requires a JSON object or file')
            slots.append((index, index + 1, args[index + 1]))
        elif arg.startswith('--settings='):
            slots.append((index, index, arg.split('=', 1)[1]))
    if len(slots) > 1:
        raise ValueError('multiple --settings inputs are ambiguous; consolidate them first')
    if not slots:
        return args + ['--settings', claude_settings(command)]
    flag_index, value_index, value = slots[0]
    if value.lstrip().startswith('{'):
        config = json.loads(value)
    else:
        config = json.loads(Path(value).expanduser().read_text())
    if not isinstance(config, dict):
        raise ValueError('Claude settings must be a JSON object')
    groups = config.setdefault('hooks', {}).setdefault('SessionStart', [])
    existing = [group for group in groups for hook in group.get('hooks', [])
                if hook.get('command') == command]
    if len(existing) > 1:
        raise ValueError('duplicate identity hooks in Claude settings')
    if existing and existing[0] != session_hook(command):
        raise ValueError('existing identity hook differs from required lifecycle hook')
    if not existing:
        groups.append(session_hook(command))
    encoded = json.dumps(config, separators=(',', ':'))
    args[value_index] = '--settings=' + encoded if value_index == flag_index else encoded
    return args


def _response(process, selector, pending, request, deadline):
    process.stdin.write(json.dumps(request).encode() + b'\n')
    process.stdin.flush()
    while time.monotonic() < deadline:
        while b'\n' in pending:
            line, _, remainder = pending.partition(b'\n')
            pending[:] = remainder
            response = json.loads(line)
            if response.get('id') == request['id']:
                if 'error' in response:
                    raise RuntimeError('Codex rejected hook discovery request')
                return response.get('result', {})
        if not selector.select(max(0, deadline - time.monotonic())):
            break
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            raise RuntimeError('Codex closed hook discovery connection')
        pending.extend(chunk)
        if len(pending) > 4 * 1024 * 1024:
            raise RuntimeError('Codex hook discovery response too large')
    raise TimeoutError('Codex hook discovery exceeded its deadline')


def discover_hook(command, home, cwd, executable='codex'):
    process = subprocess.Popen([executable, 'app-server'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env=dict(os.environ, CODEX_HOME=str(home)))
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    deadline = time.monotonic() + 20
    try:
        _response(process, selector, pending, {'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'hermes-harness-hook-install', 'version': '1'},
            'capabilities': {'experimentalApi': True}}}, deadline)
        result = _response(process, selector, pending, {'id': 2, 'method': 'hooks/list',
            'params': {'cwds': [str(cwd)]}}, deadline)
        expected_source = str(Path(home) / 'hooks.json')
        candidates = [hook for entry in result.get('data', []) for hook in entry.get('hooks', [])
                      if hook.get('command') == command and hook.get('eventName') == 'sessionStart'
                      and hook.get('sourcePath') == expected_source]
        if len(candidates) != 1:
            raise RuntimeError('Codex did not discover exactly one installed harness hook')
        hook = candidates[0]
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', hook.get('currentHash', '')):
            raise RuntimeError('Codex returned an invalid hook trust hash')
        if hook.get('matcher') != MATCHER or hook.get('timeoutSec') != 15 or not hook.get('enabled'):
            raise RuntimeError('Codex hook definition does not match the installed harness hook')
        return hook
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()


def _write(path, text):
    temp = path.with_name(path.name + '.harness.tmp')
    try:
        with open(temp, 'w', encoding='utf-8') as handle:
            os.chmod(temp, path.stat().st_mode & 0o777 if path.exists() else 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def _trust_text(text, key, digest):
    config = tomllib.loads(text)
    existing = config.get('hooks', {}).get('state', {}).get(key, {})
    if existing.get('trusted_hash') == digest:
        return text
    header = '[hooks.state.' + json.dumps(key) + ']'
    block = header + '\ntrusted_hash = ' + json.dumps(digest) + '\n'
    if key in config.get('hooks', {}).get('state', {}):
        # Preserve enabled and any future fields; only replace this key's hash.
        pattern = r'(?ms)^' + re.escape(header) + r'\s*\n(.*?)(?=^\[|\Z)'
        match = re.search(pattern, text)
        if not match:
            raise RuntimeError('existing hook trust uses unsupported TOML layout; preserve and repair manually')
        body = match.group(1)
        if re.search(r'(?m)^trusted_hash\s*=', body):
            body = re.sub(r'(?m)^trusted_hash\s*=.*$', 'trusted_hash = ' + json.dumps(digest), body)
        else:
            body += '\ntrusted_hash = ' + json.dumps(digest) + '\n'
        return text[:match.start()] + header + '\n' + body + text[match.end():]
    return text.rstrip() + '\n\n' + block


def install_codex_hook(command, codex_home=None, cwd=None, executable='codex'):
    """Explicit deployment operation; appends a hook and trusts only its hash."""
    home = Path(codex_home or os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / '.harness-hook-install.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = home / 'hooks.json'
        data = json.loads(path.read_text()) if path.exists() else {}
        groups = data.setdefault('hooks', {}).setdefault('SessionStart', [])
        matching = [group for group in groups for hook in group.get('hooks', [])
                    if hook.get('command') == command]
        if len(matching) > 1:
            raise RuntimeError('duplicate harness hooks already exist')
        if not matching:
            groups.append(session_hook(command))
            _write(path, json.dumps(data, indent=2) + '\n')
        elif matching[0] != session_hook(command):
            raise RuntimeError('existing harness hook differs; refusing to replace other handlers')
        hook = discover_hook(command, home, cwd or home, executable)
        config_path = home / 'config.toml'
        old = config_path.read_text() if config_path.exists() else ''
        new = _trust_text(old, hook['key'], hook['currentHash'])
        if old != new:
            tomllib.loads(new)
            _write(config_path, new)
        verified = discover_hook(command, home, cwd or home, executable)
        if verified.get('trustStatus') != 'trusted':
            raise RuntimeError('Codex has not confirmed trust for the installed harness hook')
        return {'key': verified['key'], 'hash': verified['currentHash'], 'source': str(path)}
