#!/usr/bin/env python3
"""Prepare reviewed adapter consent wiring; apply via install_brain.py."""
import argparse
import ast
import json
from pathlib import Path

from harness.cleanup import digest
from harness.hook_config import _write

ROOT = Path(__file__).resolve().parent
ANCHOR = '    async def _handle_mutation(self, event: Dict[str, Any]) -> None:\n'
HOOK = '''        # BEGIN HERMES NATIVE CONSENT
        if self.approval_reactions and event.get("type") == "reaction_added":
            import importlib.util
            module_spec = importlib.util.spec_from_file_location(
                "hermes_native_consent", Path(__file__).with_name("_native_consent.py"))
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            if module.receive(event, self.account_id_hex, resolve_allowed_message_senders()):
                return
        # END HERMES NATIVE CONSENT
'''


def plan(adapter):
    if adapter.is_symlink() or not adapter.is_file():
        raise ValueError('Expected regular existing Marmot adapter')
    original = adapter.read_text()
    if original.count(ANCHOR) != 1:
        raise ValueError('Native mutation hook contract changed')
    if '# BEGIN HERMES NATIVE CONSENT' in original:
        if ANCHOR + HOOK not in original:
            raise ValueError('Conflicting consent hook')
        updated = original
    else:
        updated = original.replace(ANCHOR, ANCHOR + HOOK)
    ast.parse(updated)
    shim = ('import sys\nfrom pathlib import Path\n'
            f'ROOT = Path({str(ROOT)!r})\n'
            'sys.path.insert(0, str(ROOT))\n'
            'from harness.store import Store\n'
            'from harness.permission_consent import receive as intake\n\n'
            'def receive(event, account_id, allowed_senders):\n'
            '    store = Store()\n'
            '    try:\n'
            '        return intake(store, event, account_id=account_id, allowed_senders=allowed_senders)\n'
            '    finally:\n'
            '        store.db.close()\n')
    result = {'files': [], 'creates': []}
    for path, text in ((adapter, updated), (adapter.with_name('_native_consent.py'), shim)):
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
