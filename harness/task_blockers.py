"""Signed blocker gates using the existing Beads and operator-ask engines."""
import copy
import hashlib
import json
import time

from . import blocker_receipts as receipts, task_gates as gates
from .beads import BeadsError
from .task_delivery import identity


def policy(config, name):
    settings = config.get('beads', config).get('blockers', {})
    value = settings.get('policies', {}).get(name)
    if settings.get('enabled') is not True or not isinstance(value, dict):
        raise BeadsError('Blocker policy is not explicitly enabled')
    if not value.get('signers') or not value.get('revision') or type(value.get('event_kind')) is not int:
        raise BeadsError('Blocker policy requires signers, revision and event_kind')
    return value


def create(store, beads, run, plan, config):
    required = {'key', 'issue_id', 'kind', 'revision', 'reason', 'issuer', 'category', 'assignee', 'policy'}
    if not isinstance(plan, dict) or set(plan) - {'supersedes'} != required:
        raise BeadsError('Blocker plan schema mismatch')
    selected = policy(config, plan['policy'])
    if plan['kind'] not in selected.get('kinds', []):
        raise BeadsError('Policy cannot approve this gate kind')
    if plan['kind'] in ('merge', 'deployment') and selected.get('authority_basis') != 'operator':
        raise BeadsError('Merge/deployment blocker requires operator policy')
    if plan['category'] not in ('approval', 'question', 'access-validation', 'external-result', 'pr-review', 'review'):
        raise BeadsError('Unknown blocker category')
    if plan['category'] == 'review' and plan['kind'] != 'external':
        raise BeadsError('Artifact review cannot authorize a native execution gate')
    owner = plan['assignee']
    if not isinstance(owner, dict) or set(owner) != {'type', 'id'} or owner['type'] not in ('human', 'agent', 'worker', 'external'):
        raise BeadsError('Blocker needs a typed assignee')
    route = config.get('beads', config).get('blockers', {}).get('assignees', {}).get(owner['id'])
    if not isinstance(route, dict) or route.get('type') != owner['type']:
        raise BeadsError('Assignee must have a configured route')
    blocker = dict(version=1, category=plan['category'], assignee=owner, policy=plan['policy'],
                   policy_snapshot=copy.deepcopy(selected), run_id=run['id'], repository=run.get('repo'),
                   nonce=identity(run, 'blocker:' + plan['issue_id'] + ':' + plan['key']))
    gate_plan = {k: plan[k] for k in ('key', 'issue_id', 'kind', 'revision', 'reason', 'issuer')}
    if 'supersedes' in plan:
        gate_plan['supersedes'] = plan['supersedes']
    gate_plan['blocker'] = blocker
    result = gates.create(beads._queue(run), run, gate_plan)
    gate = result['gate']
    route_ask(store, run, gate, route)
    from .tasks import observe
    observe(store, gate)
    observe(store, result['issue'])
    return result


def route_ask(store, run, gate, route):
    binding = gates.metadata(gate)['harness_gate']
    if binding['blocker']['category'] == 'review':
        return  # The review engagement dispatches signing only after a pass.
    owner = binding['blocker']['assignee']
    text = (f"Blocker {gate['id']} for {binding['issue_id']}: {binding['reason']}\n"
            f"Assigned to {owner['type']}:{owner['id']}; revision {binding['revision']}. "
            'Return a signed decision for the exact retained request. Consent is not execution evidence.')
    if owner['type'] == 'human' or route.get('via') == 'marmot':
        from .operator_asks import enqueue
        enqueue(store, run, text, 'blocker:' + gate['id'], binding['reason'])
        return
    # Explicit registered destination; inbox delivery owns provider/native guards.
    target_run = store.get(route['run_id'])
    target = route.get('target', 'manager')
    if target not in ('manager', 'worker'):
        raise BeadsError('Unsupported blocker dispatch target')
    key = 'blocker-dispatch:' + gate['id']
    with store.db:
        previous = store.db.execute('SELECT run_id,text,target FROM inbox WHERE id=?', (key,)).fetchone()
        if previous and tuple(previous) != (target_run['id'], text, target):
            raise BeadsError('Blocker dispatch route changed')
        store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES(?,?,?,?,?,?)',
                         (key, target_run['id'], text, time.time(), 'pending', target))


def expected(binding):
    blocker = binding['blocker']
    return dict(schema='harness.blocker-decision.v1', domain='workstream-blocker',
                run_id=blocker['run_id'], repository=blocker['repository'],
                blocker_id=identity({'id': blocker['run_id']}, 'gate:' + binding['issue_id'] + ':' + binding['key']),
                issue_id=binding['issue_id'], scope=binding['scope'], revision=binding['revision'],
                kind=binding['kind'], nonce=blocker['nonce'], policy_revision=blocker['policy_snapshot']['revision'])


