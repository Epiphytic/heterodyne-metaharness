"""Lossless observed approval evidence; contract: spec/permission-relay.md."""
import hashlib
import json
import re
import time

# Recognition is only a routing hint, never execution/approval authority.
PROMPT = re.compile(r'Would you like to (?:run the following command|grant these permissions|make the following edits)\?|Do you want to approve network access|needs your approval\.|permission required', re.I)
PART_SIZE = 6000


def approval_region(text):
    """Recognize a current modal footer and retain its complete captured region."""
    tail = '\n'.join(text.splitlines()[-4:])
    if ('↑/↓ to select, Enter to confirm' in tail
            and 'Allow once' in text and 'Deny' in text and 'Dangerous Command' in text):
        return text[text.rfind('Dangerous Command'):]
    footer = re.search(r'(?i)(?:press )?enter to (?:confirm|select).*esc to (?:cancel|dismiss)', tail)
    headings = list(PROMPT.finditer(text))
    return text[headings[-1].start():] if footer and headings else None


def initialize(db):
    schema = '''
    CREATE TABLE IF NOT EXISTS permission_relays(
      id TEXT PRIMARY KEY,run_id TEXT NOT NULL,native_id TEXT NOT NULL,
      pane_id TEXT NOT NULL,digest TEXT NOT NULL,evidence TEXT NOT NULL,created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS permission_parts(
      event_id TEXT PRIMARY KEY,relay_id TEXT NOT NULL,part INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS permission_messages(
      account_id TEXT NOT NULL,group_id TEXT NOT NULL,message_id TEXT NOT NULL,
      event_id TEXT NOT NULL,PRIMARY KEY(account_id,group_id,message_id));
    CREATE TABLE IF NOT EXISTS permission_consents(
      id TEXT PRIMARY KEY,relay_id TEXT NOT NULL,evidence TEXT NOT NULL,created_at REAL NOT NULL);
    '''
    for statement in schema.split(';'):
        if statement.strip():
            db.execute(statement)


def observe(store, run, info):
    text = info.get('text', '')
    if not isinstance(text, str):
        raise ValueError('Approval pane must be text')
    region = approval_region(text)
    if not info.get('alive') or region is None or not run.get('native_session_id'):
        return None
    if not isinstance(text, str) or len(text.encode()) > 1024 * 1024:
        raise ValueError('Approval pane exceeds evidence bound; Hermes must reconcile native request')
    pane = info.get('pane_id')
    if not pane or pane != run.get('pane_id'):
        raise ValueError('Approval evidence requires exact owned pane')
    text = region
    initialize(store.db)
    digest = hashlib.sha256(text.encode()).hexdigest()
    identity = hashlib.sha256(json.dumps([run['id'],run['native_session_id'],pane,digest]).encode()).hexdigest()
    now = time.time()
    header = (f'Native approval evidence {identity}. This is the complete captured approval region, not a guarantee '
              'that the terminal rendered the complete command. Inspect the native request before acceptance. '
              f'Run {run["id"]}; native {run["native_session_id"]}; pane {pane}.\n')
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('INSERT OR IGNORE INTO permission_relays VALUES (?,?,?,?,?,?,?)',
                         (identity,run['id'],run['native_session_id'],pane,digest,text,now))
        store.db.execute('''INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target)
            VALUES (?,?,?,?,?,?)''', ('permission:'+identity,run['id'],header+text,now,'pending','manager'))
        if run.get('group_id'):
            parts = [text[i:i+PART_SIZE] for i in range(0,len(text),PART_SIZE)]
            for index, part in enumerate(parts):
                event = f'permission:{identity}:{index}'
                store.db.execute('INSERT OR IGNORE INTO permission_parts VALUES (?,?,?)', (event,identity,index))
                store.db.execute('''INSERT OR IGNORE INTO outbox(id,run_id,group_id,text,created_at)
                    VALUES (?,?,?,?,?)''', (event,run['id'],run['group_id'],header+f'Part {index+1}/{len(parts)}\n'+part,now))
                from .operator_asks import register
                row = store.db.execute('SELECT * FROM outbox WHERE id=?', (event,)).fetchone()
                register(store, row, f'Inspect native approval in {run["name"]}, pane {pane}', 'permission:'+identity)
    return identity


def delivered(store, transport, row, response):
    """Retain exact visible-message identities for subsequent verified consent."""
    initialize(store.db)
    if not store.db.execute('SELECT 1 FROM permission_parts WHERE event_id=?', (row['id'],)).fetchone():
        return
    from .marmot import _hex
    account = _hex(transport.account_id,'account_id')
    ids = response.get('message_ids_hex') if isinstance(response,dict) else None
    if not isinstance(ids,list) or not ids:
        raise ValueError('Approval delivery has no acknowledged message identities')
    for native in ids:
        native = _hex(native,'message_id')
        prior = store.db.execute('SELECT event_id FROM permission_messages WHERE account_id=? AND group_id=? AND message_id=?',
                                 (account,row['group_id'],native)).fetchone()
        if prior and prior[0] != row['id']:
            raise ValueError('Approval message identity collision')
        store.db.execute('INSERT OR IGNORE INTO permission_messages VALUES (?,?,?,?)',
                         (account,row['group_id'],native,row['id']))
