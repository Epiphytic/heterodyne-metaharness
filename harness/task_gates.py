"""Revision-bound native gates. Authority: spec/gates.md."""
import hashlib
import json

from .beads import BeadsError
from .task_delivery import identity
from .task_formulas import retained
from .task_order import resolve

FIELD = 'harness_gates'
KINDS = ('design', 'merge', 'deployment', 'external')


def metadata(issue):
    """Decode only known gate objects; leave unknown metadata and scope bytes alone."""
    from .blocker_receipts import decode
    result = dict(issue.get('metadata') or {})
    for key in (FIELD, 'harness_gate', 'harness_gate_resolution', 'harness_blocker_decision'):
        if key not in result:
            continue
        value = result[key]
        if isinstance(value, str):
            value = decode(value)
        if not isinstance(value, dict):
            raise BeadsError('Malformed gate metadata: ' + key)
        result[key] = value
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def scope(issue):
    """Exclude execution receipts and gate edges, retain task requirements and routing."""
    fields = ('title', 'description', 'notes', 'acceptance_criteria', 'design', 'spec_id', 'labels')
    value = {key: issue.get(key) for key in fields}
    fields_metadata = metadata(issue)
    value['metadata'] = {k: v for k, v in fields_metadata.items() if k not in
                         (FIELD, 'harness_lifecycle', 'harness_delivery_artifact', 'harness_queue_order')}
    gates = fields_metadata.get(FIELD, {})
    value['dependencies'] = sorted((e['id'], e.get('dependency_type'))
                                 for e in issue.get('dependencies', []) if e['id'] not in gates)
    return digest(value)


def write(queue, issue, key, value):
    queue.bd('update', issue['id'], '--set-metadata', key + '=' + json.dumps(value))
    fresh = queue.show(issue['id'])
    if metadata(fresh).get(key) != value:
        raise BeadsError('Gate write uncertain; inspect and replay exact request')
    return fresh


def create(queue, run, plan):
    required = {'key', 'issue_id', 'kind', 'revision', 'reason', 'issuer'}
    if isinstance(plan, dict):
        plan = dict(plan)
        supersedes = plan.pop('supersedes', None)
        blocker = plan.pop('blocker', None)
    else:
        supersedes = None
    if not isinstance(plan, dict) or set(plan) != required or not all(
            isinstance(v, str) and v.strip() for v in plan.values()) or plan['kind'] not in KINDS:
        raise BeadsError('Gate plan requires key/issue_id/kind/revision/reason/issuer')
    issue = resolve(queue, run, plan['issue_id'])
    if issue.get('status') == 'closed':
        raise BeadsError('Cannot gate a closed task')
    key = identity(run, 'gate:' + issue['id'] + ':' + plan['key'])
    bindings = dict(metadata(issue).get(FIELD, {}))
    if supersedes is not None:
        old = bindings.get(supersedes)
        if (not old or old['kind'] != plan['kind'] or supersedes == key
                or any(b.get('supersedes') == supersedes for k, b in bindings.items() if k != key)):
            raise BeadsError('Supersession requires one existing same-kind gate')
        if old.get('blocker') and not blocker:
            raise BeadsError('Signed blocker requires a signed replacement')
    binding = dict(plan, issue_id=issue['id'], scope=scope(issue), version=1)
    if blocker is not None:
        binding['blocker'] = blocker
    if supersedes is not None:
        binding['supersedes'] = supersedes
    if key in bindings and bindings[key] != binding:
        raise BeadsError('Gate key already binds different scope; use a new gate key for revalidation')
    bindings[key] = binding
    # Reserve before creation: missing gate/edge fails facade eligibility closed.
    issue = write(queue, issue, FIELD, bindings)
    found = queue.bd('list', '--id', key, '--all', '--limit', '0')
    if not found:
        queue.bd('create', '--id', key, '--type', 'gate', '--title', 'Gate: ' + plan['kind'],
                 '--description', plan['reason'], '--metadata', json.dumps({'harness_gate': binding}))
    gate = queue.show(key)
    if gate.get('issue_type') != 'gate' or metadata(gate).get('harness_gate') != binding:
        raise BeadsError('Native gate identity conflict')
    if not any(e.get('id') == key and e.get('dependency_type') == 'blocks' for e in issue.get('dependencies', [])):
        queue.bd('dep', 'add', issue['id'], key, '--type', 'blocks')
    if supersedes is not None:
        current = queue.show(issue['id'])
        if any(e.get('id') == supersedes and e.get('dependency_type') == 'blocks' for e in current.get('dependencies', [])):
            queue.bd('dep', 'remove', issue['id'], supersedes)
    return {'gate': queue.show(key), 'issue': queue.show(issue['id'])}


def design_evidence(queue, issue, evidence):
    metadata = issue.get('metadata') or {}
    approval_id = metadata.get('design_approval')
    if not approval_id or not metadata.get('adr_revision'):
        raise BeadsError('Design gate requires existing design approval and ADR revision')
    approval = queue.show(approval_id)
    value = approval.get('metadata') or {}
    if (approval.get('status') != 'closed' or value.get('approved_by') != 'Liam'
            or not value.get('approved_at') or value.get('adr_revision') != metadata['adr_revision']
            or set(value.get('brainstorm_models', [])) != {'GLM 5.3 Fast', 'GPT-6 Astra'}
            or evidence['revision'] != metadata['adr_revision']
            or not any(e.get('id') == approval_id and e.get('dependency_type') == 'blocks'
                       for e in issue.get('dependencies', []))):
        raise BeadsError('Separate human approval, two-model ADR and matching native dependency required')


