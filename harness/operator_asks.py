"""Durable operator asks and send-time reminders; contract: spec/operator-asks.md."""
import hashlib
import json
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
    summary = ' '.join(summary.split())[:400]
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


def prepare(store, transport, row):
    """Freeze rendered content before network I/O; retry never changes recipients.

    Reminders are the final paragraph of the new notice, not another unbounded
    stream of messages. Status dedup still runs before enqueue. Native replies
    and unrelated group writers cannot be ordered by this outbox.
    """
    initialize(store.db)
    account = getattr(transport, 'account_id', '')
    prior = store.db.execute('SELECT account_id,text FROM outbox_rendered WHERE id=?', (row['id'],)).fetchone()
    if prior:
        if prior['account_id'] != account:
            raise ValueError('Rendered message account changed; reconcile before delivery')
        return prior['text']
    own = store.db.execute('SELECT ask_id FROM operator_ask_parts WHERE event_id=?', (row['id'],)).fetchone()
    outstanding = store.db.execute('''SELECT id,summary FROM operator_asks
        WHERE group_id=? AND resolved_at IS NULL AND delivered_at IS NOT NULL
        ORDER BY created_at,id''', (row['group_id'],)).fetchall()
    others = [ask for ask in outstanding if not own or ask['id'] != own[0]]
    text = row['text']
    if own or others:
        # Fail closed if lookup fails; no untagged send or operator-only fallback.
        tags = ' '.join('@' + reference for reference in transport.admin_references(row['group_id']))
        if own:
            text = tags + '\n' + text
    if others:
        reminders = '\n'.join(f'- {ask["summary"]} [{ask["id"]}]' for ask in others[:8])
        if len(others) > 8:
            reminders += f'\n{len(others)-8} further open asks; inspect workstream ask <name> list.'
        text += '\n\n' + tags + '\nStill awaiting your answer:\n' + reminders
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


def babysitter_notice(pinfo, text, *, operator_ask):
    """Installed adapter: retain failed sends in the shared outbox, never local seen-only state."""
    from .store import Store
    store = Store()
    try:
        with store.lock():
            run = store.get(pinfo['run_id'])
            if run.get('group_id') != pinfo['group']:
                raise ValueError('Babysitter group binding changed; refresh observation')
            key = 'babysitter:' + hashlib.sha256(json.dumps([run['id'], text]).encode()).hexdigest()
            if operator_ask:
                return enqueue(store, run, text, key, text[:200])
            with store.db:
                store.event(run, 'babysitter_status', text, key)
            return {'event_id': key, 'queued': True}
    finally:
        store.db.close()
