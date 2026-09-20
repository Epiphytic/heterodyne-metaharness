"""Owned review handoff without releasing claims; contract: spec/worktrees.md."""
import copy
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .beads import BeadsError
from .workspace import git


def checkout(run, issue_id):
    if issue_id == run.get('beads', {}).get('issue_id'):
        return run
    record = run.get('task_worktrees', {}).get(issue_id)
    if not record or not record.get('ready') or record.get('issue_id') != issue_id:
        raise BeadsError('No owned task checkout for review task')
    if git(record['path'], 'rev-parse', '--show-toplevel') != str(Path(record['path']).resolve()):
        raise BeadsError('Review checkout path changed')
    if git(record['path'], 'branch', '--show-current') != record['branch']:
        raise BeadsError('Review checkout branch changed')
    result = copy.deepcopy(run)
    result['workdir'] = record['path']
    result['beads']['issue_id'] = issue_id
    return result


def can_handoff(run, queue, issue):
    """Never treat a claimed task or unpushed implementation as a review task."""
    from .task_interrupt import parked
    if parked(run, queue, issue):
        return checkout(run, issue['id'])
    from .task_stages import STAGES, clean_commit
    if issue.get('status') != 'in_progress' or issue.get('assignee') != queue.worker:
        raise BeadsError('Review handoff requires the same verified owner')
    if issue['id'] not in run.get('task_worktrees', {}):
        raise BeadsError('Concurrent claims require an owned worktree for each task')
    history = issue.get('metadata', {}).get('harness_lifecycle') or []
    names = [event.get('stage') for event in history]
    if len(names) < 3 or names != list(STAGES[:len(names)]):
        raise BeadsError('Existing task must reach tested PR-open stage before handoff')
    for event in history:
        digest = hashlib.sha256(json.dumps([event['stage'], event['evidence']], sort_keys=True).encode()).hexdigest()
        if digest != event.get('digest'):
            raise BeadsError('Review lifecycle evidence digest mismatch')
    tested = history[1]['evidence']
    if tested.get('result') != 'passed' or tested.get('full_suite') is not True or not re.fullmatch(r'[a-f0-9]{64}', str(tested.get('log_sha256', ''))):
        raise BeadsError('Review handoff requires retained full-suite passing evidence')
    evidence = history[2]['evidence']
    url = urlparse(evidence.get('pr_url', ''))
    if url.scheme not in ('https', 'rad') or not url.netloc or not evidence.get('remote'):
        raise BeadsError('Review handoff requires a real pushed review destination')
    if evidence.get('commit') != history[1]['evidence'].get('commit') or evidence.get('pushed_commit') != evidence.get('commit'):
        raise BeadsError('Review commit was not tested and pushed')
    owned = checkout(run, issue['id'])
    latest = history[-1]['evidence']['commit']
    clean_commit(owned, latest)
    return owned


def claim_concurrent(beads, run, queue, issue_id):
    """Reuse native ready/design/atomic claim gates; only relax reviewed claims."""
    active = queue.bd('list', '--assignee', queue.worker, '--status', 'in_progress', '--limit', '0')
    if not active:
        return beads._call(run, 'claim', issue_id)
    from .task_workspace import boundary
    boundary(run)
    for item in active:
        can_handoff(run, queue, queue.show(item['id']))
    if issue_id not in {item['id'] for item in queue.ready() if beads._allowed(queue, item)}:
        raise BeadsError('Task is not eligible for this worker')
    queue.bd('update', issue_id, '--claim')
    result = queue.show(issue_id)
    if result.get('assignee') != queue.worker or result.get('status') != 'in_progress' or not beads._allowed(queue, result):
        raise BeadsError('Concurrent claim uncertain; inspect ownership and approval before retry')
    (queue.state / 'current').write_text(issue_id)
    return result
