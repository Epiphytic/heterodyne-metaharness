#!/usr/bin/env python3
"""Prepare a private reviewed Codex notify plan; apply with install_brain.py."""
import argparse
import json
from pathlib import Path
import re
import tomllib

from harness import cleanup
from harness.hook_config import _write

ROOT = Path(__file__).resolve().parent


def replace_notify(text, prior, line):
    """Isolate a conventional root assignment without serializing other policy."""
    match = re.search(r'(?m)^notify\s*=', text)
    if not match:
        raise ValueError('Unsupported notify TOML layout')
    end = match.start()
    for segment in text[match.start():].splitlines(keepends=True):
        end += len(segment)
        try:
            value = tomllib.loads(text[match.start():end])
        except tomllib.TOMLDecodeError:
            continue
        if value == {'notify': prior}:
            return text[:match.start()] + line + text[end:]
    raise ValueError('Cannot isolate existing notify command')


def configured(text):
    config = tomllib.loads(text)
    prior = config.get('notify', [])
    if not isinstance(prior, list) or not all(isinstance(arg, str) for arg in prior):
        raise ValueError('Unsupported notify command')
    prefix = ['/usr/bin/python3', str(ROOT / 'bin/workstream-notify'), '--forward']
    if prior[:3] == prefix:
        if len(prior) != 4 or not isinstance(json.loads(prior[3]), list):
            raise ValueError('Modified managed notifier')
        return text
    desired = prefix + [json.dumps(prior)]
    line = 'notify = ' + json.dumps(desired) + '\n'
    if 'notify' not in config:
        updated = line + text
    else:
        updated = replace_notify(text, prior, line)
    expected = dict(config, notify=desired)
    if tomllib.loads(updated) != expected:
        raise ValueError('Notify edit changed unrelated configuration')
    return updated


def plan(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected existing regular user config')
    old = path.read_text()
    new = configured(old)
    return {'files': [] if old == new else [{'path': str(path), 'sha256': cleanup.digest(path),
                                           'replacement': new}], 'creates': []}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Private output already exists')
    _write(args.output, json.dumps(plan(args.config.absolute()), indent=2) + '\n')
