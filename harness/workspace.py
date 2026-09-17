"""Owned worktrees: creation is recoverable; cleanup never touches other writers."""
from pathlib import Path
import subprocess


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def prepare(run, root):
    task = run.get('task_worktrees', {}).get(run.get('beads', {}).get('issue_id'))
    if task and run.get('workdir') == task['path']:
        if git(task['path'], 'branch', '--show-current') != task['branch']:
            raise RuntimeError('Task worktree ownership changed')
        return
    if not run.get('repo'):
        Path(run['workdir']).mkdir(parents=True, exist_ok=True)
        return
    repo = Path(run['repo']).resolve()
    target = root / 'runs' / run['id'] / 'checkout'
    branch = 'workstream/' + run['id']
    if target.exists():
        actual = git(target, 'rev-parse', '--show-toplevel')
        current = git(target, 'branch', '--show-current')
        if actual != str(target) or current != branch:
            raise RuntimeError(f'Refusing unowned worktree at {target}')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        git(repo, 'worktree', 'add', '-b', branch, str(target), 'HEAD')
    run['workdir'] = str(target)
    run['owned_worktree'] = {'path': str(target), 'branch': branch, 'repo': str(repo)}
