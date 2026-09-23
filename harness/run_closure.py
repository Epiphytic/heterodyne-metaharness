"""Explicit terminal transitions; authority: spec/sessions.md."""
import hashlib
import json

from .beads import BeadsError
from .task_delivery import contract, steps

LIMIT = 200


def normalized(issue):
    """bd may round-trip individual metadata values as JSON strings."""
    issue = dict(issue)
    metadata = issue.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise BeadsError('Invalid closure metadata')
    metadata = dict(metadata)
    for key in ('harness_delivery', 'harness_lifecycle'):
        if isinstance(metadata.get(key), str):
            metadata[key] = json.loads(metadata[key])
    issue['metadata'] = metadata
    return issue


def issues(beads, run):
    if not run.get('beads'):
        return []
    queue = beads._queue(run)  # Disabled/unavailable integration must fail closed.
    listed = queue.bd('list', '--all', '--label', 'ws:' + queue.ws,
                      '--limit', str(LIMIT + 1), '--readonly')
    if not isinstance(listed, list) or len(listed) > LIMIT:
        raise BeadsError('Closure queue exceeds bounded read')
    keys = {item['id'] for item in listed}
    bound = run['beads'].get('issue_id')
    if bound:
        keys.add(bound)
    found = {}
    while keys:
        key = keys.pop()
        if key in found:
            continue
        if len(found) >= LIMIT:
            raise BeadsError('Closure delivery graph exceeds bounded read')
        issue = normalized(queue.show(key))
        if issue['id'] != key:
            raise BeadsError('Closure issue identity mismatch')
        found[key] = issue
        binding = contract(issue)
        if binding:
            keys.update(set(binding['members'].values()) - found.keys())
    return list(found.values())


def pending_deployment(issue):
    binding = contract(issue)
    if binding:
        profile = steps(binding)
        deployment_roles = {role for role, stages in profile.items() if 'deployed' in stages}
        if not deployment_roles:
            return False
        if binding['role'] in deployment_roles and issue.get('status') != 'closed':
            return True
        # Linked roles are inspected separately; review history is not deployment history.
        return False
    history = issue['metadata'].get('harness_lifecycle', [])
    if not isinstance(history, list):
        raise BeadsError('Invalid lifecycle history')
    stages = [event['stage'] for event in history]
    return bool(stages) and stages[-1] in ('merged', 'final-tested')


def authorize(supervisor, run, action, consent, force):
    """Validate fresh state before mutation; local CLI records operator attestation."""
    if not isinstance(consent, dict):
        raise ValueError('Explicit operator consent record required')
    if (consent.get('run_id') != run['id'] or consent.get('action') != action
            or type(consent.get('force')) is not bool or consent['force'] != force):
        raise ValueError('Consent must match run, action and force')
    for field in ('approved_by', 'response', 'evidence_ref'):
        if not isinstance(consent.get(field), str) or not consent[field].strip():
            raise ValueError('Operator consent requires ' + field)
    encoded = json.dumps(consent, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError('Consent exceeds evidence bound')
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    target = 'completed' if action == 'close' else 'stopped'
    prior = run.get('closure') or {}
    if prior.get('digest') == digest and run['state'] == target:
        return prior
    if digest in run.get('consumed_closure_consents', []):
        raise ValueError('Closure consent already consumed; obtain a new operator decision')
    db = supervisor.store.db
    from .operator_asks import initialize
    initialize(db)
    if db.execute('SELECT 1 FROM operator_asks WHERE run_id=? AND resolved_at IS NULL LIMIT 1',
                  (run['id'],)).fetchone():
        raise ValueError('Unresolved operator asks prevent closure')
    tasks = issues(supervisor.beads, run)
    if any(pending_deployment(issue) for issue in tasks):
        raise ValueError('Pending deployments prevent closure, including with force')
    outstanding = sorted(i['id'] for i in tasks if i.get('status') != 'closed')
    if outstanding and not force:
        raise ValueError('Nonclosed beads prevent closure; explicit force consent required')
    if consent.get('open_beads') != outstanding:
        raise ValueError('Consent must identify the exact current open_beads list')
    return dict(digest=digest, consent=consent, at=supervisor.clock(), state=target,
                inspected_beads=sorted(i['id'] for i in tasks))


def terminate(supervisor, run, action, consent=None, force=False):
    record = authorize(supervisor, run, action, consent, force)
    run['closure'] = record
    used = run.setdefault('consumed_closure_consents', [])
    if record['digest'] not in used:
        used.append(record['digest'])
    run['state'] = record['state']
    # Durable state and full attestation precede process effects. Retry may reap panes.
    supervisor.report(run, run['state'],
                      'Operator authorized workstream ' + action + '. History and task claims retained.',
                      run['id'] + ':closure:' + record['digest'])
    supervisor.persist(run)
    supervisor.tmux.stop(run)
    if run.get('manager'):
        supervisor.tmux.stop(run['manager'])
    return run
