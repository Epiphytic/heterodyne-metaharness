"""Narrow worker error observation for the external babysitter adapter."""
import hashlib
import json
import re

from .babysitter_resolution import observe, poll

# Anchored tool errors, not operator or manager prose about an old incident.
EROFS = re.compile(
    r"^(?:fatal: Unable to create '.+index\.lock': Read-only file system|"
    r"(?:OSError|PermissionError): \[Errno 30\] Read-only file system(?:: .+)?)$",
    re.MULTILINE | re.IGNORECASE)


def worker_errors(store, panes, capture, *, now=None):
    """Caller holds lock; nonempty changed native-idle output can verify recovery."""
    store.db.execute('''CREATE TABLE IF NOT EXISTS babysitter_worker_probes (
      run_id TEXT PRIMARY KEY, pane_id TEXT NOT NULL, digest TEXT NOT NULL, native_turn TEXT NOT NULL)''')
    for info in panes:
        if info.get('is_manager'):
            continue
        owner = store.get(info['run_id'])
        if owner.get('pane_id') != info['pane_id']:
            continue
        output = capture(info['pane_id'])
        if not output.strip():
            continue
        native_turn = json.dumps(owner.get('native_turn_key'))
        digest = hashlib.sha256((output + native_turn).encode()).hexdigest()
        previous = store.db.execute('SELECT * FROM babysitter_worker_probes WHERE run_id=?',
                                    (owner['id'],)).fetchone()
        if previous and previous['pane_id'] == info['pane_id'] and previous['digest'] == digest:
            continue
        error = EROFS.search(output)
        if not error and (not previous or previous['pane_id'] != info['pane_id']
                          or owner.get('native_turn_state') != 'idle'
                          or native_turn == 'null' or native_turn == previous['native_turn']):
            continue
        evidence = ((error.group(0) if error else 'Read-only tool error absent from changed, idle worker output.')
                    + '\nVerify on a later changed capture at an owned idle native boundary. '
                    + f"Pane {info['pane_id']}; native turn {owner.get('native_turn_key')}; capture sha256 {digest}.")
        observe(store, owner, 'blocked-handoff-commit', 'worker-read-only-error', evidence,
                healthy=error is None, now=now)
        store.db.execute('INSERT OR REPLACE INTO babysitter_worker_probes VALUES (?,?,?,?)',
                         (owner['id'], info['pane_id'], digest, native_turn))


def run(namespace):
    """Installed handler: no native inputs or live repair effects in this adapter."""
    from .store import Store
    store = Store()
    try:
        with store.lock(blocking=False):
            with store.db:
                worker_errors(store, namespace['list_worker_panes'](), namespace['capture'])
            poll(store)
    finally:
        store.db.close()
