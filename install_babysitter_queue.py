#!/usr/bin/env python3
"""Prepare bounded babysitter detector edits; apply via install_brain.py."""
import argparse
import ast
import json
from pathlib import Path
from harness.cleanup import digest
from harness.hook_config import _write


def plan(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular babysitter source')
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    node = nodes['nudge_idle_worker']
    if [a.arg for a in node.args.args] != ['pane', 'pinfo', 'pstate'] or node.decorator_list:
        raise ValueError('Babysitter nudge contract changed')
    lines[node.lineno-1:node.end_lineno] = ['''def nudge_idle_worker(pane, pinfo, pstate):
    """HERMES IDLE READY DETECTOR: guarded nudge then durable escalation."""
    import sys
    sys.path.insert(0, str(HARNESS_REPO))
    from harness.babysitter_queue import run
    run(pinfo, pstate, globals())
''']
    updated = ''.join(lines)
    # Reset an episode when the existing classifier observes actual work or a
    # prompt; do not change its classification or existing approval handling.
    node = next(n for n in ast.parse(updated).body if isinstance(n, ast.FunctionDef) and n.name == 'poll_pane')
    lines = updated.splitlines(keepends=True)
    body = lines[node.lineno-1:node.end_lineno]
    if not any('pstate.pop("idle_ready_episode", None)' in line for line in body):
        replaced = []
        for line in body:
            replaced.append(line)
            if line.strip() == 'pstate["idle_polls"] = 0':
                indent = line[:len(line)-len(line.lstrip())]
                replaced.append(indent + 'pstate.pop("idle_ready_episode", None)\n')
        lines[node.lineno-1:node.end_lineno] = replaced
    updated = ''.join(lines)
    ast.parse(updated)
    return {'files': [] if updated == source else [{'path':str(path), 'sha256':digest(path), 'replacement':updated}], 'creates':[]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--babysitter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Plan already exists')
    _write(args.output, json.dumps(plan(args.babysitter.absolute()), indent=2)+'\n')
