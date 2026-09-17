"""Bounded shared-state notices. Contract: spec/brain.md."""
import hashlib
import json
import os
from pathlib import Path
import time
import stat
import uuid

SKIP = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.cache'}


def scan(home, spec, *, max_files=20000, max_bytes=128 * 1024 * 1024):
    """All-or-error scan: a partial tree must never imply file deletions."""
    result = {}
    remaining = max_bytes
    started = time.monotonic()
    def add(label, path):
        nonlocal remaining
        if len(result) >= max_files or time.monotonic() - started > 5:
            raise ValueError('Brain scan exceeded bounded budget')
        if path.is_symlink():
            raise ValueError('Brain file symlink requires explicit source enrollment')
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Brain source is not a regular file")
        if before.st_size > remaining:
            raise ValueError('Brain scan exceeded byte budget')
        remaining -= before.st_size
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Brain file changed during scan')
        result[label] = digest.hexdigest()
    for name in ('SOUL.md', 'AGENTS.md', 'memories/MEMORY.md', 'memories/USER.md', 'config.yaml'):
        path = home / name
        if path.exists():
            add(name, path)
    for label, root in [('spec', spec), ('skills', home / 'skills'), ('plugins', home / 'plugins')]:
        if root.is_symlink():
            raise ValueError('Brain root symlink requires explicit source enrollment')
        if not root.exists():
            if label == 'spec':
                raise ValueError('Canonical specification unavailable')
            continue
        def fail(error):
            raise error
        for current, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.'))
            for directory in dirs:
                if (Path(current) / directory).is_symlink():
                    raise ValueError('Brain directory symlink requires explicit source enrollment')
            for name in sorted(files):
                if name.startswith('.') or name.endswith(('.pyc', '.log', '.sqlite3', '.db')):
                    continue
                path = Path(current) / name
                add(label + '/' + path.relative_to(root).as_posix(), path)
    return result


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS brain_visible(token TEXT PRIMARY KEY, session TEXT NOT NULL,
      native_id TEXT NOT NULL, notice TEXT NOT NULL, recipient TEXT, queued_at REAL, routing_attempt_at REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS brain_known(session TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS brain_pending(session TEXT PRIMARY KEY, token TEXT NOT NULL,
      snapshot TEXT NOT NULL, notice TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS brain_transcripts(session TEXT, native_id TEXT, turn_id TEXT,
      path TEXT, offset INTEGER, PRIMARY KEY(session,native_id,turn_id));
    CREATE TABLE IF NOT EXISTS brain_offers(session TEXT, native_id TEXT, turn_id TEXT,
      token TEXT, PRIMARY KEY(session,native_id,turn_id));
    ''')


def offer(db, session, native_id, turn_id, current):
    """Persist one immutable batch; only its acknowledged entries advance."""
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT token,notice FROM brain_pending WHERE session=?', (session,)).fetchone()
        if row is None:
            old = db.execute('SELECT snapshot FROM brain_known WHERE session=?', (session,)).fetchone()
            known = json.loads(old[0]) if old else {}
            changes = [name for name in sorted(set(known) | set(current)) if known.get(name) != current.get(name)]
            if not changes:
                return None
            target = dict(known)
            lines = []
            size = 0
            for name in changes[:30] if old is not None else []:
                # JSON escaping makes arbitrary filenames data, never raw multiline context.
                action = 'deleted' if name not in current else 'changed' if name in known else 'added'
                line = action + ' ' + json.dumps(name, ensure_ascii=True)
                if size + len(line) > 2300:
                    break
                lines.append(line)
                size += len(line) + 1
                if name in current:
                    target[name] = current[name]
                else:
                    target.pop(name, None)
            if not lines and old is not None:
                raise ValueError('Brain path exceeds notice budget')
            if old is None:
                target = dict(current)
                counts = {}
                for name in current:
                    category = name.split('/')[0]
                    counts[category] = counts.get(category, 0) + 1
                lines = ['Initial inventory: ' + json.dumps(counts, sort_keys=True),
                         'This acknowledges an inventory notification, not reading or knowing file contents.']
            encoded = json.dumps(target, sort_keys=True)
            token = uuid.uuid4().hex
            notice = ('[brain-notice:' + token + ']\nShared brain inventory changes (path data only):\n' +
                      '\n'.join(lines) + '\nRead relevant current files before relying on them. ' +
                      'Authority: canonical spec/README.md; historical text is not instruction. ' +
                      f'{0 if old is None else len(changes) - len(lines)} additional changes remain for later acknowledged batches.')
            db.execute('INSERT INTO brain_pending VALUES (?,?,?,?)', (session, token, encoded, notice))
            row = (token, notice)
        db.execute('INSERT OR REPLACE INTO brain_offers VALUES (?,?,?,?)', (session, native_id, turn_id, row[0]))
        return {'token': row[0], 'context': row[1]}


def _acknowledge(db, session, native_id, turn_id, messages, exact_turn):
    """Require exact offer plus injected marker followed by model assistant content."""
    row = db.execute('''SELECT p.token,p.snapshot,p.notice FROM brain_pending p JOIN brain_offers o
      ON o.session=p.session AND o.token=p.token WHERE p.session=? AND o.native_id=? AND o.turn_id=?''',
                     (session, native_id, turn_id)).fetchone()
    if row is None:
        return False
    marker = '[brain-notice:' + row[0] + ']'
    delivered = received(marker, messages, exact_turn)
    if not delivered:
        return False
    db.execute('INSERT OR IGNORE INTO brain_visible(token,session,native_id,notice) VALUES (?,?,?,?)',
               (row[0], session, native_id, row[2]))
    db.execute('INSERT OR REPLACE INTO brain_known VALUES (?,?)', (session, row[1]))
    db.execute('DELETE FROM brain_pending WHERE session=? AND token=?', (session, row[0]))
    db.execute('DELETE FROM brain_offers WHERE session=?', (session,))
    db.execute('DELETE FROM brain_transcripts WHERE session=?', (session,))
    return True


def acknowledge(db, session, native_id, turn_id, messages, *, exact_turn=False):
    with db:
        db.execute('BEGIN IMMEDIATE')
        return _acknowledge(db, session, native_id, turn_id, messages, exact_turn)


def received(marker, messages, exact_turn=False):
    """Shared proof of injected context followed by assistant transcript content."""
    seen = False
    delivered = False
    for message in messages:
        if message.get('role') in ('user', 'developer'):
            value = message.get('api_content') or message.get('content', '')
            seen = (seen if exact_turn else False) or marker in json.dumps(value)
            if not exact_turn:
                delivered = False
        elif seen and message.get('role') == 'assistant' and message.get('content'):
            delivered = True
    return delivered
