"""Artifact review engagements; signed decisions remain manager-owned.

Authority: spec/review-gate.md. Model output is evidence, never signing authority.
"""
import hashlib
import json
import time

from . import reviews, task_blockers as blockers, task_gates as gates
from .beads import BeadsError
from .review_evidence import retained
from .review_runner import atomic


def settings(config):
    raw = config.get('review_gate', {})
    if not isinstance(raw, dict):
        raise BeadsError('Review gate configuration must be an object')
    value = dict(raw)
    if value.get('enabled') is not True:
        raise BeadsError('Review gate is not enabled')
    value.setdefault('model', 'fuelix/claude-opus-5-5')
    value.setdefault('auto_approve', True)
    value.setdefault('max_attempts', 3)
    value.setdefault('signer', 'manager')
    value.setdefault('policy', 'manager')
    if (not isinstance(value['model'], str) or not value['model'].strip()
            or type(value['auto_approve']) is not bool
            or type(value['max_attempts']) is not int or not 1 <= value['max_attempts'] <= 10
            or any(not isinstance(value.get(k), str) or not value[k].strip()
                   for k in ('signer', 'policy'))):
        raise BeadsError('Invalid review gate model, policy or attempt budget')
    if value.get('api_mode') not in (None, 'anthropic_messages', 'chat_completions', 'responses'):
        raise BeadsError('Unsupported review API mode')
    if any(k in value and (not isinstance(value[k], str) or not value[k].strip())
           for k in ('provider', 'base_url', 'api_key_env')):
        raise BeadsError('Invalid review provider configuration')
    if 'api_key' in value:
        raise BeadsError('Use api_key_env; review policies must not contain credentials')
    return value


def artifacts(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise BeadsError('Review needs 1..20 retained artifacts')
    if any(not isinstance(item, dict) or set(item) != {'path', 'sha256'} for item in items):
        raise BeadsError('Artifacts require exact path and sha256 pairs')
    values = [retained(item['path'], item['sha256']) for item in items]
    if any(value['truncated'] for value in values):
        raise BeadsError('Review artifact exceeds evidence bound; partition explicitly')
    return values


def owned(beads, run, issue_id):
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        raise BeadsError('Reconcile recovery before review')
    issue = beads.show(run, issue_id)
    if (run.get('beads', {}).get('issue_id') != issue_id or issue.get('status') != 'in_progress'
            or issue.get('assignee') != beads._queue(run).worker):
        raise BeadsError('Artifact review requires current task ownership')
    return issue


def job_for(store, run, key):
    reviews.initialize(store.db)
    job = store.db.execute("SELECT * FROM review_jobs WHERE id=? AND run_id=? AND kind='artifact'",
                           (key, run['id'])).fetchone()
    if job is None:
        raise BeadsError('Unknown artifact engagement')
    return job


def submit(store, beads, run, request, config):
    reviews.initialize(store.db)
    policy = settings(config)
    required = {'key', 'issue_id', 'artifact_kind', 'artifacts'}
    if not isinstance(request, dict) or set(request) - {'previous'} != required:
        raise BeadsError('Review requires key/issue_id/artifact_kind/artifacts')
    if not isinstance(request['key'], str) or not request['key'].strip():
        raise BeadsError('Review requires a nonempty idempotency key')
    if request['artifact_kind'] not in ('design', 'pr', 'escalation'):
        raise BeadsError('Unsupported review artifact kind')
    issue = owned(beads, run, request['issue_id'])
    captured = artifacts(request['artifacts'])
    attempt, prior_gate = 1, None
    if request.get('previous'):
        old = job_for(store, run, request['previous'])
        prior = json.loads(old['request'])
        if (old['issue_id'] != issue['id'] or old['state'] not in ('changes-requested', 'superseded', 'stale')
                or prior['artifact_kind'] != request['artifact_kind']):
            raise BeadsError('Revision requires findings on the same task and artifact kind')
        attempt, prior_gate = prior['attempt'] + 1, prior['gate_id']
        if attempt > policy['max_attempts']:
            raise BeadsError('Review attempt budget exhausted; operator disposition required')
    revision = gates.digest(request['artifacts'])
    plan = dict(key='review:' + request['key'], issue_id=issue['id'], kind='external',
                revision=revision, reason='Review ' + request['artifact_kind'] + ' against task acceptance criteria',
                issuer='review-gate', category='review', assignee={'type': 'agent', 'id': policy['signer']},
                policy=policy['policy'])
    if prior_gate:
        plan['supersedes'] = prior_gate
    created = blockers.create(store, beads, run, plan, config)
    issue = created['issue']
    payload = dict(request, policy=policy, attempt=attempt, gate_id=created['gate']['id'],
                   workdir=run['workdir'], captured=captured)
    with store.db:
        key = reviews.enqueue(store, run, issue, 'artifact', request['key'], payload, time.time())
        if request.get('previous'):
            store.db.execute("UPDATE review_jobs SET state='superseded' WHERE id=?", (request['previous'],))
    return {'review_id': key, 'gate_id': payload['gate_id'], 'attempt': attempt}


def current(store, beads, run, job, config):
    request = json.loads(job['request'])
    issue = owned(beads, run, job['issue_id'])
    if gates.scope(issue) != job['scope'] or settings(config) != request['policy']:
        raise BeadsError('Review scope or policy changed; resubmit explicitly')
    bindings = gates.metadata(issue).get(gates.FIELD, {})
    if any(b.get('supersedes') == request['gate_id'] for b in bindings.values()):
        raise BeadsError('Review gate was superseded')
    artifacts(request['artifacts'])
    binding = gates.metadata(beads._queue(run).show(request['gate_id']))['harness_gate']
    if bindings.get(request['gate_id']) != binding:
        raise BeadsError('Review binding changed')
    if blockers.policy(config, binding['blocker']['policy']) != binding['blocker']['policy_snapshot']:
        raise BeadsError('Review signer policy changed')
    return request, binding


def inbox(store, run_id, key, text, target):
    with store.db:
        old = store.db.execute('SELECT run_id,text,target FROM inbox WHERE id=?', (key,)).fetchone()
        if old and tuple(old) != (run_id, text, target):
            raise BeadsError('Review inbox identity already binds another delivery')
        store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES(?,?,?,?,?,?)',
                         (key, run_id, text, time.time(), 'pending', target))


