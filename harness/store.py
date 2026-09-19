"""Transactional durable state. Models and tmux are never the source of ownership."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

TERMINAL = ('completed', 'stopped', 'archived')


def home_path():
    return Path(os.environ.get('HERMES_HOME', '~/.hermes')).expanduser()


class Store:
    def __init__(self, home=None):
        self.root = Path(home or home_path()) / 'workstreams'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / 'harness.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        # Small serialized transactions do not need WAL; rollback journaling also
        # avoids dependency on host-specific SQLite WAL checkpoint fixes.
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, repo TEXT, agent TEXT,
          workdir TEXT, group_id TEXT, state TEXT, created_at REAL, updated_at REAL,
          data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (
          repo TEXT PRIMARY KEY, group_id TEXT, status TEXT, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, text TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS outbox (
          id TEXT PRIMARY KEY, run_id TEXT, group_id TEXT, text TEXT,
          created_at REAL, delivered_at REAL, attempts INTEGER DEFAULT 0,
          next_attempt REAL DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS outbox_reactions (
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, target_id TEXT NOT NULL, emoji TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS status_notices (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL, digest TEXT NOT NULL, group_id TEXT, kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox (
          id TEXT PRIMARY KEY, run_id TEXT, text TEXT, created_at REAL, state TEXT);
        CREATE TABLE IF NOT EXISTS identities (
          alias TEXT PRIMARY KEY, run_id TEXT, role TEXT, agent TEXT,
          native_id TEXT, group_id TEXT);
        CREATE TABLE IF NOT EXISTS native_sessions (
          run_id TEXT, role TEXT, native_id TEXT, previous_id TEXT, first_seen REAL,
          UNIQUE(run_id,role,native_id));
        ''')
        self.db.commit()
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(inbox)')}
        if 'target' not in columns:
            self.db.execute("ALTER TABLE inbox ADD COLUMN target TEXT NOT NULL DEFAULT 'manager'")
            self.db.commit()
        os.chmod(self.root / 'harness.sqlite3', 0o600)

    @contextlib.contextmanager
    def lock(self, blocking=True):
        with open(self.root / '.supervisor.lock', 'a') as handle:
            flag = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(handle, flag)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def get(self, name):
        row = self.db.execute('SELECT data FROM runs WHERE name=? OR id=?', (name, name)).fetchone()
        if row is None:
            row = self.db.execute('SELECT data FROM runs JOIN identities ON runs.id=identities.run_id WHERE alias=?', (name,)).fetchone()
        if row is None:
            raise ValueError(f'Unknown workstream: {name}')
        return json.loads(row['data'])

    def runs(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM runs ORDER BY created_at')]

    def save(self, run):
        run['updated_at'] = time.time()
        self.db.execute('''INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET group_id=excluded.group_id,
          state=excluded.state, workdir=excluded.workdir, updated_at=excluded.updated_at,
          data=excluded.data''', (run['id'], run['name'], run.get('repo'), run['agent'],
          run['workdir'], run.get('group_id'), run['state'], run['created_at'],
          run['updated_at'], json.dumps(run)))
        self.identities(run)

    def identities(self, run):
        for role, target in [('worker', run), ('manager', run.get('manager', {}))]:
            alias = f"workstream-{run['name']}-{role}"
            native = target.get('native_session_id')
            previous = self.db.execute('SELECT native_id FROM identities WHERE alias=?', (alias,)).fetchone()
            old_id = previous[0] if previous else None
            self.db.execute('INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?,?)',
                            (alias, run['id'], role, target.get('agent', 'hermes'), native, run.get('group_id')))
            if native:
                self.db.execute('INSERT OR IGNORE INTO native_sessions VALUES (?,?,?,?,?)',
                                (run['id'], role, native, old_id if old_id != native else None, time.time()))

    def event(self, run, kind, text, event_id=None, group=None, operator_ask=False):
        identity = event_id or str(uuid.uuid4())
        now = time.time()
        if not self.db.in_transaction:
            self.db.execute('BEGIN IMMEDIATE')
        if self.db.execute('SELECT 1 FROM events WHERE id=?', (identity,)).fetchone():
            return identity
        target = group or run.get('group_id')
        from .status import KINDS, record
        if kind in KINDS and not record(self, run, identity, kind, text, target, now):
            return identity
        self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                        (identity, run['id'], kind, text, now))
        if target:
            self.db.execute('''INSERT OR IGNORE INTO outbox
              (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
              (identity, run['id'], target, f"[{run['name']}] {text}", now))
            from .operator_asks import ASK_KINDS, register
            if operator_ask or kind in ASK_KINDS:
                row = self.db.execute('SELECT * FROM outbox WHERE id=?', (identity,)).fetchone()
                register(self, row, text)
        return identity

    def project(self, repo):
        row = self.db.execute('SELECT * FROM projects WHERE repo=?', (repo,)).fetchone()
        return dict(row) if row else None

    def save_project(self, repo, group, status, data):
        self.db.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?,?)',
                        (repo, group, status, json.dumps(data)))

    def checkpoint(self, run):
        """Human/manager-readable projection; the database remains authoritative."""
        directory = self.root / 'runs' / run['id']
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = directory / 'checkpoint.json'
        temp = target.with_suffix('.tmp')
        with temp.open('w') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(run, indent=2) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return target
