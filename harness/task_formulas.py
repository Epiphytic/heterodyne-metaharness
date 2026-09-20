"""Versioned persistent workflow admission. Authority: spec/formulas.md."""
import hashlib
import json
from pathlib import Path

from .beads import BeadsError

PROFILES = {
    'deployable-v1': {'implementation': ('committed', 'tested', 'pr-open'),
                      'review': ('merged', 'final-tested'), 'deployment': ('deployed', 'close-ready')},
    'library-v1': {'implementation': ('committed', 'tested', 'pr-open'),
                   'review': ('merged', 'final-tested'), 'integration': ('integrated',)},
    'research-v1': {'investigation': ('investigated',), 'recommendation': ('recommended',)},
    'configuration-v1': {'change': ('changed',), 'verification': ('verified',)},
}
EVIDENCE_STAGES = ('investigated', 'recommended', 'changed', 'verified')


def load(name):
    if name not in PROFILES:
        raise BeadsError('Unknown versioned workflow formula')
    path = Path(__file__).resolve().parent.parent / 'formulas' / (name + '.formula.json')
    raw = path.read_bytes()
    value = json.loads(raw)
    roles = list(PROFILES[name])
    expected = [(role, roles[i-1:i] if i else []) for i, role in enumerate(roles)]
    if (value.get('formula') != 'hermes-' + name or value.get('version') != 1
            or value.get('phase') != 'liquid' or value.get('type') != 'workflow'
            or [(step['id'], step.get('needs', [])) for step in value['steps']] != expected):
        raise BeadsError('Formula graph does not match its versioned contract')
    return value, hashlib.sha256(raw).hexdigest()


def profile(binding):
    name = binding.get('formula')
    _, digest = load(name)
    if binding.get('formula_sha256') != digest:
        raise BeadsError('Formula digest changed; retain published versions')
    return PROFILES[name]


def create(store, supervisor, beads, run, plan):
    from .task_delivery import create as delivery_create
    if not isinstance(plan, dict) or set(plan) != {'key', 'title', 'description', 'routes', 'formula'}:
        raise BeadsError('Formula plan needs key/title/description/routes/formula')
    _, digest = load(plan['formula'])
    binding = {'version': 2, 'formula': plan['formula'], 'formula_sha256': digest}
    return delivery_create(store, supervisor, beads, run,
                           {key: value for key, value in plan.items() if key != 'formula'}, binding=binding)


def retained(evidence):
    """Verify retained sanitized evidence bytes, not merely an attestation string."""
    artifacts = evidence.get('artifacts')
    if not isinstance(artifacts, list) or not artifacts:
        raise BeadsError('Retained evidence artifacts required')
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise BeadsError('Artifact requires absolute path and SHA256')
        path = Path(item['path'])
        if not path.is_absolute() or not path.is_file():
            raise BeadsError('Retained artifact unavailable')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise BeadsError('Retained artifact digest mismatch')


def validate(run, binding, stage, evidence, history, require_head=True):
    from .task_stages import validate_evidence, clean_commit
    if stage in EVIDENCE_STAGES:
        if not evidence.get('evidence_ref') or not evidence.get('summary'):
            raise BeadsError('Evidence reference and substantive result summary required')
        retained(evidence)
        if stage in ('verified', 'recommended'):
            prior = 'changed' if stage == 'verified' else 'investigated'
            expected = next(event['digest'] for event in history if event['stage'] == prior)
            if evidence.get('input_digest') != expected:
                raise BeadsError('Evidence must pin the preceding step digest')
        if stage == 'verified' and (evidence.get('result') != 'passed' or not evidence.get('command')):
            raise BeadsError('Configuration verification needs passing actual check evidence')
        return
    if stage == 'integrated':
        commit = clean_commit(run, evidence.get('commit'), require_head=require_head)
        expected = next(event['evidence']['commit'] for event in history if event['stage'] == 'final-tested')
        if (commit != expected or evidence.get('result') != 'passed' or not evidence.get('command')
                or not evidence.get('target') or not evidence.get('evidence_ref')):
            raise BeadsError('Integration verification must test the reviewed commit in its consumer')
        retained(evidence)
        return
    if binding.get('formula') == 'deployable-v1' and stage == 'deployed' and evidence.get('applicable') is not True:
        raise BeadsError('Deployable formula cannot omit deployment')
    validate_evidence(run, stage, evidence, history, require_head=require_head)
