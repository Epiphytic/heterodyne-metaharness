"""Legacy babysitter notices enter durable repair episodes, never execute prose."""
import re

from .babysitter_resolution import ACTIONS, observe
from .manager_tasks import collect

PATTERNS = (
    ('blocked-handoff-commit', r'EROFS|read.only file system|index\.lock'),
    ('supervisor-dead', r'supervisor.*(?:dead|down|failed|inactive)|hermes-workstreams.*(?:failed|inactive)'),
    ('outbox-head-of-line', r'outbox|head.of.line'),
    ('invalid-pause', r'invalid.pause|pause.anomal'),
    ('worker-stall', r'stall|stranded|stuck'),
)


def record(store, run, text, *, operator_ask, category=None, healthy=False):
    if category is None:
        category = next((kind for kind, pattern in PATTERNS if re.search(pattern, text, re.I)), None)
    if category is None and operator_ask:
        category = 'worker-stall'
    if category not in ACTIONS:
        return None
    with store.db:
        subject = 'worker-read-only-error' if category == 'blocked-handoff-commit' else 'babysitter-notice'
        key = observe(store, run, category, subject, text, healthy=healthy)
        collect(store)
    return {'finding_id': key, 'queued': True, 'target': 'manager-bead'}
