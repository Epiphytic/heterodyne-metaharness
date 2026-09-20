"""Per-Bead owned worktrees and safe native handoff; spec/worktrees.md."""
import json
import os
from pathlib import Path
import re
import subprocess

from .beads import BeadsError
from .workspace import git


def boundary(run):
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        raise BeadsError('Reconcile recovery before changing task worktree')
    if run.get('native_session_id') and run.get('native_turn_state') != 'idle':
        raise BeadsError('Task worktree switch requires a confirmed idle native turn')
    if run.get('observed_state') == 'awaiting_approval':
        raise BeadsError('Native approval must be resolved before changing worktree')
    if git(run['workdir'], 'status', '--porcelain'):
        raise BeadsError('Uncommitted work at task boundary is a workflow error; commit first')


def prepare(run, root, issue_id, previous):
    """Caller holds supervisor lock; retry only this exact recorded destination."""
    if not run.get('repo'):
        return None
    existing = run.get('task_worktrees', {}).get(issue_id)
    if previous == issue_id and existing:
        return existing
    if previous == issue_id and not existing:
        return None  # Deployment migration: never force-move an existing claim.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', issue_id):
        raise BeadsError('Invalid task worktree identity')
    boundary(run)
    source = Path(existing['source'] if existing else
                  run.get('owned_worktree', {}).get('path', run['workdir'])).resolve()
    directory = root / 'runs' / run['id'] / 'beads' / issue_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'checkout'
    manifest = directory / 'workspace.json'
    record = {'issue_id': issue_id, 'path': str(target), 'branch': 'bead/' + run['id'] + '/' + issue_id,
              'repo': str(Path(run['repo']).resolve()), 'base': git(run['repo'], 'rev-parse', 'HEAD'),
              'source': str(source), 'dependencies': []}
    if manifest.exists():
        saved = json.loads(manifest.read_text())
        for key in ('issue_id', 'path', 'branch', 'repo', 'source'):
            if saved[key] != record[key]:
                raise BeadsError('Task worktree manifest ownership mismatch')
        record = saved
    else:
        _save(manifest, record)  # Recovery intent before Git mutation.
    _checkout(record)
    _dependencies(record, manifest)
    run.setdefault('task_worktrees', {})[issue_id] = record
    return record



def _checkout(record):
    target = Path(record['path'])
    if target.exists():
        if git(target, 'rev-parse', '--show-toplevel') != str(target) or git(target, 'branch', '--show-current') != record['branch']:
            raise BeadsError('Refusing unowned task checkout')
    else:
        # An uncertain prior git-add may have created the branch but no checkout.
        exists = subprocess.run(['git', '-C', record['repo'], 'show-ref', '--verify', '--quiet',
                                 'refs/heads/' + record['branch']], check=False).returncode == 0
        if exists:
            if git(record['repo'], 'rev-parse', record['branch']) != record['base']:
                raise BeadsError('Existing task branch changed; reconcile before adoption')
            git(record['repo'], 'worktree', 'add', str(target), record['branch'])
        else:
            git(record['repo'], 'worktree', 'add', '-b', record['branch'], str(target), record['base'])


def _dependencies(record, manifest):
    source, target = Path(record['source']), Path(record['path'])
    if not record.get('ready'):
        for name in ('.venv', 'venv', 'node_modules'):
            origin = source / name
            if not origin.is_dir():
                continue
            if origin.is_symlink() or (target / name).is_symlink():
                raise BeadsError('Dependency root symlink needs explicit operator reconciliation')
            if git(target, 'ls-files', '--', name):
                raise BeadsError('Refusing to overwrite tracked dependency files')
            destination = target / name
            destination.mkdir(exist_ok=True)
            subprocess.run(['cp', '-a', '--reflink=auto', '--', str(origin) + '/.', str(destination)],
                           check=True, capture_output=True, timeout=300)
            record['dependencies'].append({'path': name, 'source': str(origin), 'method': 'reflink-auto; local-copy fallback'})
            # Retry can safely recopy an incomplete dependency tree before ready;
            # no worker is launched against it until the durable ready marker.
        record['ready'] = True
        _save(manifest, record)

def _save(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as out:
        json.dump(data, out, sort_keys=True, indent=2)
        out.flush(); os.fsync(out.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def switch(supervisor, run, record):
    if not record or run['workdir'] == record['path']:
        return
    boundary(run)
    if not run.get('native_session_id'):
        raise BeadsError('Register exact native identity before switching a live worker')
    run['workspace_transition'] = record
    supervisor.persist(run)
    # Exact owned idle worker only. No manager restart, no task prompt replay.
    supervisor.tmux.stop(run)
    run['workdir'] = record['path']
    config = run.setdefault('config', {})
    args = list(config.get('extra_args', config.get('args', [])))
    clean = []
    i = 0
    while i < len(args):
        if args[i] in ('-C', '--cd'):
            i += 2
        elif args[i].startswith('--cd='):
            i += 1
        else:
            clean.append(args[i]); i += 1
    config['extra_args'] = clean + (['-C', run['workdir']] if run['agent'] == 'codex' else [])
    run['launch_prompt'] = ''
    run['prompt_state'] = 'not_replayed'
    supervisor.persist(run)
    if run.get('native_session_id'):
        supervisor.launch(run, run, recovering=True)
    run.pop('workspace_transition', None)
    supervisor.persist(run)


def assign(args, store, supervisor, beads, run):
    """Binding/claim and owned cwd transition share the supervisor transaction lock."""
    previous = run.get('beads', {}).get('issue_id')
    if args.task_action == 'show':
        return beads.show(run, args.issue_id)
    if previous != args.issue_id and run.get('repo'):
        boundary(run)
    result = getattr(beads, args.task_action)(run, args.issue_id)
    record = prepare(run, store.root, args.issue_id, previous)
    if record:
        supervisor.persist(run)
        switch(supervisor, run, record)
    if args.task_action == 'claim':
        from .task_interrupt import resumed
        resumed(run, args.issue_id)
    return result
