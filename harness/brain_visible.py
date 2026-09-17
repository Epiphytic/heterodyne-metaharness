"""Visible brain receipts through the acknowledged outbox; spec/brain.md."""
import hashlib
import json
import re
import sqlite3
import time


def recipient(store, session, native):
    """Resolve only structured ownership or exact native gateway registry lineage."""
    owner, _, role = session.rpartition(':')
    if role in ('worker', 'manager'):
        row = store.db.execute('SELECT group_id,name FROM runs WHERE id=?', (owner,)).fetchone()
        if row and row[0]:
            return row[0], owner, role
        return None
    if not session.startswith('hermes:'):
        return None
    path = store.root.parent / 'sessions/sessions.json'
    if not path.is_file():
        return None
    entries = json.loads(path.read_text())
    if not isinstance(entries, dict):
        raise ValueError('Unexpected gateway registry schema')
    from .brain_hooks import hermes_origin
    candidates = []
    for key, entry in entries.items():
        if not isinstance(entry, dict) or entry.get('platform') != 'marmot':
            continue
        current = entry.get('session_id')
        if not current:
            continue
        if current != native and 'hermes:' + hermes_origin(store.root.parent, current) != session:
            continue
        parts = key.split(':')
        if (len(parts) != 6 or parts[:3] != ['agent', 'main', 'marmot']
                or parts[3] != 'group' or entry.get('session_key') != key):
            continue
        group = parts[4]
        if re.fullmatch(r'[0-9a-fA-F]+', group):
            candidates.append(group.lower())
    if len(candidates) != 1:
        return None
    return candidates[0], session, 'hermes'


def enqueue_receipts(store):
    """Retry unresolved receipts in the existing dispatcher; never wake a model."""
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='brain_visible'").fetchone():
        return 0
    queued = 0
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        rows = store.db.execute('SELECT token,session,native_id,notice FROM brain_visible WHERE queued_at IS NULL ORDER BY routing_attempt_at,rowid LIMIT 100').fetchall()
        for token, session, native, notice in rows:
            store.db.execute('UPDATE brain_visible SET routing_attempt_at=? WHERE token=?', (time.time(), token))
            try:
                target = recipient(store, session, native)
            except (OSError, ValueError, sqlite3.Error):
                continue  # Receipt remains durable until routing can be verified.
            if target is None:
                continue
            group, owner, role = target
            identity = 'brain-visible:' + hashlib.sha256((token + '\0' + group).encode()).hexdigest()
            now = time.time()
            text = '[' + role + ' brain context received]\n' + notice
            store.db.execute('''INSERT OR IGNORE INTO outbox(id,run_id,group_id,text,created_at)
                                VALUES (?,?,?,?,?)''', (identity, owner, group, text, now))
            store.db.execute('UPDATE brain_visible SET recipient=?,queued_at=? WHERE token=?', (group, now, token))
            queued += 1
    return queued