def validate_evidence(queue, issue, binding, evidence):
    if not isinstance(evidence, dict) or any(evidence.get(k) != binding[k] for k in ('issue_id', 'kind', 'revision')):
        raise BeadsError('Gate evidence targets unrelated kind/task/revision')
    if evidence.get('scope') != binding['scope'] or scope(issue) != binding['scope']:
        raise BeadsError('Gate scope changed; revalidation required')
    if (evidence.get('authority_basis') not in ('operator', 'operator-reaction', 'verified-external')
            or not evidence.get('issuer') or not evidence.get('evidence_ref') or evidence.get('result') != 'passed'):
        raise BeadsError('Explicit resolution authority and passing evidence required')
    retained(evidence)
    if binding.get('blocker'):
        from .task_blockers import validate_signed
        validate_signed(binding, evidence)
    if binding['kind'] == 'design':
        design_evidence(queue, issue, evidence)
    if binding['kind'] in ('merge', 'deployment') and evidence.get('authority_basis') == 'verified-external':
        raise BeadsError('Merge/deployment resolution requires operator authority')


def resolve_gate(queue, run, gate_id, evidence):
    gate = queue.show(gate_id)
    binding = metadata(gate).get('harness_gate')
    if gate.get('issue_type') != 'gate' or not binding:
        raise BeadsError('Not a harness native gate')
    issue = resolve(queue, run, binding['issue_id'])
    if metadata(issue).get(FIELD, {}).get(gate_id) != binding:
        raise BeadsError('Gate is not bound to this routed task')
    validate_evidence(queue, issue, binding, evidence)
    decision = metadata(gate).get('harness_blocker_decision')
    if decision and any(decision['evidence'].get(k) != evidence.get(k)
                        for k in ('signed_receipt', 'decision_evidence')):
        raise BeadsError('Resolution conflicts with retained signed decision')
    resolution = {'evidence': evidence, 'digest': digest(evidence)}
    previous = metadata(gate).get('harness_gate_resolution')
    if previous and previous != resolution:
        raise BeadsError('Resolution evidence differs from retained record')
    if gate.get('status') == 'closed':
        if previous != resolution:
            raise BeadsError('Gate closed without matching resolution evidence')
        return gate
    write(queue, gate, 'harness_gate_resolution', resolution)
    queue.bd('gate', 'resolve', gate_id, '--reason', 'Verified facade evidence ' + resolution['digest'])
    fresh = queue.show(gate_id)
    if fresh.get('status') != 'closed':
        raise BeadsError('Gate resolution uncertain; inspect before exact replay')
    return fresh


def check(queue, issue):
    if issue.get('issue_type') == 'gate':
        raise BeadsError('Gates resolve through evidence; never claim them as coding work')
    bindings = metadata(issue).get(FIELD, {})
    superseded = {b['supersedes'] for b in bindings.values() if b.get('supersedes')}
    for key, binding in bindings.items():
        if key in superseded:
            continue
        if not queue.bd('list', '--id', key, '--all', '--limit', '0'):
            raise BeadsError('Reserved gate has not been created')
        gate = queue.show(key)
        if (gate.get('issue_type') != 'gate' or metadata(gate).get('harness_gate') != binding
                or not any(e.get('id') == key and e.get('dependency_type') == 'blocks'
                           for e in issue.get('dependencies', []))):
            raise BeadsError('Gate contract or blocking edge missing')
        if gate.get('status') != 'closed':
            raise BeadsError('Task has an unresolved gate')
        resolution = metadata(gate).get('harness_gate_resolution', {})
        if resolution.get('digest') != digest(resolution.get('evidence')):
            raise BeadsError('Gate closed without verified evidence')
        validate_evidence(queue, issue, binding, resolution['evidence'])


def configure(actions):
    gate = actions.add_parser('gate')
    commands = gate.add_subparsers(dest='gate_action', required=True)
    commands.add_parser('create').add_argument('--file', required=True)
    resolver = commands.add_parser('resolve')
    resolver.add_argument('gate_id')
    resolver.add_argument('--evidence-file', required=True)
    commands.add_parser('check').add_argument('issue_id')


def dispatch(args, store, beads, run):
    from pathlib import Path
    from .tasks import observe
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        raise BeadsError('Reconcile recovery before gate operations')
    queue = beads._queue(run)
    if args.gate_action == 'create':
        plan = json.loads(Path(args.file).read_text())
        if 'blocker' in plan:
            raise BeadsError('Use blocker create with configured signer authority')
        result = create(queue, run, plan)
        observe(store, result['issue'])
        from .transitions import gate
        gate(store, run, result['issue'], result['gate'])
        return result
    if args.gate_action == 'resolve':
        evidence = json.loads(Path(args.evidence_file).read_text())
        if metadata(queue.show(args.gate_id)).get('harness_gate', {}).get('blocker'):
            from .task_blockers import resolve as resolve_blocker
            return resolve_blocker(store, beads, run, args.gate_id, evidence, beads.config)
        result = resolve_gate(queue, run, args.gate_id, evidence)
        issue = queue.show(metadata(result)['harness_gate']['issue_id'])
        observe(store, issue)
        from .transitions import gate
        gate(store, run, issue, result, resolved=True)
        return result
    issue = resolve(queue, run, args.issue_id)
    check(queue, issue)
    return {'issue_id': issue['id'], 'gates_satisfied': True}
