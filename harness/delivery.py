"""Bounded acknowledged outbox delivery, independent of agent observation.

One attempt per invocation prevents an unavailable connector consuming a whole
heartbeat interval. A separate process may call this more frequently. Per-group
FIFO preserves status order while allowing other groups past a failed target.
"""
import fcntl
import hashlib
import json
import logging
import sqlite3
import time

MAX_ATTEMPT_SECONDS = 15
RETRY_SECONDS = 30
ESCALATION_SUFFIX = ':delivery-escalation'
# Operator-set floor (2026-09-20): identical rendered text must not be SENT to
# the same group more than once per hour. Keyed on rendered text because status
# re-renders vary per poll; row-id dedup alone cannot catch them.
SEND_DEDUP_SECONDS = 3600
DEDUP_IGNORE_PREFIXES = ('reaction:', 'permission:', 'transition:')


def _send_dedup_key(text):
    return hashlib.sha256(json.dumps(text).encode()).hexdigest()


def _initialize_dedup(db):
    db.execute('''CREATE TABLE IF NOT EXISTS send_dedup(
      text_hash TEXT PRIMARY KEY, group_id TEXT NOT NULL,
      last_sent_at REAL NOT NULL)''')


def _recently_sent(db, group_id, text, now):
    _initialize_dedup(db)
    cutoff = now - SEND_DEDUP_SECONDS
    db.execute('DELETE FROM send_dedup WHERE last_sent_at < ?', (cutoff,))
    row = db.execute('SELECT 1 FROM send_dedup WHERE text_hash=? AND group_id=? AND last_sent_at>?',
                     (_send_dedup_key(text), group_id, cutoff)).fetchone()
    return row is not None


def _mark_sent(db, group_id, text, now):
    _initialize_dedup(db)
    db.execute('''INSERT INTO send_dedup(text_hash,group_id,last_sent_at) VALUES (?,?,?)
      ON CONFLICT(text_hash) DO UPDATE SET last_sent_at=excluded.last_sent_at,
      group_id=excluded.group_id''', (_send_dedup_key(text), group_id, now))


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
    from .operator_asks import register
    escalation = store.db.execute('SELECT * FROM outbox WHERE id=?', (identity,)).fetchone()
    register(store, escalation, 'Check Marmot connector delivery for ' + row['run_id'])


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
    if not row['id'].startswith(DEDUP_IGNORE_PREFIXES):
        rendered = None
        try:
            from .operator_asks import prepare
            rendered = prepare(store, transport, row)
        except Exception:
            rendered = None  # prepare failure must not bypass the normal error path
        if rendered is not None and _recently_sent(store.db, row['group_id'], rendered, now):
            last = store.db.execute('''SELECT MAX(last_sent_at) FROM send_dedup
              WHERE text_hash=? AND group_id=?''',
              (_send_dedup_key(rendered), row['group_id'])).fetchone()[0]
            retry_at = max((last or now) + SEND_DEDUP_SECONDS, now + RETRY_SECONDS)
            with store.db:
                store.db.execute('''UPDATE outbox SET attempts=attempts+1,
                  next_attempt=?, error='send-dedup: identical text sent within 1h' WHERE id=?''',
                  (retry_at, row['id']))
            return
    try:
        from .reactions import send_outbox
        response = send_outbox(store, transport, row)
        if row['id'].startswith('permission:'):
            from .permission_relay import delivered
            with store.db:
                delivered(store, transport, row, response)
    except Exception as exc:
        with store.db:
            store.db.execute('''UPDATE outbox SET attempts=attempts+1,
              next_attempt=?, error=? WHERE id=?''', (now + RETRY_SECONDS, str(exc)[:500], row['id']))
            if row['attempts'] + 1 >= 3:
                _escalate(store, row, ops_group, now)
    else:
        with store.db:
            from .operator_asks import delivered
            delivered(store, row, now)
            if not row['id'].startswith(DEDUP_IGNORE_PREFIXES):
                try:
                    from .operator_asks import prepare
                    _mark_sent(store.db, row['group_id'], prepare(store, transport, row), now)
                except Exception:
                    pass
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
            from .transitions import flush, retire_polls
            retire_polls(store, now)
            flush(store, now)
            _attempt(store, transport, now, ops_group)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def health(store):
    row = store.db.execute('''SELECT COUNT(*) AS pending, MIN(created_at) AS oldest,
      MAX(attempts) AS max_attempts FROM outbox WHERE delivered_at IS NULL''').fetchone()
    return dict(row)
