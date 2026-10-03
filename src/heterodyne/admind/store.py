"""admind's durable state: one SQLite file in WAL mode (ADR 0001 §8).

- `inbound`: every operator message ID admind has accepted, for replay protection and at-most-once
  delivery. A prompt goes `received` then `dispatched` (claimed before it is pasted); a command goes
  `received`, `executing` (persisted before it runs), then `done` with its reply queued in the same
  transaction. After a restart a `received` or `executing` row is answered, never replayed.
- `outbox`: replies and notices, sent in order with a stable idempotency key, so a resend after a crash
  is deduplicated by wn-agent (S4 step 4).
- `alerts`: alert files already relayed, by raw file-name bytes (a name need not be valid UTF-8).
- `kv`: small named values (group, account, agent session, latch, ...).
"""

import contextlib
import functools
import os
import re
import sqlite3
import stat
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from heterodyne.admind import chunk
from heterodyne.admind.redact import redact, redact_continuation

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('received', 'dispatched', 'executing', 'done', 'dropped')),
    received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    reply_to TEXT,
    lane INTEGER NOT NULL DEFAULT 1 CHECK (lane IN (1, 2)),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    message_id TEXT);
-- parts of a `!details` reply being prepared: invisible to the outbox until publish_details moves them
CREATE TABLE IF NOT EXISTS details_stage (
    token TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY (token, idx));
CREATE TABLE IF NOT EXISTS alerts (name BLOB PRIMARY KEY, relayed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prompts (
    message_id TEXT PRIMARY KEY, operator TEXT NOT NULL, received_at TEXT NOT NULL, words TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    session TEXT NOT NULL,
    reply_to TEXT,
    origin TEXT NOT NULL,
    text TEXT NOT NULL,
    transcript TEXT,
    transcript_start INTEGER,
    transcript_end INTEGER,
    status TEXT NOT NULL CHECK (status IN ('verbatim', 'summarizing', 'summarized', 'batched')),
    batch_id INTEGER,
    batch_seq INTEGER,              -- the order replies joined the backstop in, across batches (B10)
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'posted')),
    attempt INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS posts (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('verbatim', 'summary', 'batch')),
    turn_id INTEGER,
    batch_id INTEGER);
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
    lane: int


@dataclass(frozen=True)
class PromptRow:
    operator: str
    received_at: str
    words: str


@dataclass(frozen=True)
class TurnRow:
    turn_id: int
    key: str
    session: str
    reply_to: str | None
    origin: str
    text: str
    transcript: str | None
    transcript_start: int | None
    transcript_end: int | None
    status: str
    batch_id: int | None


@dataclass(frozen=True)
class PostRow:
    key: str
    kind: str
    turn_id: int | None
    batch_id: int | None


_TURN = ("turn_id, key, session, reply_to, origin, text, transcript, transcript_start, transcript_end, "
         "status, batch_id")       # fixed column list: the only thing interpolated into the queries below


def _opt_int(v: object) -> int | None:
    return None if v is None else int(cast(int, v))


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


def _turn(r: tuple[object, ...]) -> TurnRow:
    return TurnRow(int(cast(int, r[0])), str(r[1]), str(r[2]), _opt_str(r[3]), str(r[4]), str(r[5]),
                   _opt_str(r[6]), _opt_int(r[7]), _opt_int(r[8]), str(r[9]), _opt_int(r[10]))


def _post(r: tuple[object, ...]) -> PostRow:
    return PostRow(str(r[0]), str(r[1]), _opt_int(r[2]), _opt_int(r[3]))


# The keys of a chunked reply: `reply:<session>:<seq>:<i>` (an agent's reply) and `<tag>:<message id>:<i>`
# (`Admind.reply`). Every other key (`adopt-hold:1`, `hook-lost:3`, `ready`, ...) is one whole message.
_CHUNKED = re.compile(r"\A(reply:[^:]+:\d+|[a-z]+:[0-9a-f]{64}):(\d+)\Z")
PARTLY_SENT = "<redacted continuation of a partly sent reply>"


def _like(text: str) -> str:
    """`text` as a literal LIKE prefix (escape `\\`, `%` and `_`)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Store method: the daemon calls the store from the event loop and from worker threads."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock:  # type: ignore[attr-defined]  # args[0] is the Store
            return method(*args, **kwargs)
    return wrapper


def private_dir(path: Path) -> None:
    """Create `path` (and missing parents) and make it a 0700 directory owned by this user. A symlink in
    the final component, or a directory someone else owns, is refused: `path` is lstat'ed, opened with
    O_NOFOLLOW | O_DIRECTORY and the two are compared, so the chmod lands on the directory that was
    checked. Parents above `path` are not inspected."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = os.lstat(path)
    if not stat.S_ISDIR(before.st_mode):
        raise PermissionError(f"{path} is not a plain directory (a symlink is refused)")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PermissionError(f"{path} changed while it was checked")
        if opened.st_uid != os.geteuid():
            raise PermissionError(f"{path} is not owned by the service user")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


