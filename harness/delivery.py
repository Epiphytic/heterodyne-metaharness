"""Bounded acknowledged outbox delivery, independent of agent observation.

One attempt per invocation prevents an unavailable connector consuming a whole
heartbeat interval. A separate process may call this more frequently. Per-group
FIFO preserves status order while allowing other groups past a failed target.
"""
import fcntl
import logging
import sqlite3
import time

MAX_ATTEMPT_SECONDS = 15
RETRY_SECONDS = 30
ESCALATION_SUFFIX = ':delivery-escalation'


def _escalate(store, row, ops_group, now):
    if not ops_group or row['id'].endswith(ESCALATION_SUFFIX) or row['group_id'] == ops_group:
        return
    identity = row['id'] + ESCALATION_SUFFIX
    text = (f"Workstream {row['run_id']} cannot deliver status to group {row['group_id']} "
            f"after at least three attempts. Event {row['id']} remains queued; "
            'check Marmot connector delivery. Automatic retries continue.')
    store.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                     (identity, row['run_id'], 'delivery_failed', text, now))
    store.db.execute('''INSERT OR IGNORE INTO outbox
      (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
      (identity, row['run_id'], ops_group, text, now))


def _attempt(store, transport, now, ops_group):
    row = store.db.execute('''SELECT current.* FROM outbox AS current
      WHERE current.delivered_at IS NULL AND current.next_attempt<=?
      AND NOT EXISTS (SELECT 1 FROM outbox AS earlier
        WHERE earlier.group_id=current.group_id AND earlier.delivered_at IS NULL
          AND (earlier.created_at<current.created_at OR
            (earlier.created_at=current.created_at AND earlier.rowid<current.rowid)))
      ORDER BY current.next_attempt, current.created_at, current.rowid LIMIT 1''', (now,)).fetchone()
    if row is None:
        return
    # Marmot enforces this across connection/write/read, including slow trickles.
    original_timeout = getattr(transport, 'timeout', MAX_ATTEMPT_SECONDS)
    transport.timeout = min(original_timeout, MAX_ATTEMPT_SECONDS)
    try:
        transport.send(row['group_id'], row['text'], row['id'])
    except Exception as exc:
        with store.db:
            store.db.execute('''UPDATE outbox SET attempts=attempts+1,
              next_attempt=?, error=? WHERE id=?''', (now + RETRY_SECONDS, str(exc)[:500], row['id']))
            if row['attempts'] + 1 >= 3:
                _escalate(store, row, ops_group, now)
    else:
        with store.db:
            store.db.execute('''UPDATE outbox SET delivered_at=?, attempts=attempts+1,
              error=NULL WHERE id=?''', (now, row['id']))
    finally:
        transport.timeout = original_timeout


def deliver(store, transport, now=None, ops_group=None):
    """Try one eligible FIFO head, serializing competing dispatcher processes."""
    now = time.time() if now is None else now
    with open(store.root / '.delivery.lock', 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            from .brain_visible import enqueue_receipts
            try:
                enqueue_receipts(store)
            except (OSError, ValueError, sqlite3.Error) as exc:
                logging.getLogger(__name__).warning('Brain visible routing deferred: %s', type(exc).__name__)
            _attempt(store, transport, now, ops_group)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def health(store):
    row = store.db.execute('''SELECT COUNT(*) AS pending, MIN(created_at) AS oldest,
      MAX(attempts) AS max_attempts FROM outbox WHERE delivered_at IS NULL''').fetchone()
    return dict(row)
