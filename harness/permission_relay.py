"""Lossless observed approval evidence; contract: spec/permission-relay.md."""
import hashlib
import json
import re
import time

# Recognition is only a routing hint, never execution/approval authority.
PROMPT = re.compile(r'Would you like to (?:run the following command|grant these permissions|make the following edits)\?|Do you want to approve network access|needs your approval\.|permission required', re.I)


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


def observe(store, run, info, *, detected=False, beads=None):
    text = info.get('text', '')
    if not isinstance(text, str):
        raise ValueError('Approval pane must be text')
    region = approval_region(text)
    if region is None and detected:
        region = text  # Unrecognized native UI: retain the whole bounded capture for inspection.
    if not info.get('alive') or region is None:
        return None
    if not isinstance(text, str) or len(text.encode()) > 1024 * 1024:
        raise ValueError('Approval pane exceeds evidence bound; Hermes must reconcile native request')
    pane = info.get('pane_id')
    if not pane or pane != run.get('pane_id'):
        raise ValueError('Approval evidence requires exact owned pane')
    text = region
    initialize(store.db)
    digest = hashlib.sha256(text.encode()).hexdigest()
    native = run.get('native_session_id') or 'unknown'
    identity = hashlib.sha256(json.dumps([run['id'],native,pane,digest]).encode()).hexdigest()
    now = time.time()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('INSERT OR IGNORE INTO permission_relays VALUES (?,?,?,?,?,?,?)',
                         (identity,run['id'],native,pane,digest,text,now))
        from .manager_tasks import native_approval
        request_id = native_approval(store, run, identity, pane=pane, native=native, region=text,
                                     subject='manager' if run.get('manager', {}).get('pane_id') == pane else 'worker')
    if beads is not None and beads.enabled:
        from .manager_tasks import materialize
        from .beads import BeadsError
        row = store.db.execute('SELECT * FROM manager_tasks WHERE request_id=?', (request_id,)).fetchone()
        if not row['issue_id']:
            try:
                materialize(store, beads, row)
            except (BeadsError, RuntimeError, ValueError):
                pass  # The one-minute pickup retries the durable request.
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