class Store:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise PermissionError(f"{path} is not a regular file")
            os.fchmod(fd, 0o600)
            # sqlite3 reopens by pathname, so the descriptor above cannot pin it. Checked immediately
            # before connect: the path must still be the regular file just opened, not a symlink. A swap
            # in the gap that remains needs write access to the parent, which private_dir made 0700
            # and owned by the service user.
            now_at = os.lstat(path)
            if (not stat.S_ISREG(now_at.st_mode)
                    or (now_at.st_dev, now_at.st_ino) != (opened.st_dev, opened.st_ino)):
                raise PermissionError(f"{path} is not the regular file that was opened")
        finally:
            os.close(fd)
        self.lock = threading.RLock()
        self._depth = 0         # open transaction() nesting, guarded by self.lock
        # check_same_thread=False: calls are serialized by self.lock (see _locked), not by thread.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        columns = {str(r[1]) for r in self.db.execute("PRAGMA table_info(outbox)")}
        if "lane" not in columns:       # a database created before revision 13 (B17)
            with self.transaction():    # the column and the redaction marker: both or neither
                self.db.execute("ALTER TABLE outbox ADD COLUMN lane INTEGER NOT NULL DEFAULT 1")
                self.db.execute("INSERT OR REPLACE INTO kv(key, value) "
                                "VALUES ('outbox_needs_redaction', '1')")

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
    def enqueue(self, key: str, text: str, reply_to: str | None, lane: int = 1) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status, lane) "
                              "VALUES (?, ?, ?, 'pending', ?)", (key, reply_to, text, lane))
        return cur.rowcount == 1

    @_locked
    def stage_details(self, token: str, first: int, parts: list[str]) -> None:
        """Hold parts first.. of a reply that is not yet visible to delivery (see publish_details)."""
        with self.transaction():
            self.db.executemany("INSERT OR REPLACE INTO details_stage(token, idx, text) VALUES (?, ?, ?)",
                                [(token, first + i, p) for i, p in enumerate(parts)])

    @_locked
    def publish_details(self, token: str, prefix: str, reply_to: str, lane: int) -> int:
        """Move every staged part to the outbox, keyed `{prefix}:{idx}`, in one statement: all or none.
        Returns how many. The caller sets its inbound status in the same transaction."""
        with self.transaction():
            cur = self.db.execute(
                "INSERT OR IGNORE INTO outbox(key, reply_to, text, status, lane) "
                "SELECT ? || ':' || idx, ?, text, 'pending', ? FROM details_stage WHERE token = ? "
                "ORDER BY idx", (prefix, reply_to, lane, token))
            self.db.execute("DELETE FROM details_stage WHERE token = ?", (token,))
            return cur.rowcount

    @_locked
    def discard_details(self, token: str | None = None) -> None:
        """Drop one staged reply, or (no token) every one: a restart leaves no live preparation."""
        if token is None:
            self.db.execute("DELETE FROM details_stage")
        else:
            self.db.execute("DELETE FROM details_stage WHERE token = ?", (token,))

    @_locked
    def pending(self) -> list[OutboxRow]:
        rows = self.db.execute("SELECT seq, key, reply_to, text, attempts, lane FROM outbox "
                               "WHERE status = 'pending' ORDER BY lane, seq").fetchall()
        return [OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]), str(r[3]), int(r[4]),
                          int(r[5])) for r in rows]

    @_locked
    def next_pending(self) -> OutboxRow | None:
        """The next message to send: lane 1 (commands, alerts, notices, summaries, batches, verbatim
        replies) before lane 2 (`!details`), each in order (B14)."""
        r = self.db.execute("SELECT seq, key, reply_to, text, attempts, lane FROM outbox "
                            "WHERE status = 'pending' ORDER BY lane, seq LIMIT 1").fetchone()
        return None if r is None else OutboxRow(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]),
                                                str(r[3]), int(r[4]), int(r[5]))

    @_locked
    def redact_pending_outbox(self, chunk_chars: int) -> int:
        """Once, after the upgrade to revision 13 (B17): redact what a plan-2 admind queued and never sent.
        The pending chunks of one reply (keys `<prefix>:<i>`) are joined in order first, so a value split
        across their boundaries is still found, then re-chunked under new keys `<prefix>:r<i>`. If the
        reply's earlier chunks were already sent, they are joined in order and a value they began is hidden
        to its end (it can span several of them); if one of them is missing or not sent, the pending text is
        replaced whole (fail closed). Returns the number of rows rewritten; one transaction."""
        if self.get("outbox_needs_redaction") is None:
            return 0
        with self.transaction():
            rows = self.db.execute("SELECT seq, key, reply_to, text FROM outbox WHERE status = 'pending' "
                                   "ORDER BY seq").fetchall()
            groups: dict[str, list[tuple[int, int, str | None, str]]] = {}
            for seq, key, reply_to, text in rows:
                m = _CHUNKED.match(str(key))
                prefix, index = (m.group(1), int(m.group(2))) if m else (str(key), -1)
                groups.setdefault(prefix, []).append(
                    (index, int(seq), None if reply_to is None else str(reply_to), str(text)))
            for prefix, parts in groups.items():
                parts.sort()
                first, reply_to = parts[0][0], parts[0][2]
                joined = "".join(p[3] for p in parts)
                if first <= 0:
                    clean = redact(joined)
                else:
                    sent = self.db.execute(
                        "SELECT key, text FROM outbox WHERE key LIKE ? ESCAPE '\\' AND status = 'sent'",
                        (_like(prefix) + ":%",)).fetchall()
                    earlier = {int(m.group(2)): str(t) for k, t in sent
                               if (m := _CHUNKED.match(str(k))) and m.group(1) == prefix}
                    if all(i in earlier for i in range(first)):
                        clean = redact_continuation("".join(earlier[i] for i in range(first)), joined)
                    else:
                        clean = PARTLY_SENT
                self.db.executemany("DELETE FROM outbox WHERE seq = ?", [(p[1],) for p in parts])
                if first < 0:       # an unchunked key keeps its key
                    pieces = [(prefix, clean)]
                else:
                    pieces = [(f"{prefix}:r{i}", part)
                              for i, part in enumerate(chunk.split(clean, chunk_chars))]
                self.db.executemany("INSERT INTO outbox(key, reply_to, text, status, lane) "
                                    "VALUES (?, ?, ?, 'pending', 1)", [(k, reply_to, t) for k, t in pieces])
            self.db.execute("DELETE FROM kv WHERE key = 'outbox_needs_redaction'")
        return len(rows)

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
    def record_prompt(self, message_id: str, operator: str, words: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO prompts(message_id, operator, received_at, words) "
                        "VALUES (?, ?, ?, ?)", (message_id, operator, now(), words))

    @_locked
    def prompt(self, message_id: str) -> PromptRow | None:
        r = self.db.execute("SELECT operator, received_at, words FROM prompts WHERE message_id = ?",
                            (message_id,)).fetchone()
        return None if r is None else PromptRow(str(r[0]), str(r[1]), str(r[2]))

    @_locked
    def add_turn(self, key: str, session: str, reply_to: str | None, origin: str, text: str,
                 transcript: str | None, start: int | None, end: int | None, status: str) -> int:
        self.db.execute("INSERT OR IGNORE INTO turns(key, session, reply_to, origin, text, transcript, "
                        "transcript_start, transcript_end, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (key, session, reply_to, origin, text, transcript, start, end, status, now()))
        return int(self.db.execute("SELECT turn_id FROM turns WHERE key = ?", (key,)).fetchone()[0])

    @_locked
    def turn(self, turn_id: int) -> TurnRow | None:
        r = self.db.execute(f"SELECT {_TURN} FROM turns WHERE turn_id = ?",  # noqa: S608
                            (turn_id,)).fetchone()
        return None if r is None else _turn(r)

    @_locked
    def turns_with_status(self, status: str) -> list[TurnRow]:
        rows = self.db.execute(f"SELECT {_TURN} FROM turns WHERE status = ? ORDER BY turn_id",  # noqa: S608
                               (status,)).fetchall()
        return [_turn(r) for r in rows]

    @_locked
    def set_turn_status(self, turn_id: int, status: str) -> None:
        self.db.execute("UPDATE turns SET status = ? WHERE turn_id = ?", (status, turn_id))

    @_locked
    def add_to_batch(self, turn_id: int, batch_id: int) -> None:
        """The reply joins the batch, after every reply that joined a batch before it: §8's "in order" is
        the order replies reach the backstop, not the order their turns began (Codex r5 finding 3). An
        older reply whose summary fails late comes after a newer one whose delivery failed first."""
        self.db.execute("UPDATE turns SET status = 'batched', batch_id = ?, "
                        "batch_seq = (SELECT COALESCE(MAX(batch_seq), 0) + 1 FROM turns) WHERE turn_id = ?",
                        (batch_id, turn_id))

    @_locked
    def open_batch(self, at: float, seconds: float) -> int:
        """The open batch whose window is still running at `at`, or a new one opened at `at` (a batch opens
        with its first reply). A batch whose window has passed takes no more replies, even before
        `batch_loop` posts it (Codex r5 finding 4)."""
        r = self.db.execute("SELECT batch_id FROM batches WHERE status = 'open' AND opened_at + ? > ? "
                            "ORDER BY batch_id LIMIT 1", (seconds, at)).fetchone()
        if r is not None:
            return int(r[0])
        cur = self.db.execute("INSERT INTO batches(opened_at, status) VALUES (?, 'open')", (at,))
        return int(cur.lastrowid or 0)

    @_locked
    def due_batches(self, at: float, seconds: float) -> list[tuple[int, int]]:
        rows = self.db.execute("SELECT batch_id, attempt FROM batches WHERE status = 'open' "
                               "AND opened_at + ? <= ? ORDER BY batch_id", (seconds, at)).fetchall()
        return [(int(r[0]), int(r[1])) for r in rows]

    @_locked
    def batch_turns(self, batch_id: int) -> list[TurnRow]:
        rows = self.db.execute(f"SELECT {_TURN} FROM turns WHERE batch_id = ? ORDER BY batch_seq",  # noqa: S608
                               (batch_id,)).fetchall()
        return [_turn(r) for r in rows]

    @_locked
    def close_batch(self, batch_id: int) -> None:
        """Its message is queued; delivery is the outbox's job."""
        self.db.execute("UPDATE batches SET status = 'posted' WHERE batch_id = ?", (batch_id,))

    @_locked
    def reopen_batch(self, batch_id: int, at: float) -> None:
        """Its message was given up on: post it again after a new window, under a new key (the attempt)."""
        self.db.execute("UPDATE batches SET status = 'open', opened_at = ?, attempt = attempt + 1 "
                        "WHERE batch_id = ?", (at, batch_id))

    @_locked
    def record_post(self, key: str, kind: str, turn_id: int | None, batch_id: int | None) -> None:
        self.db.execute("INSERT OR IGNORE INTO posts(key, kind, turn_id, batch_id) VALUES (?, ?, ?, ?)",
                        (key, kind, turn_id, batch_id))

    @_locked
    def post_record(self, key: str) -> PostRow | None:
        r = self.db.execute("SELECT key, kind, turn_id, batch_id FROM posts WHERE key = ?", (key,)).fetchone()
        return None if r is None else _post(r)

    @_locked
    def details_target(self, message_id: str | None) -> PostRow | None:
        """The record behind a delivered message (B12): the sent outbox row with that message ID, or without
        one the latest summary or batch that was delivered. Pending and failed rows never count."""
        if message_id is not None:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM outbox o JOIN posts p "
                                "ON p.key = o.key WHERE o.message_id = ? AND o.status = 'sent'",
                                (message_id,)).fetchone()
        else:
            r = self.db.execute("SELECT p.key, p.kind, p.turn_id, p.batch_id FROM posts p JOIN outbox o "
                                "ON o.key = p.key WHERE p.kind IN ('summary', 'batch') AND o.status = 'sent' "
                                "ORDER BY o.seq DESC LIMIT 1").fetchone()
        return None if r is None else _post(r)

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        """Run the block's store calls as one SQLite transaction: all of them commit, or (on any
        exception, a simulated crash included) none do. Re-entrant: a nested block joins the outer one.
        The store lock is held throughout, so keep the block short and free of awaits."""
        with self.lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    @_locked
    def relay_alert(self, name: bytes, key: str, text: str) -> bool:
        """Record the alert and queue its message in one transaction; False if already relayed."""
        with self.transaction():
            cur = self.db.execute("INSERT OR IGNORE INTO alerts(name, relayed_at) VALUES (?, ?)",
                                  (name, now()))
            if cur.rowcount != 1:
                return False
            cur = self.db.execute("INSERT OR IGNORE INTO outbox(key, reply_to, text, status, lane) "
                                  "VALUES (?, NULL, ?, 'pending', 1)", (key, text))
            if cur.rowcount != 1:
                row = self.db.execute("SELECT text FROM outbox WHERE key = ?", (key,)).fetchone()
                if row is None or row[0] != text:
                    raise ValueError(f"outbox key already in use with different text: {key}")
            return True
