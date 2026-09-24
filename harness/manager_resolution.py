"""Signed manager outcomes, independently verified before Beads closure."""
import json
import os
import time

from . import blocker_receipts as receipts, manager_tasks as tasks
from .beads import BeadsError


def lookup(store, run, issue_id):
    tasks.initialize(store)
    row = store.db.execute('SELECT * FROM manager_tasks WHERE run_id=? AND issue_id=?',
                           (run['id'], issue_id)).fetchone()
    if not row or row['dispatched_at'] is None:
        raise BeadsError('No dispatched manager request for this run/bead')
    request = json.loads(row['request'])
    if request['kind'] == 'native_approval' and request['subject'] == 'manager' and row['operator_hold']:
        return row  # The blocked manager could not receive its own pickup turn.
    inbox = store.db.execute('SELECT state FROM inbox WHERE id=?', (row['request_id'],)).fetchone()
    if not inbox or inbox['state'] != 'submitted':
        raise BeadsError('Manager delivery must be confirmed before resolution')
    return row


def verification(store, beads, run, row):
    request = json.loads(row['request'])
    if request['kind'] == 'native_approval':
        from .permission_relay import initialize
        initialize(store.db)
        relay = store.db.execute('SELECT * FROM permission_relays WHERE id=? AND run_id=?',
                                 (request['source'], run['id'])).fetchone()
        if (not relay or relay['pane_id'] != request['pane_id']
                or relay['native_id'] != request['native_session_id']
                or relay['evidence'] != request['evidence']):
            raise BeadsError('Native approval evidence binding changed')
        subject = run.get('manager', {}) if request['subject'] == 'manager' else run
        observation = subject.get('observation', {})
        if (subject.get('pane_id') != relay['pane_id']
                or (subject.get('native_session_id') or 'unknown') != relay['native_id']
                or subject.get('observed_state') == 'awaiting_approval'
                or observation.get('state') == 'awaiting_approval'
                or not observation.get('pane_alive')
                or observation.get('at', 0) <= row['dispatched_at']):
            raise BeadsError('Fresh post-action native observation required')
        return {'relay_id': relay['id'], 'pane_id': relay['pane_id'],
                'native_session_id': relay['native_id'], 'observation': observation,
                'observed_at_ms': int(observation['at'] * 1000)}
    if request['kind'] == 'approval':
        from . import task_gates as gates
        queue = beads._queue(run)
        gate = queue.show(request['source'])
        metadata = gates.metadata
        result = metadata(gate).get('harness_gate_resolution')
        if gate.get('status') != 'closed' or not result:
            raise BeadsError('Approval must resolve through its existing signed gate policy first')
        binding = metadata(gate)['harness_gate']
        if result.get('digest') != gates.digest(result.get('evidence')):
            raise BeadsError('Approval gate closed without verified evidence')
        gates.validate_evidence(queue, queue.show(binding['issue_id']), binding, result['evidence'])
        return {'gate_id': gate['id'], 'resolution': result}
    finding = store.db.execute('SELECT * FROM babysitter_findings WHERE id=?', (request['source'],)).fetchone()
    if (not finding or finding['episode'] != request['episode'] or finding['phase'] != 'resolved'
            or finding['observed_at'] <= row['dispatched_at']):
        raise BeadsError('Fresh independent post-dispatch verification required')
    return {'source': finding['id'], 'episode': finding['episode'], 'sequence': finding['sequence'],
            'observed_at_ms': int(finding['observed_at'] * 1000), 'evidence': finding['evidence']}


def evidence(store, beads, run, issue_id):
    row = lookup(store, run, issue_id)
    proof = verification(store, beads, run, row)
    return {'schema': 'harness.manager-resolution.v1', 'run_id': run['id'], 'issue_id': issue_id,
            'request_sha256': receipts.digest(json.loads(row['request'])),
            'verification': proof, 'verification_sha256': receipts.digest(proof)}


def validate(event, expected, config, now):
    receipts.verify_event(event)
    policy = config.get('manager_tasks', {})
    if (event['pubkey'] not in policy.get('signers', []) or event['pubkey'] in policy.get('revoked', [])
            or event['kind'] != policy.get('event_kind')):
        raise BeadsError('Manager resolution signer/kind not authorized by configured policy')
    body = receipts.decode(event['content'])
    bound = {k: v for k, v in expected.items() if k != 'verification'}
    if (not isinstance(body, dict) or set(body) != set(bound) | {'resolution', 'action_completed_at_ms'}
            or any(body.get(k) != v for k, v in bound.items())):
        raise BeadsError('Signed resolution belongs to a different request or verification')
    if not isinstance(body['resolution'], str) or not body['resolution'].strip():
        raise BeadsError('Full resolution text required')
    action = body['action_completed_at_ms']
    observed = expected['verification'].get('observed_at_ms', event['created_at'] * 1000)
    if (type(action) is not int or not 0 <= action < observed
            or not observed <= event['created_at'] * 1000 + 999
            or not now - 86400 <= event['created_at'] <= now):
        raise BeadsError('Verification must follow action and precede a current signed resolution')
    return body


