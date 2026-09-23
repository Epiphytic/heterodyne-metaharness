#!/usr/bin/env python3
"""Prepare an EROFS observer/agent resolver adapter; manager applies the plan."""
import argparse
import ast
import json
from pathlib import Path
from harness.cleanup import digest
from harness.hook_config import _write

REPLACEMENT = '''def handle_erofs_stall(state):
    """HERMES AGENT RESOLUTION: durable detection, dispatch and verification."""
    import sys
    sys.path.insert(0, str(HARNESS_REPO))
    from harness.babysitter_observers import run
    run(globals())
'''


def plan(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular babysitter source')
    source = path.read_text()
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_erofs_stall']
    if len(matches) != 1:
        raise ValueError('Expected one EROFS handler')
    node = matches[0]
    if (ast.unparse(node.args) != 'state' or node.decorator_list
            or not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == 'handle_erofs_stall' for n in ast.walk(tree))):
        raise ValueError('Babysitter handler/poll contract changed')
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [REPLACEMENT]
    updated = ''.join(lines)
    ast.parse(updated)
    return {'files': [] if updated == source else [
        {'path': str(path), 'sha256': digest(path), 'replacement': updated}], 'creates': []}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--babysitter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Plan already exists')
    _write(args.output, json.dumps(plan(args.babysitter.absolute()), indent=2) + '\n')