def validate_signed(binding, evidence, now=None):
    event = evidence.get('signed_receipt')
    unsigned = evidence.get('decision_evidence')
    if not isinstance(unsigned, dict) or not unsigned:
        raise BeadsError('Retained decision evidence required')
    body = receipts.verify(event, unsigned, expected(binding), binding['blocker']['policy_snapshot'],
                           time.time() if now is None else now)
    if evidence.get('authority_basis') != binding['blocker']['policy_snapshot'].get('authority_basis'):
        raise BeadsError('Resolution authority differs from signer policy')
    decision = 'answer' if binding['blocker']['category'] == 'question' else 'approve'
    if body['decision'] != decision:
        raise BeadsError('Decision does not resolve this blocker')
    if binding['blocker']['category'] == 'access-validation':
        effect = evidence.get('native_effect_ref')
        if not effect or effect not in [a.get('path') for a in evidence.get('artifacts', [])]:
            raise BeadsError('Access approval requires independently retained native effect evidence')
    return body


def resolve(store, beads, run, gate_id, evidence, config):
    queue = beads._queue(run)
    gate = queue.show(gate_id)
    binding = gates.metadata(gate).get('harness_gate', {})
    blocker = binding.get('blocker')
    if not blocker:
        raise BeadsError('Not a signed blocker gate')
    current = policy(config, blocker['policy'])
    if current != blocker['policy_snapshot']:
        raise BeadsError('Decision policy changed; explicit gate revalidation required')
    validate_signed(binding, evidence)
    receive(store, beads, run, gate_id, {k: evidence[k] for k in
        ('signed_receipt', 'decision_evidence')}, config)
    result = gates.resolve_gate(queue, run, gate_id, evidence)
    ask_key = 'operator-ask:' + hashlib.sha256(
        json.dumps([run['id'], 'blocker:' + gate_id]).encode()).hexdigest()
    from .operator_asks import initialize, resolve as resolve_ask
    initialize(store.db)
    if store.db.execute('SELECT 1 FROM operator_asks WHERE id=?', (ask_key,)).fetchone():
        resolve_ask(store, run, ask_key, receipts.digest(evidence))
    from .tasks import observe
    observe(store, result)
    observe(store, queue.show(binding['issue_id']))
    return result


def check_policies(issue, config):
    bindings = gates.metadata(issue).get(gates.FIELD, {})
    superseded = {b['supersedes'] for b in bindings.values() if b.get('supersedes')}
    for key, binding in bindings.items():
        if key in superseded:
            continue
        blocker = binding.get('blocker')
        if blocker and policy(config, blocker['policy']) != blocker['policy_snapshot']:
            raise BeadsError('Blocker authority policy changed')


def assert_worker_unblocked(beads, run):
    issue_id = run.get('beads', {}).get('issue_id')
    if not beads.enabled or not issue_id:
        return
    queue = beads._queue(run)
    issue = queue.show(issue_id)
    # Preserve legacy input semantics for tasks without this opt-in extension.
    if not any(b.get('blocker') for b in gates.metadata(issue).get(gates.FIELD, {}).values()):
        return
    check_policies(issue, beads.config)
    # Artifact review gates hold lifecycle advancement, not revision input.
    gates.check(queue, issue, allow_review_edits=True)


def receive(store, beads, run, gate_id, evidence, config):
    """Retain authenticated consent without closing a gate or typing native keys."""
    queue = beads._queue(run)
    gate = queue.show(gate_id)
    binding = gates.metadata(gate).get('harness_gate', {})
    blocker = binding.get('blocker', {})
    if blocker.get('run_id') != run['id']:
        raise BeadsError('Not this run\'s blocker')
    current = policy(config, blocker['policy'])
    if current != blocker['policy_snapshot']:
        raise BeadsError('Decision policy changed; revalidation required')
    parent = queue.show(binding['issue_id'])
    if gates.metadata(parent).get(gates.FIELD, {}).get(gate_id) != binding or gates.scope(parent) != binding['scope']:
        raise BeadsError('Decision scope changed')
    body = receipts.verify(evidence.get('signed_receipt'), evidence.get('decision_evidence'),
                           expected(binding), current, time.time())
    phase = {'approve': 'approved', 'answer': 'resolved', 'deny': 'denied'}[body['decision']]
    record = dict(phase=phase, evidence=evidence, digest=receipts.digest(evidence))
    prior = gates.metadata(gate).get('harness_blocker_decision')
    if prior and prior != record:
        raise BeadsError('Conflicting decision; create a replacement gate')
    fresh = gates.write(queue, gate, 'harness_blocker_decision', record) if not prior else gate
    from .tasks import observe
    observe(store, fresh)
    return fresh
