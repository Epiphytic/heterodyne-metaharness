"""Retained evidence between independent owners. Authority: spec/handoffs.md."""
from pathlib import Path

from .beads import BeadsError
from .task_formulas import retained
from .task_stages import clean_commit, validate_evidence
from .workspace import git


def enabled(binding):
    return binding.get('formula') in ('deployable-v2', 'library-v2')


def context(run, binding, evidence):
    return dict(run, workdir=evidence['checkout']) if enabled(binding) else run


def required(evidence, *fields):
    if any(not isinstance(evidence.get(k), str) or not evidence[k].strip() for k in fields):
        raise BeadsError('Handoff requires nonempty ' + ', '.join(fields))


def reference(evidence, field):
    required(evidence, field)
    matches = [a for a in evidence['artifacts'] if a['path'] == evidence[field]]
    if len(matches) != 1:
        raise BeadsError('Handoff reference must identify one retained artifact: ' + field)
    return matches[0]['sha256']


def checkout(run, stage, evidence, history, require_head):
    required(evidence, 'checkout', 'repository')
    path = Path(evidence['checkout'])
    if not path.is_absolute() or git(str(path), 'rev-parse', '--show-toplevel') != str(path.resolve()):
        raise BeadsError('Handoff checkout must identify a retained Git worktree')
    repository = git(str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if repository != evidence['repository']:
        raise BeadsError('Handoff repository identity mismatch')
    if history and evidence['repository'] != history[0]['evidence']['repository']:
        raise BeadsError('Handoff cannot switch repository')
    # Implementer must use its owned checkout. Later owners inspect retained artifacts;
    # their own unrelated cwd/HEAD is not evidence for this delivery.
    if require_head and stage in ('committed', 'tested', 'pr-open') and str(path.resolve()) != str(Path(run['workdir']).resolve()):
        raise BeadsError('Implementation handoff must use owned checkout')
    return dict(run, workdir=str(path))


def validate(run, stage, evidence, history, require_head=True):
    retained(evidence)
    reference(evidence, 'evidence_ref')
    if history and evidence.get('input_digest') != history[-1]['digest']:
        raise BeadsError('Handoff must pin preceding event digest')
    artifact_run = checkout(run, stage, evidence, history, require_head)
    # Retained later checkouts may move HEAD after the exact revision was tested.
    own_head = require_head and stage in ('committed', 'tested', 'pr-open')
    by_stage = {event['stage']: event['evidence'] for event in history}
    if stage in ('deployed', 'live-verified', 'integrated'):
        commit = clean_commit(artifact_run, evidence.get('commit'), require_head=False)
        if commit != by_stage['final-tested']['commit']:
            raise BeadsError('Handoff revision must match post-merge tested commit')
        if evidence.get('result') != 'passed':
            raise BeadsError('Handoff requires actual passing result')
        required(evidence, 'target', 'environment')
        if stage == 'deployed':
            deployment(evidence, by_stage['merged'])
        elif stage == 'live-verified':
            verification(evidence, by_stage['deployed'])
        else:
            required(evidence, 'command')
        return
    validate_evidence(artifact_run, stage, evidence, history, require_head=own_head)
    if stage in ('tested', 'final-tested'):
        if reference(evidence, 'evidence_ref') != evidence.get('log_sha256'):
            raise BeadsError('Test log hash must match retained log bytes')
    elif stage == 'merged':
        if evidence.get('approved_revision') != by_stage['pr-open']['commit']:
            raise BeadsError('Review approval must pin the submitted revision')
        reference(evidence, 'review_ref')
        reference(evidence, 'authority_ref')
        if not isinstance(evidence.get('restrictions'), list) or any(not isinstance(v, str) or not v.strip() for v in evidence['restrictions']):
            raise BeadsError('Review must record remaining restrictions (empty list if none)')


def deployment(evidence, merged):
    if (evidence.get('applicable') is not True or evidence.get('deployment_authority') != 'operator'
            or evidence.get('deployed_revision') != evidence['commit']):
        raise BeadsError('Deployment requires operator authority and actual tested revision')
    reference(evidence, 'authority_ref')
    if evidence.get('restrictions') != merged['restrictions']:
        raise BeadsError('Deployment must preserve review restrictions')
    if merged['restrictions']:
        # Resolution is retained operator evidence, not automatic waiver by a worker.
        reference(evidence, 'restriction_resolution_ref')


def verification(evidence, deployed):
    if any(evidence.get(k) != deployed[k] for k in ('target', 'environment', 'deployed_revision')):
        raise BeadsError('Live verification must pin the exact deployment target and revision')
    if evidence.get('running_revision') != evidence['commit']:
        raise BeadsError('Live running revision differs from tested deployment')
    required(evidence, 'command')
    checks = evidence.get('acceptance_checks')
    if not isinstance(checks, list) or not checks:
        raise BeadsError('Live acceptance checks required')
    for check in checks:
        if not isinstance(check, dict) or check.get('result') != 'passed':
            raise BeadsError('Live acceptance check failed')
        required(check, 'check', 'observed')
    reference(evidence, 'live_verification_ref')
