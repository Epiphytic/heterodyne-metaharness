"""Deterministic maintenance admission. Contract: spec/maintenance.md."""

from .beads import Beads
from .store import TERMINAL
from .tasks import admit


def enqueue_task(store, supervisor, config, title, text, key):
    """Caller holds Store.lock. Queue a bead plus one manager notification."""
    binding = config.get('maintenance', {})
    run = store.get(binding.get('run_id', 'hermes-maintenance'))
    if (not binding.get('run_id') or run['id'] != binding['run_id']
            or run['agent'] != 'codex' or not run.get('persistent')
            or run['group_id'] != binding.get('group_id') or run['state'] in TERMINAL):
        raise ValueError('Maintenance binding unavailable; inspect existing session, never create a replacement')
    if not title.strip() or not text.strip() or not key.strip():
        raise ValueError('title, task text and stable request key required')
    return admit(store, supervisor, Beads(config.get('beads', {})), run, title, text, 'maintenance:' + key)