def resolve(store, beads, run, issue_id, event, config, now=None):
    now = time.time() if now is None else now
    row = lookup(store, run, issue_id)
    queue, issue = tasks.owned(beads, run, row)
    if issue.get('assignee') != queue.worker:
        raise BeadsError('Manager ownership changed; inspect instead of reclaiming')
    expected = evidence(store, beads, run, issue_id)
    if row['resolution']:
        retained = receipts.decode(row['resolution'])
        if retained['signed_event'] != event:
            raise BeadsError('Resolution already pinned; replay exact event')
        # Fresh healthy verification above still gates closure; retain the proof
        # originally signed when later healthy observations advance the sequence.
        expected['verification'] = retained['verification']
        expected['verification_sha256'] = receipts.digest(retained['verification'])
    body = validate(event, expected, config, now)
    request = json.loads(row['request'])
    if request['kind'] == 'native_approval' and request.get('capture_complete') is not True:
        raise BeadsError('Captured approval is incomplete; inspect live dialog and escalate')
    if request['kind'] == 'native_approval' and request['evidence'] not in body['resolution']:
        raise BeadsError('Signed resolution must include the full captured approval text')
    if body['action_completed_at_ms'] < int(row['dispatched_at'] * 1000):
        raise BeadsError('Resolution action predates manager dispatch')
    receipt = receipts.canonical({'signed_event': event, 'verification': expected['verification']})
    if row['resolution'] and row['resolution'] != receipt:
        raise BeadsError('Resolution already pinned; replay exact event')
    # Reserve the exact receipt before external writes; uncertain outcomes reconcile by event ID.
    with store.db:
        store.db.execute('UPDATE manager_tasks SET resolution=? WHERE request_id=?', (receipt, row['request_id']))
    if request['kind'] == 'native_approval':
        path = store.root / 'runs' / run['id'] / 'approval-log.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        prior = path.read_text().splitlines() if path.exists() else []
        record = {'schema': 'harness.native-approval-resolution.v1', 'relay_id': request['source'],
                  'issue_id': issue_id, 'event': event, 'captured_approval': request['evidence'],
                  'verification': expected['verification']}
        if not any(json.loads(line).get('event', {}).get('id') == event['id'] for line in prior):
            with path.open('a') as audit:
                audit.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + '\n')
                audit.flush()
                os.fsync(audit.fileno())
    marker = 'manager-resolution:' + event['id']
    comment = marker + '\n' + body['resolution'] + '\n' + receipt
    comments = queue.bd('comments', issue_id)
    if not any(comment == item.get('text') for item in comments):
        queue.bd('comments', 'add', issue_id, comment)
    if not any(comment == item.get('text') for item in queue.bd('comments', issue_id)):
        raise BeadsError('Signed comment write unconfirmed; bead remains open')
    if issue.get('status') != 'closed':
        queue.bd('close', issue_id, '--reason', marker)
    result = queue.show(issue_id)
    if result.get('status') != 'closed':
        raise BeadsError('Close outcome unconfirmed; inspect and replay exact signed event')
    from .tasks import observe
    observe(store, result)
    return result


def escalate(store, run, issue_id, reason):
    row = lookup(store, run, issue_id)
    if not reason.strip():
        raise BeadsError('Escalation needs the concrete authority or dangerous operation')
    request = json.loads(row['request'])
    detail = ''
    if request['kind'] == 'native_approval':
        detail = (f"\nRun: {run['id']}; native session: {request['native_session_id']}; "
                  f"pane: {request['pane_id']}.\nCaptured native approval region "
                  '(inspect live dialog for complete command):\n' + request['evidence'])
    from .operator_asks import enqueue
    result = enqueue(store, run, f'Manager bead {issue_id} requires Liam: {reason}' + detail,
                   'manager-task:' + issue_id, reason)
    with store.db:
        store.db.execute('UPDATE manager_tasks SET operator_hold=1 WHERE issue_id=? AND run_id=?', (issue_id, run['id']))
    return result


def configure(sub):
    cmd = sub.add_parser('manager-task')
    actions = cmd.add_subparsers(dest='manager_action', required=True)
    actions.add_parser('pickup')
    for name in ('boundary', 'evidence', 'resolve', 'escalate', 'approval'):
        action = actions.add_parser(name)
        action.add_argument('name')
        if name != 'boundary':
            action.add_argument('issue_id')
        if name in ('resolve', 'escalate'):
            action.add_argument('--file', required=True)


def dispatch(args, store, supervisor, config):
    from pathlib import Path
    action = args.manager_action
    if action == 'pickup':
        return tasks.pickup(store, supervisor)
    run = store.get(args.name)
    if action == 'boundary':
        return tasks.boundary(run)
    if action == 'approval':
        return {'request_id': tasks.approval(store, supervisor.beads, run, args.issue_id)}
    if action == 'evidence':
        return evidence(store, supervisor.beads, run, args.issue_id)
    raw = Path(args.file).read_text()
    if action == 'escalate':
        return escalate(store, run, args.issue_id, raw)
    return resolve(store, supervisor.beads, run, args.issue_id, receipts.decode(raw), config)
