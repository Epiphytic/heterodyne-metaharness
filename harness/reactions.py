"""Durable desired-active reaction intents; contract: spec/reactions.md."""
import hashlib
import time

from .marmot import MarmotError, _hex, validate_reaction
from .store import TERMINAL


def enqueue(store, run, account_id, target_id, emoji, key):
    if run.get('state') in TERMINAL or not run.get('group_id'):
        raise MarmotError('reaction requires a live workstream with an exact group binding')
    if not isinstance(key, str) or not key.strip():
        raise MarmotError('reaction requires a stable nonempty key')
    account_id = _hex(account_id, 'account_id')
    group = _hex(run['group_id'], 'group_id')
    target_id = _hex(target_id, 'target_id')
    validate_reaction(emoji)
    identity = 'reaction:' + hashlib.sha256((run['id'] + '\0' + key).encode()).hexdigest()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        prior = store.db.execute('''SELECT o.run_id,o.group_id,r.account_id,r.target_id,r.emoji
            FROM outbox o LEFT JOIN outbox_reactions r ON r.id=o.id WHERE o.id=?''', (identity,)).fetchone()
        if prior:
            if tuple(prior) != (run['id'], group, account_id, target_id, emoji):
                raise MarmotError('reaction key already identifies different content or routing')
        else:
            store.db.execute('INSERT INTO outbox(id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)',
                             (identity, run['id'], group, '', time.time()))
            store.db.execute('INSERT INTO outbox_reactions VALUES (?,?,?,?)',
                             (identity, account_id, target_id, emoji))
    return {'event_id': identity, 'queued': True}


def send_outbox(store, transport, row):
    reaction = store.db.execute('SELECT account_id,target_id,emoji FROM outbox_reactions WHERE id=?',
                                (row['id'],)).fetchone()
    if reaction:
        if transport.account_id != reaction['account_id']:
            raise MarmotError('reaction account binding changed; restore or reconcile before delivery')
        return transport.react(row['group_id'], reaction['target_id'], reaction['emoji'], row['id'])
    if row['id'].startswith('reaction:'):
        raise MarmotError('reaction payload missing; cannot deliver as ordinary text')
    from .operator_asks import prepare
    return transport.send(row['group_id'], prepare(store, transport, row), row['id'])
