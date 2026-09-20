"""Explicit clean-boundary interruption; contract: spec/queue-order.md."""
import hashlib
import json
import time
from types import SimpleNamespace

from .beads import BeadsError
from .task_order import prioritize, resolve
from .task_workspace import boundary, assign
from .workspace import git


def parked(run, queue, issue):
    record = run.get('task_parks', {}).get(issue['id'])
    if not record or record.get('state') != 'parked':
        return False
    from .task_review import checkout
    owned = checkout(run, issue['id'])
    if issue.get('status') != 'in_progress' or issue.get('assignee') != queue.worker:
        raise BeadsError('Parked claim ownership changed; reconcile explicitly')
    if git(owned['workdir'], 'status', '--porcelain') or git(owned['workdir'], 'rev-parse', 'HEAD') != record['commit']:
        raise BeadsError('Parked checkout changed; reconcile before switching')
    return True


def drop(store, supervisor, beads, run, value, issuer, reason):
    boundary(run)
    if not issuer.strip() or not reason.strip():
        raise BeadsError('Interruption requires issuer and reason records')
    if not run.get('beads', {}).get('pickup_enabled'):
        raise BeadsError('Pickup is paused; explicit resume required before interruption')
    queue = beads._queue(run)
    if (queue.state / 'paused').exists():
        raise BeadsError('Native pickup pause takes precedence')
    target = resolve(queue, run, value)
    current = run.get('beads', {}).get('issue_id')
    if target['id'] == current:
        return queue.show(current)  # Reconcile an already successful switch.
    if target['id'] not in {i['id'] for i in beads.ready(run)}:
        raise BeadsError('Requested task must pass current native ready/approval gates')
    if not current:
        raise BeadsError('No active claim to interrupt; use prioritize and claim')
    issue = queue.show(current)
    if issue.get('assignee') != queue.worker or issue.get('status') != 'in_progress':
        raise BeadsError('Only the verified current claim may be parked')
    if current not in run.get('task_worktrees', {}):
        raise BeadsError('Interruption requires an owned per-task worktree')
    # Validate other retained claims before any mutation.
    from .task_review import can_handoff
    for item in queue.bd('list', '--assignee', queue.worker, '--status', 'in_progress', '--limit', '0'):
        if item['id'] != current:
            can_handoff(run, queue, queue.show(item['id']))
    prioritize(queue, run, [target['id']], issuer)
    record = {'state': 'parked', 'commit': git(run['workdir'], 'rev-parse', 'HEAD'),
              'path': run['workdir'], 'issuer': issuer, 'reason': reason,
              'at': time.time(), 'next': target['id']}
    run.setdefault('task_parks', {})[current] = record
    supervisor.persist(run)  # Durable checkpoint before external claim/worktree switch.
    try:
        encoded = json.dumps(record, sort_keys=True)
        queue.bd('update', current, '--set-metadata', 'harness_interruption=' + encoded)
        if queue.show(current).get('metadata', {}).get('harness_interruption') != record:
            raise BeadsError('Interruption note uncertain; parked claim retained for reconciliation')
        with store.db:
            store.event(run, 'task_interrupted', encoded,
                        'task-interrupted:' + hashlib.sha256(encoded.encode()).hexdigest())
        return assign(SimpleNamespace(task_action='claim', issue_id=target['id']),
                      store, supervisor, beads, run)
    except Exception:
        run['beads'].update(recovery_required=True, pickup_enabled=False)
        run['resume_required'] = True
        supervisor.persist(run)
        raise



def resumed(run, issue_id):
    record = run.get('task_parks', {}).get(issue_id)
    if record and record.get('state') == 'parked':
        record.update(state='resumed', resumed_at=time.time())
