#!/usr/bin/env python3
"""Prepare guarded Marmot /status wiring; apply privately via install_brain.py."""
import argparse
import ast
import json
from pathlib import Path

from harness.cleanup import digest
from harness.hook_config import _write

ROOT = Path(__file__).resolve().parent
ANCHOR = '            # BEGIN HERMES WORKSTREAM ROUTING\n'
HOOK = '''            # BEGIN HERMES CHANNEL STATUS
            import importlib.util
            status_spec = importlib.util.spec_from_file_location(
                "hermes_channel_status", Path(__file__).with_name("_channel_status.py"))
            status_module = importlib.util.module_from_spec(status_spec)
            status_spec.loader.exec_module(status_module)
            if await status_module.handle(self, event, resolve_allowed_message_senders()):
                return
            # END HERMES CHANNEL STATUS
'''


def plan(adapter):
    if adapter.is_symlink() or not adapter.is_file():
        raise ValueError('Expected regular existing Marmot adapter')
    old = adapter.read_text()
    if old.count(ANCHOR) != 1:
        raise ValueError('Native routing hook contract changed')
    if '# BEGIN HERMES CHANNEL STATUS' in old:
        if HOOK + ANCHOR not in old:
            raise ValueError('Conflicting status hook')
        updated = old
    else:
        updated = old.replace(ANCHOR, HOOK + ANCHOR)
    ast.parse(updated)
    shim = ('import sys\n' + f'sys.path.insert(0, {str(ROOT)!r})\n'
            'from harness.channel_status_hook import handle\n')
    result = {'files': [], 'creates': []}
    for path, text in ((adapter, updated), (adapter.with_name('_channel_status.py'), shim)):
        if path.is_symlink():
            raise ValueError('Refusing symlink installer target')
        if path.exists():
            if path.read_text() != text:
                result['files'].append({'path': str(path), 'sha256': digest(path), 'replacement': text})
        else:
            result['creates'].append({'path': str(path), 'content': text})
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adapter', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Private plan output already exists')
    _write(args.output, json.dumps(plan(args.adapter.absolute()), indent=2)+'\n')
