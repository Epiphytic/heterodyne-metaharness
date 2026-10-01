"""admind's durable state: one SQLite file in WAL mode (ADR 0001 §8).

- `inbound`: every operator message ID admind has accepted, for replay protection and at-most-once
  delivery (a `received` row that never reached `dispatched` is answered after a restart, never
  replayed to the agent).
- `outbox`: replies and notices, sent in order with a stable idempotency key, so a resend after a crash
  is deduplicated by wn-agent (S4 step 4).
- `alerts`: alert files already relayed, by raw file-name bytes (a name need not be valid UTF-8).
- `kv`: small named values (group, account, agent session, latch, ...).
"""

import functools
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('received', 'dispatched', 'dropped')),
    received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    reply_to TEXT,
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    message_id TEXT);
CREATE TABLE IF NOT EXISTS alerts (name BLOB PRIMARY KEY, relayed_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class OutboxRow:
    seq: int
    key: str
    reply_to: str | None
    text: str
    attempts: int


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Store method: the daemon calls the store from the event loop and from worker threads."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock:  # type: ignore[attr-defined]  # args[0] is the Store
            return method(*args, **kwargs)
    return wrapper


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


class Store:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.lock = threading.RLock()
        # check_same_thread=False: calls are serialized by self.lock (see _locked), not by thread.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    @_locked
    def close(self) -> None:
        self.db.close()

    @_locked
    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    @_locked
    def set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    @_locked
    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    @_locked
    def claim_inbound(self, message_id: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO inbound(message_id, status, received_at) "
                              "VALUES (?, 'received', ?)", (message_id, now()))
        return cur.rowcount == 1

    @_locked
    def set_inbound(self, message_id: str, status: str) -> None:
        self.db.execute("UPDATE inbound SET status = ? WHERE message_id = ?", (status, message_id))

    @_locked
    def inbound_with_status(self, status: str) -> list[str]:
        rows = self.db.execute("SELECT message_id FROM inbound WHERE status = ? ORDER BY received_at, rowid",
                               (status,)).fetchall()
        return [str(r[0]) for r in rows]

    @_locked
    def enqueue(self, key: str, text: str, reply_to: str | None) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status) "
                              "VALUES (?, ?, ?, 'pending')", (key, reply_to, text))
        return cur.rowcount == 1

    @_locked
    def pending(self) -> list[OutboxRow]:
        rows = self.db.execute("SELECT seq, key, reply_to, text, attempts FROM outbox "
                               "WHERE status = 'pending' ORDER BY seq").fetchall()
        return [OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]), str(r[3]), int(r[4]))
                for r in rows]

    @_locked
    def mark_sent(self, seq: int, message_id: str | None) -> None:
        self.db.execute("UPDATE outbox SET status = 'sent', message_id = ? WHERE seq = ?", (message_id, seq))

    @_locked
    def mark_attempt(self, seq: int) -> int:
        self.db.execute("UPDATE outbox SET attempts = attempts + 1 WHERE seq = ?", (seq,))
        row = self.db.execute("SELECT attempts FROM outbox WHERE seq = ?", (seq,)).fetchone()
        return int(row[0])

    @_locked
    def mark_failed(self, seq: int) -> None:
        self.db.execute("UPDATE outbox SET status = 'failed' WHERE seq = ?", (seq,))

    @_locked
    def relayed(self, name: bytes) -> bool:
        return self.db.execute("SELECT 1 FROM alerts WHERE name = ?", (name,)).fetchone() is not None

    @_locked
    def relay_alert(self, name: bytes, key: str, text: str) -> bool:
        """Record the alert and queue its message in one transaction; False if already relayed."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cur = self.db.execute("INSERT OR IGNORE INTO alerts(name, relayed_at) VALUES (?, ?)",
                                  (name, now()))
            if cur.rowcount != 1:
                self.db.execute("ROLLBACK")
                return False
            cur = self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status) "
                                  "VALUES (?, NULL, ?, 'pending')", (key, text))
            if cur.rowcount != 1:
                row = self.db.execute("SELECT text FROM outbox WHERE key = ?", (key,)).fetchone()
                if row is None or row[0] != text:
                    raise ValueError(f"outbox key already in use with different text: {key}")
            self.db.execute("COMMIT")
            return True
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
