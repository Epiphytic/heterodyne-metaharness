"""Durable operator asks and send-time reminders; contract: spec/operator-asks.md."""
import hashlib
import json
import re
import time

ASK_KINDS = {'operator_question', 'approval', 'awaiting_resume'}


def initialize(db):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS operator_asks(
          id TEXT PRIMARY KEY,run_id TEXT NOT NULL,group_id TEXT NOT NULL,
          summary TEXT NOT NULL,created_at REAL NOT NULL,delivered_at REAL,
          resolved_at REAL,resolution TEXT)''',
        '''CREATE TABLE IF NOT EXISTS operator_ask_parts(
          event_id TEXT PRIMARY KEY,ask_id TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS outbox_rendered(
          id TEXT PRIMARY KEY,account_id TEXT NOT NULL,text TEXT NOT NULL)''',
    ):
        db.execute(statement)


def register(store, row, summary, ask_id=None):
    """Caller owns the transaction; one logical ask may span several parts."""
    initialize(store.db)
    identity = ask_id or row['id']
    expected = (row['run_id'], row['group_id'], summary)
    prior = store.db.execute('SELECT run_id,group_id,summary FROM operator_asks WHERE id=?',
                             (identity,)).fetchone()
    if prior and tuple(prior) != expected:
        raise ValueError('Ask identity already identifies different content or routing')
    store.db.execute('''INSERT OR IGNORE INTO operator_asks
        (id,run_id,group_id,summary,created_at) VALUES (?,?,?,?,?)''',
                     (identity, *expected, row['created_at']))
    part = store.db.execute('SELECT ask_id FROM operator_ask_parts WHERE event_id=?', (row['id'],)).fetchone()
    if part and part[0] != identity:
        raise ValueError('Ask part identity collision')
    store.db.execute('INSERT OR IGNORE INTO operator_ask_parts VALUES (?,?)', (row['id'], identity))


def enqueue(store, run, text, key, summary):
    if not run.get('group_id') or not all(isinstance(v, str) and v.strip() for v in (text, key, summary)):
        raise ValueError('Ask requires exact group binding, text, stable key and summary')
    identity = 'operator-ask:' + hashlib.sha256(json.dumps([run['id'], key]).encode()).hexdigest()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        prior = store.db.execute('SELECT run_id,group_id,text FROM outbox WHERE id=?', (identity,)).fetchone()
        expected = (run['id'], run['group_id'], text)
        if prior and tuple(prior) != expected:
            raise ValueError('Ask key already identifies different content or routing')
        store.db.execute('''INSERT OR IGNORE INTO outbox
            (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''', (identity, *expected, time.time()))
        row = store.db.execute('SELECT * FROM outbox WHERE id=?', (identity,)).fetchone()
        register(store, row, summary)
    return {'event_id': identity, 'queued': True}


def resolve(store, run, identity, evidence):
    if not evidence.strip():
        raise ValueError('Resolution requires operator response or native-inspection evidence')
    with store.db:
        initialize(store.db)
        row = store.db.execute('SELECT * FROM operator_asks WHERE id=? AND run_id=?',
                               (identity, run['id'])).fetchone()
        if row is None:
            raise ValueError('Ask does not belong to this workstream')
        if row['resolved_at'] is None:
            store.db.execute('UPDATE operator_asks SET resolved_at=?,resolution=? WHERE id=?',
                             (time.time(), evidence, identity))
        elif row['resolution'] != evidence:
            raise ValueError('Ask already resolved with different evidence')
    return {'ask_id': identity, 'resolved': True}


# Restrict removal to known stamps; retain surrounding prose and evidence.
_STAMP = re.compile(r'\[brain-notice:[^\]\r\n]*\]|\[worker brain context received\]', re.I)
_REFERENCE = re.compile(r'(?<![\w-])(?:operator-ask:[a-zA-Z0-9_-]+|ask:[a-fA-F0-9]{8,}|[a-fA-F0-9]{64}|btq-[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*)(?![\w-])')


def operator_text(text, mentions=''):
    """Keep complete prose; move opaque identifiers to the final reference line."""
    text = _STAMP.sub('', text).strip()
    # Our already-rendered footer is stable across retries.
    body, separator, footer = text.rpartition('\n\nReferences: ')
    if separator:
        text = body
    references = []

    def reference(match):
        value = match.group()
        if value not in references:
            references.append(value)
        return f'[reference {references.index(value) + 1}]'

    text = _REFERENCE.sub(reference, text)
    if references:
        extra = '; '.join(f'{i}: {value}' for i, value in enumerate(references, 1))
        footer = (footer + '; ' if separator else '') + extra
    if mentions:
        text += '\n\n' + mentions
    if separator or references:
        text += '\n\nReferences: ' + footer
    return text


def enqueue_reminder(store, now):
    """Queue one hourly reminder, only after ordinary destination traffic drains."""
    initialize(store.db)
    with store.db:
        # A resolved unattempted reminder has no uncertain network effect.
        store.db.execute("""DELETE FROM outbox WHERE id LIKE 'ask-reminder:%'
            AND attempts=0 AND delivered_at IS NULL AND id IN
            (SELECT p.event_id FROM operator_ask_parts p JOIN operator_asks a
             ON a.id=p.ask_id WHERE a.resolved_at IS NOT NULL)""")
        ask = store.db.execute("""SELECT a.* FROM operator_asks a
            WHERE a.resolved_at IS NULL AND a.delivered_at<=?
            AND NOT EXISTS (SELECT 1 FROM outbox q WHERE q.group_id=a.group_id
                            AND q.delivered_at IS NULL)
            AND NOT EXISTS (SELECT 1 FROM operator_ask_parts p JOIN outbox q
                ON q.id=p.event_id WHERE p.ask_id=a.id
                AND q.id LIKE 'ask-reminder:%' AND q.created_at>?)
            ORDER BY COALESCE((SELECT MAX(q.created_at) FROM operator_ask_parts p
                JOIN outbox q ON q.id=p.event_id WHERE p.ask_id=a.id
                AND q.id LIKE 'ask-reminder:%'), a.delivered_at), a.id LIMIT 1""",
            (now - 3600, now - 3600)).fetchone()
        if ask is None:
            return
        parts = store.db.execute("""SELECT q.text FROM operator_ask_parts p
            JOIN outbox q ON q.id=p.event_id WHERE p.ask_id=?
            AND q.id NOT LIKE 'ask-reminder:%' ORDER BY q.created_at,q.rowid""",
            (ask['id'],)).fetchall()
        if not parts:
            return
        identity = 'ask-reminder:' + hashlib.sha256(
            json.dumps([ask['id'], now]).encode()).hexdigest()
        text = 'Reminder: operator response pending.\n\n' + '\n\n'.join(p['text'] for p in parts)
        store.db.execute('''INSERT INTO outbox
            (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
            (identity, ask['run_id'], ask['group_id'], text, now))
        store.db.execute('INSERT INTO operator_ask_parts VALUES (?,?)', (identity, ask['id']))


def prepare(store, transport, row):
    """Freeze one message before network I/O; never append unrelated asks."""
    initialize(store.db)
    account = getattr(transport, 'account_id', '')
    prior = store.db.execute('SELECT account_id,text FROM outbox_rendered WHERE id=?', (row['id'],)).fetchone()
    if prior:
        if prior['account_id'] != account:
            raise ValueError('Rendered message account changed; reconcile before delivery')
        if ('Still awaiting your answer:' in prior['text'] or
                operator_text(prior['text']) != prior['text']):
            raise ValueError('Legacy rendered ask requires delivery reconciliation')
        return prior['text']
    own = store.db.execute('SELECT ask_id FROM operator_ask_parts WHERE event_id=?', (row['id'],)).fetchone()
    tags = ''
    if own:
        # Fail closed on lookup failure; never guess admin recipients.
        tags = ' '.join('@' + reference for reference in transport.admin_references(row['group_id']))
    text = operator_text(row['text'], tags)
    with store.db:
        store.db.execute('INSERT INTO outbox_rendered VALUES (?,?,?)', (row['id'], account, text))
    return text


def delivered(store, row, now):
    initialize(store.db)
    store.db.execute('''UPDATE operator_asks SET delivered_at=COALESCE(delivered_at,?)
        WHERE id IN (SELECT ask_id FROM operator_ask_parts WHERE event_id=?)''', (now, row['id']))


def configure_cli(sub):
    ask = sub.add_parser('ask')
    ask.add_argument('name')
    actions = ask.add_subparsers(dest='ask_action', required=True)
    create = actions.add_parser('enqueue')
    create.add_argument('--file', required=True)
    create.add_argument('--key', required=True)
    create.add_argument('--summary', required=True)
    create.add_argument('--hourly-backstop', action='store_true')
    actions.add_parser('list')
    close = actions.add_parser('resolve')
    close.add_argument('ask_id')
    close.add_argument('--evidence-file', required=True)


def handle_cli(args, store):
    from pathlib import Path
    run = store.get(args.name)
    if args.ask_action == 'enqueue':
        if args.hourly_backstop:
            run = store.get('hermes-maintenance')
            if run.get('group_id') != '1cead9a9921044b3236ddb271e9a4cac':
                raise ValueError('Canonical hourly backstop group binding changed')
        return enqueue(store, run, Path(args.file).read_text(), args.key, args.summary)
    if args.ask_action == 'resolve':
        return resolve(store, run, args.ask_id, Path(args.evidence_file).read_text())
    initialize(store.db)
    return [dict(row) for row in store.db.execute('SELECT * FROM operator_asks WHERE run_id=? AND resolved_at IS NULL',
                                                 (run['id'],))]


def babysitter_notice(pinfo, text, *, operator_ask, category=None, healthy=False):
    """Installed adapter: retain failed sends in the shared outbox, never local seen-only state."""
    from .store import Store
    store = Store()
    try:
        with store.lock():
            run = store.get(pinfo['run_id'])
            if run.get('group_id') != pinfo['group']:
                raise ValueError('Babysitter group binding changed; refresh observation')
            from .cli import load_config
            if load_config(store.root.parent).get('beads', {}).get('enabled'):
                from .manager_notices import record
                result = record(store, run, text, operator_ask=operator_ask, category=category, healthy=healthy)
                if result:
                    return result
            key = 'babysitter:' + hashlib.sha256(json.dumps([run['id'], text]).encode()).hexdigest()
            if operator_ask:
                return enqueue(store, run, text, key, text)
            with store.db:
                store.event(run, 'babysitter_status', text, key)
            return {'event_id': key, 'queued': True}
    finally:
        store.db.close()
