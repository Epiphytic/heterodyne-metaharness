"""Deterministic bounded evidence collection. Authority: spec/reviews.md."""
import hashlib
import os
from pathlib import Path
import subprocess

LIMIT = 128 * 1024


def retained(path, expected=None):
    source = Path(path)
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise ValueError('Review evidence requires an absolute regular retained file')
    size = source.stat().st_size
    if size > 16 * 1024 * 1024:
        raise ValueError('Retained review artifact exceeds 16 MiB')
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise ValueError('Review artifact changed: ' + str(source))
    return {'path': str(source), 'sha256': digest, 'bytes': len(raw),
            'text': raw[:LIMIT].decode('utf-8', errors='replace'),
            'truncated': len(raw) > LIMIT}


def git_read(cwd, *args):
    """No external diff/textconv hooks; bounded captured bytes with explicit truncation."""
    import tempfile
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(['git', '--no-optional-locks', '-C', cwd, *args],
                                stdout=output, stderr=subprocess.PIPE, timeout=10,
                                env={**os.environ, 'GIT_EXTERNAL_DIFF': '',
                                     'GIT_PAGER': 'cat'}, check=False)
        if result.returncode:
            raise ValueError('Review Git read failed: ' + args[0])
        size = output.tell()
        output.seek(0)
        digest = hashlib.file_digest(output, 'sha256').hexdigest()
        output.seek(0)
        return {'text': output.read(LIMIT).decode('utf-8', errors='replace'),
                'bytes': size, 'sha256': digest, 'truncated': size > LIMIT}


def collect(run, handoff=None):
    from .channel_status import capture
    handoff = handoff or {}
    cwd = run['workdir']
    before = git_read(cwd, 'rev-parse', 'HEAD')['text'].strip()
    status = git_read(cwd, 'status', '--porcelain=v1', '--untracked-files=normal')
    diff = git_read(cwd, 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--')
    # Includes the committed deliverable even when the working tree is clean.
    base = run.get('task_worktrees', {}).get(run.get('beads', {}).get('issue_id'), {}).get('base')
    if base:
        committed = git_read(cwd, 'diff', '--no-ext-diff', '--no-textconv', base, before, '--')
    else:
        committed = git_read(cwd, 'show', '--format=fuller', '--no-ext-diff', '--no-textconv', before, '--')
    pane = capture(run, lines=500)
    selected = handoff.get('artifacts') or stage_artifacts(run)
    if len(selected) > 20:
        raise ValueError('Review artifact inventory exceeds 20; explicit bounded handoff required')
    artifacts = [retained(a['path'], a['sha256']) for a in selected]
    after = git_read(cwd, 'rev-parse', 'HEAD')['text'].strip()
    after_status = git_read(cwd, 'status', '--porcelain=v1', '--untracked-files=normal')
    after_diff = git_read(cwd, 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', '--')
    if before != after or status != after_status or diff != after_diff:
        raise ValueError('Working tree changed during review evidence collection; retry at a stable boundary')
    if handoff.get('commit') and handoff['commit'] != before:
        raise ValueError('Review handoff commit is stale')
    return {'commit': before, 'base': base, 'status': status, 'diff': diff, 'committed_diff': committed,
            'terminal_state': pane[0], 'terminal_tail': pane[1], 'terminal_limit': 500,
            'artifacts': artifacts,
            'limitations': ['Bounded evidence snapshot, not an atomic filesystem snapshot.',
                            'Untracked file names are included, contents require retained artifacts.',
                            'Task-base diff available.' if base else 'No task base recorded; committed diff covers HEAD only.']}


def stage_artifacts(run):
    """Use actual retained lifecycle logs, not tests claimed in terminal prose."""
    issue = run.get('beads', {}).get('task_snapshot', {}).get('issue', {})
    artifacts = {}
    for event in issue.get('metadata', {}).get('harness_lifecycle', []):
        evidence = event.get('evidence', {})
        for artifact in evidence.get('artifacts', []):
            artifacts[artifact['path']] = artifact
        if evidence.get('log_sha256') and evidence.get('evidence_ref'):
            path = evidence['evidence_ref']
            artifacts[path] = {'path': path, 'sha256': evidence['log_sha256']}
    return list(artifacts.values())
