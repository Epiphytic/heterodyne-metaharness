#!/usr/bin/env python3
"""Prepare bounded babysitter adapters and explicit admin lookup configuration.

Apply the private plan via install_brain.py after reviewing live-home safety.
"""
import argparse
import ast
import json
from pathlib import Path

from harness.cleanup import digest
from harness.hook_config import _write


def replacement(name):
    return f'''def {name}(pinfo, text):
    """HERMES DURABLE OPERATOR ASKS: canonical outbox adapter."""
    try:
        import sys
        sys.path.insert(0, str(HARNESS_REPO))
        from harness.operator_asks import babysitter_notice
        result = babysitter_notice(pinfo, text, operator_ask={name == 'escalate'})
        log(f"{name} queued={{result['queued']}} event={{result['event_id']}}")
        return result['queued']
    except Exception as exc:
        log(f"{name} enqueue failed: {{type(exc).__name__}}")
        return False
'''


def plan(babysitter, config_path, binary, home):
    for path in (babysitter, config_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError('Installer expects existing regular files')
    original = babysitter.read_text()
    tree = ast.parse(original)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('notify', 'escalate')]
    if {node.name for node in functions} != {'notify', 'escalate'} or len(functions) != 2:
        raise ValueError('Babysitter adapter functions changed')
    lines = original.splitlines(keepends=True)
    for node in sorted(functions, key=lambda node: node.lineno, reverse=True):
        if [arg.arg for arg in node.args.args] != ['pinfo', 'text'] or node.decorator_list:
            raise ValueError('Babysitter call signature changed')
        lines[node.lineno-1:node.end_lineno] = [replacement(node.name)]
    updated = ''.join(lines)
    ast.parse(updated)
    config = json.loads(config_path.read_text())
    config.setdefault('marmot', {})['group_admins'] = {'binary': str(binary), 'home': str(home)}
    result = {'files': [], 'creates': []}
    for path, content in ((babysitter, updated), (config_path, json.dumps(config, indent=2)+'\n')):
        if path.read_text() != content:
            result['files'].append({'path': str(path), 'sha256': digest(path), 'replacement': content})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('babysitter', 'config', 'binary', 'marmot-home', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Private plan output already exists')
    _write(args.output, json.dumps(plan(args.babysitter.absolute(), args.config.absolute(),
                                      args.binary.absolute(), args.marmot_home.absolute()), indent=2)+'\n')
