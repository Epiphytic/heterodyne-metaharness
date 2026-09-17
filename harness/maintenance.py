"""Deterministic maintenance admission. Contract: spec/maintenance.md."""
import hashlib
import time

from .beads import Beads
from .store import TERMINAL


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
    issue = Beads(config.get('beads', {})).create(run, title, text, key='maintenance:' + key)
    issue_id = issue['id']
    identity = hashlib.sha256((run['id'] + '\0maintenance:' + key).encode()).hexdigest()
    message = ('Maintenance task queued: ' + issue_id + '. Inspect through workstream task ' + run['name']
               + ' show. Preserve the current claim and native approvals. At the next authorized task boundary, '
               'bind/claim this task and send its requirements to the existing worker; never create another writer.')
    with store.db:
        store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES(?,?,?,?,?,?)',
                         (identity, run['id'], message, time.time(), 'pending', 'manager'))
    supervisor.persist(run)
    return {'issue_id': issue_id, 'run_id': run['id'], 'notification_id': identity, 'queued': True}