def publish(store, run, job, text):
    from .transitions import parts
    if not run.get('group_id'):
        raise BeadsError('Review verdict requires a bound workstream group')
    with store.db:
        for index, part in enumerate(parts(text)):
            key = job['id'] + ':verdict:' + str(index)
            store.db.execute('INSERT OR IGNORE INTO outbox(id,run_id,group_id,text,created_at) VALUES(?,?,?,?,?)',
                             (key, run['id'], run['group_id'], part, time.time()))


def appraisal(job, result):
    request = json.loads(job['request'])
    required = {'summary', 'blocking_findings', 'verdict', 'evidence_digest', 'reviewer_session', 'model',
                'resolved_model', 'resolved_provider', 'resolved_api_mode'}
    if (not isinstance(result, dict) or set(result) != required or result['verdict'] not in ('pass', 'changes', 'escalate')
            or any(not isinstance(result[k], str) or not result[k].strip()
                   for k in ('resolved_model', 'resolved_provider', 'resolved_api_mode'))
            or not isinstance(result['summary'], str) or not result['summary'].strip()
            or not isinstance(result['blocking_findings'], list)
            or not all(isinstance(f, str) and f.strip() for f in result['blocking_findings'])
            or not isinstance(result['reviewer_session'], str) or not result['reviewer_session'] or result['model'] != request['policy']['model']
            or (result['verdict'] == 'pass' and result['blocking_findings'])
            or (result['verdict'] == 'changes' and not result['blocking_findings'])):
        raise BeadsError('Invalid artifact reviewer verdict')
    return dict(review_id=job['id'], artifact_kind=request['artifact_kind'], attempt=request['attempt'], **result)


def escalate(store, beads, run, job, config, reason):
    request, binding = current(store, beads, run, job, config)
    policy = request['policy']
    owner = policy.get('operator', 'operator')
    created = blockers.create(store, beads, run, dict(key='review-escalation:' + job['id'],
        issue_id=job['issue_id'], kind='external', revision=binding['revision'], reason=reason,
        issuer='review-gate', category='approval', policy=policy.get('operator_policy', policy['policy']),
        assignee={'type': 'human', 'id': owner}, supersedes=request['gate_id']), config)
    with store.db:
        store.db.execute("UPDATE review_jobs SET state='escalated' WHERE id=?", (job['id'],))
    return created


