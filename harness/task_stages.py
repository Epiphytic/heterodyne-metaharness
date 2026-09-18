"""Evidence-bound Bead execution lifecycle. Authority: spec/changes.md."""
import hashlib
import json
import re
import time
from urllib.parse import urlparse

from .beads import BeadsError
from .workspace import git

STAGES = ('committed', 'tested', 'pr-open', 'merged', 'final-tested', 'deployed', 'close-ready')


def clean_commit(run, commit):
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', commit):
        raise BeadsError('Evidence requires a full commit SHA')
    if git(run['workdir'], 'status', '--porcelain'):
        raise BeadsError('Uncommitted work at lifecycle boundary is a workflow error')
    actual = git(run['workdir'], 'rev-parse', '--verify', commit + '^{commit}')
    if actual != commit or git(run['workdir'], 'rev-parse', 'HEAD') != commit:
        raise BeadsError('Evidence must pin the current full commit SHA')
    if git(run['workdir'], 'log', '-1', '--format=%G?', commit) != 'G':
        raise BeadsError('Commit signature must verify as G')
    return actual


def validate_evidence(run, stage, evidence, history):
    if not isinstance(evidence, dict) or not evidence.get('evidence_ref'):
        raise BeadsError('Stage needs an actual evidence reference')
    commit = clean_commit(run, evidence.get('commit', ''))
    by_stage = {event['stage']: event for event in history}
    if stage in ('tested', 'final-tested'):
        if evidence.get('result') != 'passed' or evidence.get('full_suite') is not True:
            raise BeadsError('Full suite passing evidence is required')
        if not evidence.get('command') or not re.fullmatch(r'[a-f0-9]{64}', str(evidence.get('log_sha256', ''))):
            raise BeadsError('Test command and retained log SHA256 required')
        required = 'committed' if stage == 'tested' else 'merged'
        if commit != by_stage[required]['evidence']['commit']:
            raise BeadsError('Test evidence does not match required commit')
    elif stage == 'pr-open':
        if commit != by_stage['tested']['evidence']['commit']:
            raise BeadsError('PR commit has not passed its full suite')
        url = urlparse(evidence.get('pr_url', ''))
        if url.scheme not in ('https', 'rad') or not url.netloc:
            raise BeadsError('Explicit private review/PR URL required; do not invent a remote')
        if not evidence.get('remote') or evidence.get('pushed_commit') != commit:
            raise BeadsError('Recorded remote and pushed commit required')
        if not git(run['workdir'], 'remote', 'get-url', evidence['remote']):
            raise BeadsError('Configured remote required before PR stage')
    elif stage == 'merged':
        if evidence.get('merge_authority') != 'operator' or not evidence.get('review_ref'):
            raise BeadsError('Operator merge/review evidence required; workers do not merge')
        git(run['workdir'], 'merge-base', '--is-ancestor', by_stage['pr-open']['evidence']['commit'], commit)
    elif stage == 'deployed':
        deployment_evidence(evidence, by_stage['final-tested']['evidence']['commit'])
    elif stage == 'close-ready':
        if commit != by_stage['deployed']['evidence']['commit']:
            raise BeadsError('Close-ready requires the deployed tested commit')
    return commit


def deployment_evidence(evidence, commit):
    if evidence.get('commit') != commit:
        raise BeadsError('Deployment must pin the post-merge tested commit')
    if evidence.get('deployment_authority') != 'operator':
        raise BeadsError('Actual operator deployment/applicability evidence required')
    if evidence.get('applicable') is False:
        if not isinstance(evidence.get('reason'), str) or not evidence['reason'].strip():
            raise BeadsError('Non-deployable work requires an explicit applicability reason')
        return
    if evidence.get('applicable') is not True:
        raise BeadsError('Deployment applicability must be explicit')
    for field in ('target', 'deployed_revision', 'live_verification_ref'):
        if not isinstance(evidence.get(field), str) or not evidence[field].strip():
            raise BeadsError('Deployment target, exact revision and live verification reference required')
    if evidence['deployed_revision'] != commit or evidence.get('result') != 'passed':
        raise BeadsError('Deployment requires the tested revision and passing live verification')


def record(store, beads, run, issue_id, stage, evidence):
    if stage not in STAGES:
        raise BeadsError('Unknown lifecycle stage')
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required'):
        raise BeadsError('Reconcile recovery before lifecycle transitions')
    queue = beads._queue(run)
    issue = queue.show(issue_id)
    if issue_id != run.get('beads', {}).get('issue_id') or issue.get('assignee') != queue.worker:
        raise BeadsError('Only the bound current owner can record task lifecycle')
    metadata = dict(issue.get('metadata') or {})
    history = list(metadata.get('harness_lifecycle') or [])
    digest = hashlib.sha256(json.dumps([stage, evidence], sort_keys=True).encode()).hexdigest()
    if any(event.get('digest') == digest for event in history):
        return issue  # Lost acknowledgment retry never repeats a mutation.
    expected = STAGES[len(history)] if len(history) < len(STAGES) else None
    if stage != expected:
        raise BeadsError(f'Lifecycle requires {expected}; amendments need explicit reconciliation')
    validate_evidence(run, stage, evidence, history)
    history.append({'stage': stage, 'evidence': evidence, 'digest': digest, 'at': time.time()})
    metadata['harness_lifecycle'] = history
    queue.bd('update', issue_id, '--metadata', json.dumps(metadata))
    result = queue.show(issue_id)
    if result.get('metadata', {}).get('harness_lifecycle') != history:
        raise BeadsError('Lifecycle write uncertain; inspect and replay same evidence only')
    return result


def close_ready(run, issue):
    if issue.get('status') == 'closed':
        return
    history = issue.get('metadata', {}).get('harness_lifecycle') or []
    if [event.get('stage') for event in history] != list(STAGES):
        raise BeadsError('Close requires committed, tested, PR-open, merged, final-tested, deployed, close-ready evidence')
    for i, event in enumerate(history):
        digest = hashlib.sha256(json.dumps([event['stage'], event['evidence']], sort_keys=True).encode()).hexdigest()
        if digest != event.get('digest'):
            raise BeadsError('Lifecycle evidence digest mismatch')
    by_stage = {event['stage']: event['evidence'] for event in history}
    commit = by_stage['merged']['commit']
    if any(by_stage[name]['commit'] != commit for name in ('final-tested', 'deployed', 'close-ready')):
        raise BeadsError('Post-merge evidence commit mismatch')
    tested = by_stage['final-tested']
    if tested.get('result') != 'passed' or tested.get('full_suite') is not True:
        raise BeadsError('Passing full post-merge suite required')
    deployment_evidence(by_stage['deployed'], commit)
    clean_commit(run, commit)
