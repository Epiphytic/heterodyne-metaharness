"""Bounded workflow views, never task authority. See spec/workflow-routing.md."""
import json
import sqlite3
from pathlib import Path

from .beads import Beads, BeadsError
from .task_delivery import contract
from .task_order import order_key

LIMIT = 200


def routed(run, issue):
    labels = issue.get('labels', [])
    route = {p: [v[len(p):] for v in labels if v.startswith(p)]
             for p in ('agent:', 'ws:', 'session:')}
    return (route['agent:'] == [run['agent']]
            and route['ws:'] == [run.get('beads', {}).get('workstream')]
            and route['session:'] in ([], [run['id']]))


def category(issue, lookup):
    """Workflow position is distinct from execution eligibility or approval."""
    binding = contract(issue)
    metadata = issue.get('metadata') or {}
    gates = metadata.get('harness_gates') or {}
    superseded = {v['supersedes'] for v in gates.values() if v.get('supersedes')}
    approvals = [key for key in gates if key not in superseded]
    if metadata.get('design_approval'):
        approvals.append(metadata['design_approval'])
    if any(lookup(key).get('status') != 'closed' for key in approvals):
        return 'awaiting-approval'
    blockers = [e['id'] for e in issue.get('dependencies', [])
                if e.get('dependency_type') == 'blocks'
                and lookup(e['id']).get('status') != 'closed']
    if blockers or issue.get('status') not in ('open', 'in_progress'):
        return 'blocked'
    if issue.get('status') == 'in_progress':
        return 'executing'
    if binding:
        role = binding['role']
        if role == 'parent':
            if any(lookup(key).get('status') != 'closed' for r, key in binding['members'].items() if r != 'parent'):
                return 'blocked'
        if role == 'review':
            return 'awaiting-review'
        if role in ('deployment', 'verification', 'integration'):
            return 'awaiting-deployment'
    return 'ready'


def build(run, issues, lookup, eligible=None):
    selected = sorted((i for i in issues if routed(run, i) and i.get('status') != 'closed'), key=order_key)
    if len(selected) > LIMIT:
        raise BeadsError('Progress exceeds 200 routed tasks; narrow the workstream')
    rows = []
    for issue in selected:
        try:
            status = category(issue, lookup)
            binding = contract(issue)
            blocked = [e['id'] for e in issue.get('dependencies', [])
                       if e.get('dependency_type') == 'blocks' and lookup(e['id']).get('status') != 'closed']
            if eligible is not None and issue['id'] in eligible and status == 'awaiting-approval':
                status = 'ready'
            row = {'id': issue['id'], 'title': issue['title'], 'status': issue['status'],
                   'view': status, 'assignee': issue.get('assignee'), 'blocked_by': blocked,
                   'role': binding['role'] if binding else None,
                   'parent': binding['members']['parent'] if binding else None,
                   'artifact': (issue.get('metadata') or {}).get('harness_delivery_artifact'),
                   'eligible': issue['id'] in eligible if eligible is not None else None}
        except (BeadsError, KeyError) as exc:
            row = {'id': issue['id'], 'title': issue['title'], 'view': 'blocked',
                   'eligible': False, 'reason': str(exc)}
        rows.append(row)
    return {'read_only': True, 'source': 'native-beads' if eligible is not None else 'cached-projection',
            'claim_requires_live_validation': True, 'tasks': rows}


def read(home, name, config=None):
    path = Path(home).expanduser().resolve() / 'workstreams/harness.sqlite3'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT data FROM runs WHERE id=? OR name=?', (name, name)).fetchone()
        if not row:
            row = db.execute('SELECT runs.data FROM runs JOIN identities ON runs.id=identities.run_id WHERE alias=?', (name,)).fetchone()
        if not row:
            raise BeadsError('Unknown workstream')
        run = json.loads(row[0])
        if config is not None:
            return live(run, Beads(config.get('beads', {})))
        # Bound scan; never initialize Store or update a checkpoint from a read.
        count = db.execute('SELECT COUNT(*) FROM task_current').fetchone()[0]
        if count > 5000:
            raise BeadsError('Cached progress exceeds bounded scan; use --live')
        cache = {r[0]: json.loads(r[1]) for r in db.execute('SELECT issue_id,data FROM task_current')}
        def lookup(key):
            if key not in cache:
                raise BeadsError('Missing cached dependency; use --live')
            return cache[key]
        return build(run, cache.values(), lookup)


def live(run, beads):
    queue = beads._queue(run)
    listed = queue.bd('list', '--all', '--label', 'agent:' + queue.agent,
                      '--label', 'ws:' + queue.ws, '--limit', str(LIMIT + 1), '--readonly')
    if len(listed) > LIMIT:
        raise BeadsError('Native progress exceeds 200 tasks; narrow the workstream')
    cache = {}
    def lookup(key):
        if key not in cache:
            if len(cache) >= LIMIT * 4:
                raise BeadsError('Native progress exceeds dependency read bound')
            cache[key] = queue.bd('show', key, '--readonly')[0]
        return cache[key]
    issues = [lookup(i['id']) for i in listed if routed(run, i) and i.get('status') != 'closed']
    held = (run.get('resume_required') or run.get('beads', {}).get('recovery_required')
            or run.get('beads', {}).get('pickup_enabled') is False or (queue.state / 'paused').exists())
    ready = [] if held else queue.bd('ready', '--label', 'agent:' + queue.agent,
                                    '--label', 'ws:' + queue.ws, '--unassigned',
                                    '--limit', str(LIMIT + 1), '--readonly')
    if len(ready) > LIMIT:
        raise BeadsError('Ready view exceeds bounded read')
    eligible = {i['id'] for i in ready if beads._allowed(queue, lookup(i['id']))}
    return build(run, issues, lookup, eligible)