def process_result(store, beads, run, job, result, config):
    from .review_dispatch import directory
    request, binding = current(store, beads, run, job, config)
    decision = appraisal(job, result)
    expected = hashlib.sha256((directory(store, job['id']) / 'evidence.json').read_bytes()).hexdigest()
    if result['evidence_digest'] != expected:
        raise BeadsError('Review evidence digest mismatch')
    encoded = json.dumps(result, sort_keys=True)
    if job['result'] and job['result'] != encoded:
        raise BeadsError('Review verdict is immutable')
    with store.db:
        store.db.execute("UPDATE review_jobs SET result=?,state='reviewed' WHERE id=?", (encoded, job['id']))
    publish(store, run, job, f"Artifact review ({result['model']}): {result['verdict']}\n{result['summary']}\n"
            + '\n'.join(result['blocking_findings']) + '\nReview: ' + job['id'])
    if result['verdict'] == 'changes' and request['attempt'] < request['policy']['max_attempts']:
        inbox(store, run['id'], job['id'] + ':findings',
              'Revise the reviewed artifact and submit a new review referencing this engagement.\n'
              + result['summary'] + '\n' + '\n'.join(result['blocking_findings']) + '\nReview: ' + job['id'], 'worker')
        state = 'changes-requested'
    elif result['verdict'] != 'pass' or not request['policy']['auto_approve']:
        return escalate(store, beads, run, job, config, result['summary'] + '\n' + '\n'.join(result['blocking_findings']))
    else:
        signer_request = dict(gate_id=request['gate_id'], review_id=job['id'],
                              envelope=blockers.expected(binding), decision_evidence=decision)
        atomic(directory(store, job['id']) / 'signing-request.json', signer_request)
        route = config.get('beads', config)['blockers']['assignees'][request['policy']['signer']]
        destination = store.get(route.get('run_id', run['id']))
        inbox(store, destination['id'], job['id'] + ':sign',
              'Sign the delegated passing artifact-review receipt; this is execution of configured approval policy. '
              'Return the original signed event via workstream review receipt.\n'
              + json.dumps(signer_request, sort_keys=True), route.get('target', 'manager'))
        state = 'receipt-pending'
    with store.db:
        store.db.execute('UPDATE review_jobs SET state=? WHERE id=?', (state, job['id']))


def receive(store, beads, run, key, evidence, config):
    from .review_dispatch import directory
    job = job_for(store, run, key)
    request, binding = current(store, beads, run, job, config)
    if job['state'] not in ('receipt-pending', 'approved'):
        raise BeadsError('Review is not awaiting a delegated receipt')
    if not isinstance(evidence, dict) or set(evidence) != {'signed_receipt', 'decision_evidence'}:
        raise BeadsError('Receipt requires signed_receipt and decision_evidence')
    decision = appraisal(job, json.loads(job['result']))
    if evidence.get('decision_evidence') != decision or not request['policy']['auto_approve']:
        raise BeadsError('Receipt must bind the exact independent passing appraisal')
    target = directory(store, key) / 'result.json'
    artifact = retained(str(target))
    if json.loads(target.read_text()) != json.loads(job['result']):
        raise BeadsError('Retained review result changed')
    retained(str(target.parent / 'evidence.json'), decision['evidence_digest'])
    envelope = dict(evidence, issue_id=job['issue_id'], kind='external', revision=binding['revision'],
                    scope=binding['scope'], result='passed', issuer=request['policy']['signer'],
                    authority_basis=binding['blocker']['policy_snapshot']['authority_basis'],
                    evidence_ref=str(target), artifacts=[{'path':str(target),'sha256':artifact['sha256']}, *request['artifacts']])
    result = blockers.resolve(store, beads, run, request['gate_id'], envelope, config)
    # Receipt delivery has a separate immutable outbox identity from the appraisal.
    publish(store, run, dict(job, id=key + ':approval'), 'Artifact auto-approved by configured delegated signer receipt.\n'
            + json.dumps(evidence, sort_keys=True) + '\nReview: ' + key)
    with store.db:
        store.db.execute("UPDATE review_jobs SET state='approved' WHERE id=?", (key,))
    return result
