"""wsd's SQLite journal (ADR 0001 §3.3, §5.4): one file in WAL mode under the wsd state directory.

What it holds, and what it never does:
- `inbox`: decision events keyed by (surface, event ID), `pending` until committed, superseded,
  rejected or escalated. The decision itself lives on the bead (§5.4); the inbox only orders and dedups.
- `ops`: the pickup, park, resume, release and escalation journals, one row per operation with its last
  completed step, so a crash at any point is replayed from the step it reached (§4.3).
- `beads`, `holds`, `workstreams`: the state every surface shows (plan 6), each with a concrete reason.
- `events`: an append-only progress log with a cursor, for per-message progress reactions (plan 6). A
  state event's `detail` is JSON with the reason and its concrete detail (blocker IDs, the failing step).

Beads stay the source of truth. Opening the journal checks it first, through a read-only connection: an
unreadable, corrupt or foreign file, or one whose schema differs in any table, column, constraint or
index, is refused (JournalCorrupt) and never deleted or recreated, because a lost journal must be
noticed, not papered over. A refused file and its write-ahead log keep their bytes (`_validate` names
the sidecars SQLite may add). A journal locked by another connection, at open or later, is JournalBusy,
which is transient and says nothing about the file. A missing file is created atomically and reported
as `fresh`.
`backup()` writes a consistent online copy (SQLite's backup API, safe under WAL) for the beads backups (§3.3).
"""

import contextlib
import functools
import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

import msgspec

from heterodyne.fsutil import private_dir
from heterodyne.wsd.launches import SET_ONCE, LaunchEntry, Receipt
from heterodyne.wsd.states import BeadState, Reason, WsState, check

SCHEMA_VERSION = "2"
V1_VERSION = "1"
# Plan 3's schema, frozen: a version 1 journal must match it exactly to be upgraded (`upgrade.py`).
V1_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE inbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    surface TEXT NOT NULL,
    event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ws TEXT,
    bead TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed', 'superseded', 'rejected', 'needs_human')),
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (surface, event_id));
CREATE TABLE ops (
    op_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('pickup', 'park', 'resume', 'release', 'escalate')),
    ws TEXT NOT NULL,
    bead TEXT NOT NULL,
    step TEXT NOT NULL,
    data TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('open', 'done', 'abandoned', 'stuck')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'open';
CREATE TABLE beads (
    ws TEXT NOT NULL, bead TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, detail TEXT NOT NULL,
    since TEXT NOT NULL, PRIMARY KEY (ws, bead));
CREATE TABLE holds (
    ws TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, since TEXT NOT NULL,
    PRIMARY KEY (ws, reason));
CREATE TABLE workstreams (ws TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, ws TEXT NOT NULL, bead TEXT,
    kind TEXT NOT NULL, detail TEXT NOT NULL, ref TEXT);
"""
# Version 2 (AU-3): one statement each, so the upgrade runs them one `execute` at a time inside its own
# transaction (`executescript` would commit first). The text is the schema: never edit one in place.
V2_TABLES = (
    """CREATE TABLE launches (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL CHECK (generation >= 1),
    ws TEXT NOT NULL, bead TEXT NOT NULL, role TEXT NOT NULL, profile TEXT NOT NULL,
    account TEXT NOT NULL, credential_key TEXT NOT NULL, model_passed TEXT NOT NULL,
    dispatched_at TEXT, native_id TEXT, model_reported TEXT,
    outcome TEXT CHECK (outcome IN ('launched', 'abandoned')),
    adopted INTEGER NOT NULL DEFAULT 0 CHECK (adopted IN (0, 1)),
    journaled_at TEXT NOT NULL,
    rebuilt_at TEXT,
    PRIMARY KEY (session_key, generation))""",
    """CREATE TABLE receipts (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('started', 'refused')),
    refusal TEXT CHECK (refusal IN ('failed', 'unavailable')),
    tmux_session TEXT, tmux_pane TEXT, pane_pid INTEGER, native_id TEXT, error TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (session_key, generation),
    FOREIGN KEY (session_key, generation) REFERENCES launches (session_key, generation),
    CHECK ((kind = 'refused') = (refusal IS NOT NULL)))""",
    """CREATE TABLE adoptions (
    ws TEXT NOT NULL, bead TEXT NOT NULL, session_key TEXT,
    verdict TEXT NOT NULL CHECK (verdict IN ('adopted', 'held')), detail TEXT NOT NULL,
    facts TEXT NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0 CHECK (settled IN (0, 1)),
    resolution TEXT CHECK (resolution IN ('adopted')), resolved_at TEXT, resolved_by TEXT,
    PRIMARY KEY (ws, bead))""",
    """CREATE TABLE deferrals (
    session_key TEXT NOT NULL, number INTEGER NOT NULL, bead TEXT NOT NULL, role TEXT NOT NULL,
    profile TEXT NOT NULL, reason TEXT NOT NULL, defer_until TEXT, trust TEXT NOT NULL,
    PRIMARY KEY (session_key, number))""",
    """CREATE TABLE account_usage (
    credential_key TEXT NOT NULL, window_id TEXT NOT NULL, kind TEXT NOT NULL, used_percent REAL,
    resets_at TEXT, observed_at TEXT NOT NULL, receipt_seq INTEGER NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (credential_key, window_id))""",
    """CREATE TABLE account_usage_untrusted (
    session_key TEXT NOT NULL, generation INTEGER NOT NULL, window_id TEXT NOT NULL, kind TEXT NOT NULL,
    used_percent REAL, resets_at TEXT, observed_at TEXT NOT NULL, receipt_seq INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (session_key, generation, window_id))""",
    """CREATE TABLE account_exhausted (
    credential_key TEXT PRIMARY KEY, until TEXT, observed_at TEXT NOT NULL, receipt_seq INTEGER NOT NULL)""",
)
V2_META = (("usage_receipt_seq", "0"),)     # orders AU-5's usage receipts; unused until then
SCHEMA = V1_SCHEMA + "".join(f"{stmt};\n" for stmt in V2_TABLES)
BUSY_TIMEOUT = 5.0  # seconds a connection waits on another's lock before SQLITE_BUSY


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JournalCorrupt(Exception):
    """The journal failed its integrity check. wsd refuses to start; the file is left for the operator."""


class JournalNeedsUpgrade(Exception):
    """The journal is plan 3's exactly (schema and version 1). Nothing was written; `upgrade.run` upgrades
    it in one transaction."""


class JournalBusy(Exception):
    """The journal is locked by another connection (SQLITE_BUSY or SQLITE_LOCKED, extended codes
    included), when it is opened or by any Journal method after. Transient: the file is not suspect, so
    it is never reported as corrupt, and the method's writes did not happen; retry once the other holder
    lets go. Inside a `transaction()` block that promise is the block's: the whole block rolls back when
    the exception leaves it. A block that catches it and carries on commits whatever else it wrote, a
    method's half included (a bead row without its event), so never catch it inside a block."""


class JournalUnusable(Exception):
    """A transaction's cleanup failed (its ROLLBACK, or reading the connection's state), so the connection
    may still hold an open write transaction and SQLite's write lock. The file is not suspect. Every public
    operation except close() raises this, before and after close, with the cleanup error as its cause;
    open a new Journal (restart wsd)."""


class OpConflict(Exception):
    """An operation is already open for this bead: finish or replay it first."""


class EntryConflict(Exception):
    """A launch entry's set-once field already holds a different value, or the entry already exists
    with other fields: an entry is never changed."""


class InboxStatus(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    NEEDS_HUMAN = "needs_human"


class OpKind(StrEnum):
    PICKUP = "pickup"
    PARK = "park"
    RESUME = "resume"
    RELEASE = "release"
    ESCALATE = "escalate"


class OpStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    ABANDONED = "abandoned"
    STUCK = "stuck"


@dataclass(frozen=True)
class InboxRow:
    seq: int
    surface: str
    event_id: str
    kind: str
    ws: str | None
    bead: str | None
    payload: str
    status: InboxStatus
    attempts: int


@dataclass(frozen=True)
class Op:
    op_id: str
    kind: OpKind
    ws: str
    bead: str
    step: str
    data: dict[str, str]
    attempts: int
    status: OpStatus


@dataclass(frozen=True)
class BeadRow:
    ws: str
    bead: str
    state: BeadState
    reason: Reason | None
    detail: str
    since: str


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    ws: str
    bead: str | None
    kind: str
    detail: str
    ref: str | None


@dataclass(frozen=True)
class Snapshot:
    """Everything a status surface needs about one workstream, read in one transaction."""
    ws: str
    state: WsState | None
    holds: dict[Reason, str]
    beads: list[BeadRow]
    ops: list[Op]
    last_event: int


@dataclass(frozen=True)
class Adoption:
    """A legacy bead's adoption verdict (AU-3 §2.5): the upgrade-time evidence, never changed, plus
    `settled` and the release's `resolution`, each set once."""
    ws: str
    bead: str
    session_key: str | None
    verdict: str            # "adopted" or "held"
    detail: str
    facts: str              # JSON: the upgrade-time facts
    settled: bool
    resolution: str | None
    resolved_at: str | None
    resolved_by: str | None

    @property
    def unresolved_hold(self) -> bool:
        return self.verdict == "held" and self.resolution is None


_DATA = msgspec.json.Decoder(dict[str, str])
_LAUNCH_COLS = ("session_key", "generation", "ws", "bead", "role", "profile", "account", "credential_key",
                "model_passed", "dispatched_at", "native_id", "model_reported", "outcome", "adopted",
                "journaled_at")
_LAUNCH = ", ".join(_LAUNCH_COLS)
_LAUNCH_N = len(_LAUNCH_COLS)
_RECEIPT = "session_key, generation, kind, refusal, tmux_session, tmux_pane, pane_pid, native_id, error"
_ADOPTION = "ws, bead, session_key, verdict, detail, facts, settled, resolution, resolved_at, resolved_by"
type ReceiptKind = Literal["started", "refused"]
type Refusal = Literal["failed", "unavailable"]


def _launch_row(entry: LaunchEntry) -> tuple[object, ...]:
    return tuple(int(entry.adopted) if c == "adopted" else getattr(entry, c) for c in _LAUNCH_COLS)


def _launch(r: tuple[object, ...]) -> LaunchEntry:
    values = dict(zip(_LAUNCH_COLS, r, strict=True))
    values["adopted"] = bool(values["adopted"])
    return LaunchEntry(**cast(dict[str, Any], values))


def _adoption(r: tuple[object, ...]) -> Adoption:
    return Adoption(str(r[0]), str(r[1]), _opt(r[2]), str(r[3]), str(r[4]), str(r[5]), bool(r[6]),
                    _opt(r[7]), _opt(r[8]), _opt(r[9]))


def state_detail(reason: Reason | None, detail: str) -> str:
    """A state event's detail: the reason and its concrete detail, as JSON (plan 6 renders it)."""
    return msgspec.json.encode({"reason": reason.value if reason else "", "detail": detail}).decode()
_INBOX = "seq, surface, event_id, kind, ws, bead, payload, status, attempts"
_OP = "op_id, kind, ws, bead, step, data, attempts, status"


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


def _inbox(r: tuple[object, ...]) -> InboxRow:
    return InboxRow(int(cast(int, r[0])), str(r[1]), str(r[2]), str(r[3]), _opt(r[4]), _opt(r[5]), str(r[6]),
                    InboxStatus(str(r[7])), int(cast(int, r[8])))


def _op(r: tuple[object, ...]) -> Op:
    return Op(str(r[0]), OpKind(str(r[1])), str(r[2]), str(r[3]), str(r[4]), _DATA.decode(str(r[5])),
              int(cast(int, r[6])), OpStatus(str(r[7])))


def _bead(r: tuple[object, ...]) -> BeadRow:
    reason = _opt(r[3])
    return BeadRow(str(r[0]), str(r[1]), BeadState(str(r[2])), None if reason is None else Reason(reason),
                   str(r[4]), str(r[5]))


def _transient(exc: sqlite3.Error) -> bool:
    return exc.sqlite_errorcode & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


@contextlib.contextmanager
def _busy() -> Generator[None]:
    """Contention on the journal raises JournalBusy; every other SQLite error passes through as it is."""
    try:
        yield
    except sqlite3.OperationalError as exc:
        if _transient(exc):
            busy = JournalBusy(exc.sqlite_errorname)
            for note in getattr(exc, "__notes__", ()):  # a failed cleanup's note survives the conversion
                busy.add_note(note)
            raise busy from None
        raise


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Journal method: wsd calls it from the event loop and from worker threads. A poisoned
    journal refuses it once the lock is taken, so a thread queued behind the failing transaction is too."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock, _busy():  # type: ignore[attr-defined]  # args[0] is the Journal
            args[0]._usable()  # type: ignore[attr-defined]
            return method(*args, **kwargs)
    return wrapper


def _serialized[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """`_locked` without the poison check, for close()."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock, _busy():  # type: ignore[attr-defined]  # args[0] is the Journal
            return method(*args, **kwargs)
    return wrapper


def _uri(path: Path, query: str) -> str:
    return f"{path.absolute().as_uri()}?{query}"


def _connect(path: Path) -> sqlite3.Connection:
    """The working connection, opened only after `_validate` accepted the file. `mode=rw` never creates
    a file; switching to WAL is the first write. The file is checked again on this connection, because
    another connection may have changed it since `_validate` read it; a file refused here has already
    been switched to WAL. Until this check passes the connection never checkpoints on close: as the last
    connection to a WAL file it would otherwise copy a log another writer left into the main file and
    delete the log, changing a file it refuses."""
    db = sqlite3.connect(_uri(path, "mode=rw"), uri=True, isolation_level=None, timeout=BUSY_TIMEOUT,
                         check_same_thread=False)
    try:
        db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        db.execute("PRAGMA journal_mode=WAL")
        _check_snapshot(db)
        db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, False)
    except BaseException:
        db.close()
        raise
    return db


def _create(path: Path) -> None:
    """Build a complete journal under a temporary name and rename it into place, so `path` either does
    not exist or holds a whole schema: a crash can never leave a half-made journal that looks fresh."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.new")
    db = sqlite3.connect(tmp, isolation_level=None)
    try:
        db.executescript("BEGIN;" + SCHEMA + "COMMIT;")
        db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?), ('created_at', ?)",
                   (SCHEMA_VERSION, now()))
        db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", V2_META)
    finally:
        db.close()
    tmp.chmod(0o600)
    os.link(tmp, path)      # fails if `path` appeared meanwhile: never overwrite a journal
    tmp.unlink()


def _schema(db: sqlite3.Connection) -> set[tuple[str, str, str, str]]:
    """Every table and index the schema declares, with its SQL: columns, constraints and an index's
    predicate included. SQLite's own objects (`sqlite_sequence`, automatic indexes) are left out."""
    rows = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
    return {(str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows if not str(r[1]).startswith("sqlite_")}


def _expected(schema: str) -> set[tuple[str, str, str, str]]:
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(schema)
        return _schema(db)
    finally:
        db.close()


EXPECTED_SCHEMA = _expected(SCHEMA)
EXPECTED_V1 = _expected(V1_SCHEMA)


def _version(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return None if row is None else str(row[0])


def check_v1(db: sqlite3.Connection) -> bool:
    """Whether the file is plan 3's journal exactly: the frozen v1 schema and version 1."""
    return _schema(db) == EXPECTED_V1 and _version(db) == V1_VERSION


def _check(db: sqlite3.Connection) -> None:
    """Version 2 opens; version 1, exactly, needs the upgrade; anything else is refused."""
    rows = db.execute("PRAGMA integrity_check").fetchall()
    if [tuple(r) for r in rows] != [("ok",)]:
        raise JournalCorrupt("integrity_check failed")
    schema = _schema(db)
    if schema == EXPECTED_SCHEMA:
        if _version(db) != SCHEMA_VERSION:
            raise JournalCorrupt("unknown schema version")
        return
    if schema == EXPECTED_V1:
        if _version(db) != V1_VERSION:
            raise JournalCorrupt("unknown schema version")
        raise JournalNeedsUpgrade("the journal is version 1")
    raise JournalCorrupt("schema differs")


def _check_snapshot(db: sqlite3.Connection) -> None:
    """`_check` inside one read transaction: every read sees the same committed snapshot, under SQLite's
    locks, write-ahead log included."""
    db.execute("BEGIN")
    try:
        _check(db)
    finally:
        db.execute("ROLLBACK")


def _validate(path: Path) -> None:
    """Check the journal before anything writes to it, through a read-only connection that is never
    created, never switched to WAL and never checkpoints: the main file and an existing `-wal` keep
    their bytes. Reading a WAL-mode file needs its shared-memory index, so SQLite may create or update
    `-shm` and create an empty `-wal`, and a read-only connection leaves them in place; a refused
    WAL-mode file can keep those two sidecars. A rollback-journal (non-WAL) file gains no files."""
    db = sqlite3.connect(_uri(path, "mode=ro"), uri=True, isolation_level=None, timeout=BUSY_TIMEOUT)
    try:
        _check_snapshot(db)
    finally:
        db.close()


def _refusal(exc: sqlite3.DatabaseError) -> Exception:
    if _transient(exc):
        return JournalBusy(exc.sqlite_errorname)
    return JournalCorrupt(type(exc).__name__)


def backup_to(db: sqlite3.Connection, dest: Path) -> None:
    """A consistent online copy of `db` at `dest` (0600), written under a temporary name and renamed."""
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.close(fd)
    try:
        copy = sqlite3.connect(tmp)
        try:
            db.backup(copy)
        finally:
            copy.close()
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Journal:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        self.fresh = False
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            _create(path)
            self.fresh = True
        else:
            if not stat.S_ISREG(st.st_mode):
                raise JournalCorrupt("the journal path is not a regular file")
        self.lock = threading.RLock()
        self._depth = 0
        self._broken: BaseException | None = None   # a transaction's failed cleanup; see JournalUnusable
        try:
            _validate(path)
            self.db = _connect(path)
        except sqlite3.DatabaseError as exc:
            raise _refusal(exc) from None

    @_serialized
    def close(self) -> None:
        """Close the connection, discarding any transaction a failed cleanup left open. A poisoned journal
        stays poisoned."""
        self.db.close()

    def _usable(self) -> None:
        if self._broken is not None:
            raise JournalUnusable(f"the journal is unusable: a transaction's cleanup failed "
                                  f"({type(self._broken).__name__}); open a new Journal") from self._broken

    @_locked
    def backup(self, dest: Path) -> None:
        """A consistent copy of the journal at `dest` (0600), taken online with SQLite's backup API, so
        it is safe while wsd runs in WAL mode. Written under a temporary name and renamed into place.
        Refused inside a transaction: the copy would wait on the caller's own write lock forever."""
        if self._depth or self.db.in_transaction:
            raise RuntimeError("backup() inside a journal transaction")
        backup_to(self.db, dest)

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        """All the block's journal writes commit, or (on any exception that leaves the block, a simulated
        crash included) none do. Re-entrant: a nested block joins the outer one. An exception caught inside
        the block does not roll anything back (see JournalBusy). When SQLite has already rolled the
        transaction back itself (SQLITE_FULL, for one), the original error is raised as it is. When the
        cleanup itself fails, the original error still leaves the block, with a note naming the cleanup
        error, and the journal is poisoned (JournalUnusable)."""
        with self.lock:
            self._usable()
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            with _busy():
                self.db.execute("BEGIN IMMEDIATE")
                self._depth = 1
                try:
                    yield
                    self.db.execute("COMMIT")
                except BaseException as exc:
                    failed: BaseException | None = None
                    try:
                        if self.db.in_transaction:
                            self.db.execute("ROLLBACK")
                    except BaseException as cleanup:
                        self._broken = cleanup
                        if not isinstance(cleanup, Exception):
                            raise       # an interrupt wins; Python makes `exc` its context
                        failed = cleanup
                    if failed is not None:
                        exc.add_note(f"journal cleanup failed ({type(failed).__name__}: {failed}); "
                                     "the journal is unusable; open a new Journal")
                    raise               # bare: `exc` keeps its own cause, context and notes
                finally:
                    self._depth = 0

    # --- inbox (§5.4) ---

    @_locked
    def inbox_add(self, surface: str, event_id: str, kind: str, payload: str,
                  ws: str | None = None, bead: str | None = None) -> int | None:
        """Insert a `pending` event; None if (surface, event_id) was already seen (a replay)."""
        at = now()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO inbox (surface, event_id, kind, ws, bead, payload, status, received_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (surface, event_id, kind, ws, bead, payload, at, at))
        return None if cur.rowcount == 0 else cur.lastrowid

    @_locked
    def inbox_pending(self) -> list[InboxRow]:
        rows = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE status = 'pending' "  # noqa: S608
                               "ORDER BY seq").fetchall()
        return [_inbox(r) for r in rows]

    @_locked
    def inbox_get(self, seq: int) -> InboxRow | None:
        row = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
        return None if row is None else _inbox(row)

    @_locked
    def inbox_failed(self, seq: int, limit: int) -> InboxStatus:
        """Count one failed attempt; the `limit`th failure escalates the event to `needs_human`."""
        with self.transaction():
            self.db.execute("UPDATE inbox SET attempts = attempts + 1, updated_at = ? "
                            "WHERE seq = ? AND status = 'pending'", (now(), seq))
            self.db.execute("UPDATE inbox SET status = 'needs_human' "
                            "WHERE seq = ? AND status = 'pending' AND attempts >= ?", (seq, limit))
            row = self.db.execute("SELECT status FROM inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            raise KeyError(seq)
        return InboxStatus(str(row[0]))

    @_locked
    def inbox_finish(self, seq: int, status: InboxStatus) -> None:
        if status is InboxStatus.PENDING:
            raise ValueError("finish needs a final status")
        cur = self.db.execute("UPDATE inbox SET status = ?, updated_at = ? "
                              "WHERE seq = ? AND status = 'pending'", (status.value, now(), seq))
        if cur.rowcount != 1:
            raise KeyError(seq)

    # --- operation journals (§4.3) ---

    @_locked
    def op_open(self, kind: OpKind, ws: str, bead: str, data: dict[str, str] | None = None) -> Op:
        op_id = uuid.uuid4().hex
        at = now()
        try:
            self.db.execute(
                "INSERT INTO ops (op_id, kind, ws, bead, step, data, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'intent', ?, 'open', ?, ?)",
                (op_id, kind.value, ws, bead, msgspec.json.encode(data or {}).decode(), at, at))
        except sqlite3.IntegrityError:
            raise OpConflict(f"an operation is already open for {bead}") from None
        return self._op(op_id)

    def _op(self, op_id: str) -> Op:
        """Unlocked and unchecked: the caller holds `self.lock` (a `_locked` method, or inside
        `transaction()`), so the poison check has already run."""
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE op_id = ?", (op_id,)).fetchone()  # noqa: S608
        if row is None:
            raise KeyError(op_id)
        return _op(row)

    @_locked
    def op_step(self, op_id: str, step: str, data: dict[str, str] | None = None) -> Op:
        """Record that `step` completed, merging `data` (a SHA, a worktree path) into the op's data."""
        with self.transaction():
            op = self._op(op_id)
            if op.status is not OpStatus.OPEN:
                raise OpConflict(f"operation {op_id} is {op.status}")
            merged = {**op.data, **(data or {})}
            self.db.execute("UPDATE ops SET step = ?, data = ?, updated_at = ? WHERE op_id = ?",
                            (step, msgspec.json.encode(merged).decode(), now(), op_id))
            return self._op(op_id)

    @_locked
    def op_failed(self, op_id: str) -> int:
        """Count one failed attempt and return the total."""
        self.db.execute("UPDATE ops SET attempts = attempts + 1, updated_at = ? WHERE op_id = ?",
                        (now(), op_id))
        return self._op(op_id).attempts

    @_locked
    def op_finish(self, op_id: str, status: OpStatus) -> None:
        if status is OpStatus.OPEN:
            raise ValueError("finish needs a final status")
        self.db.execute("UPDATE ops SET status = ?, updated_at = ? WHERE op_id = ? AND status = 'open'",
                        (status.value, now(), op_id))

    @_locked
    def op_forget(self, op_id: str, *keys: str) -> Op:
        """Drop `keys` from the op's data (a refused or abandoned generation, so the next attempt pins a
        new one). The step is kept."""
        with self.transaction():
            op = self._op(op_id)
            if op.status is not OpStatus.OPEN:
                raise OpConflict(f"operation {op_id} is {op.status}")
            kept = {k: v for k, v in op.data.items() if k not in keys}
            self.db.execute("UPDATE ops SET data = ?, updated_at = ? WHERE op_id = ?",
                            (msgspec.json.encode(kept).decode(), now(), op_id))
            return self._op(op_id)

    @_locked
    def ops_open(self, ws: str | None = None) -> list[Op]:
        if ws is None:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' "  # noqa: S608
                                   "ORDER BY rowid")
        else:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? "  # noqa: S608
                                   "ORDER BY rowid", (ws,))
        return [_op(r) for r in rows.fetchall()]

    @_locked
    def op_for(self, ws: str, bead: str) -> Op | None:
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? AND bead = ?",  # noqa: S608
                              (ws, bead)).fetchone()
        return None if row is None else _op(row)

    # --- launch entries and receipts (AU-3 §2.2, §2.3) ---

    @_locked
    def launch_insert(self, entry: LaunchEntry, rebuilt: bool = False) -> None:
        """Journal a new entry; `rebuilt` marks one copied from the bead (§4.2 step 1). An existing entry
        is never replaced: the same one again is a no-op, any other is EntryConflict."""
        with self.transaction():
            current = self.launch(*entry.ident)
            if current is not None:
                if current != entry:
                    raise EntryConflict(f"generation {entry.generation} is already journaled differently")
                return
            self.db.execute(
                f"INSERT INTO launches ({_LAUNCH}, rebuilt_at) VALUES ({', '.join('?' * _LAUNCH_N)}, ?)",  # noqa: S608
                (*_launch_row(entry), now() if rebuilt else None))

    @_locked
    def launch_set(self, session_key: str, generation: int, name: str, value: str) -> LaunchEntry:
        """Set one set-once field while it is empty. The same value again is a no-op (replay); a different
        one is EntryConflict. Nothing else ever updates a launch row."""
        if name not in SET_ONCE:
            raise ValueError(f"{name} is not a set-once field")
        with self.transaction():
            current = self.launch(session_key, generation)
            if current is None:
                raise KeyError((session_key, generation))
            have = getattr(current, name)
            if have is None:
                self.db.execute(f"UPDATE launches SET {name} = ? WHERE session_key = ? AND generation = ? "  # noqa: S608
                                f"AND {name} IS NULL", (value, session_key, generation))
                return msgspec.structs.replace(current, **{name: value})
            if have != value:
                raise EntryConflict(f"generation {generation} already has a different {name}")
            return current

    @_locked
    def launch(self, session_key: str, generation: int) -> LaunchEntry | None:
        row = self.db.execute(f"SELECT {_LAUNCH} FROM launches WHERE session_key = ? AND generation = ?",  # noqa: S608
                              (session_key, generation)).fetchone()
        return None if row is None else _launch(row)

    @_locked
    def launches(self, session_key: str) -> list[LaunchEntry]:
        rows = self.db.execute(f"SELECT {_LAUNCH} FROM launches WHERE session_key = ? "  # noqa: S608
                               "ORDER BY generation", (session_key,)).fetchall()
        return [_launch(r) for r in rows]

    @_locked
    def launches_of(self, ws: str, bead: str) -> list[LaunchEntry]:
        rows = self.db.execute(f"SELECT {_LAUNCH} FROM launches WHERE ws = ? AND bead = ? "  # noqa: S608
                               "ORDER BY session_key, generation", (ws, bead)).fetchall()
        return [_launch(r) for r in rows]

    @_locked
    def rebuilt_at(self, session_key: str, generation: int) -> str | None:
        row = self.db.execute("SELECT rebuilt_at FROM launches WHERE session_key = ? AND generation = ?",
                              (session_key, generation)).fetchone()
        return None if row is None else _opt(row[0])

    @_locked
    def receipt_put(self, receipt: Receipt) -> None:
        """Journal what the runtime returned or raised for a dispatched generation. Written once: a second
        receipt for the generation is EntryConflict."""
        try:
            self.db.execute(
                f"INSERT INTO receipts ({_RECEIPT}, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
                (receipt.session_key, receipt.generation, receipt.kind, receipt.refusal, receipt.tmux_session,
                 receipt.tmux_pane, receipt.pane_pid, receipt.native_id, receipt.error, now()))
        except sqlite3.IntegrityError:
            raise EntryConflict(f"generation {receipt.generation} already has a receipt") from None

    @_locked
    def receipt(self, session_key: str, generation: int) -> Receipt | None:
        row = self.db.execute(f"SELECT {_RECEIPT} FROM receipts WHERE session_key = ? AND generation = ?",  # noqa: S608
                              (session_key, generation)).fetchone()
        if row is None:
            return None
        pid = row[6]
        return Receipt(str(row[0]), int(cast(int, row[1])), cast(ReceiptKind, str(row[2])),
                       cast(Refusal | None, _opt(row[3])), _opt(row[4]), _opt(row[5]),
                       None if pid is None else int(cast(int, pid)), _opt(row[7]), _opt(row[8]))

    @_locked
    def receipts(self) -> int:
        return int(cast(int, self.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]))

    # --- adoptions (AU-3 §2.5, §3.2, §3.3) ---

    @_locked
    def adoption(self, ws: str, bead: str) -> Adoption | None:
        row = self.db.execute(f"SELECT {_ADOPTION} FROM adoptions WHERE ws = ? AND bead = ?",  # noqa: S608
                              (ws, bead)).fetchone()
        return None if row is None else _adoption(row)

    @_locked
    def adoptions_unsettled(self, ws: str) -> list[Adoption]:
        rows = self.db.execute(f"SELECT {_ADOPTION} FROM adoptions WHERE ws = ? AND settled = 0 "  # noqa: S608
                               "ORDER BY bead", (ws,)).fetchall()
        return [_adoption(r) for r in rows]

    @_locked
    def adoption_settle(self, ws: str, bead: str) -> None:
        self.db.execute("UPDATE adoptions SET settled = 1 WHERE ws = ? AND bead = ?", (ws, bead))

    @_locked
    def adoption_resolve(self, ws: str, bead: str, by: str) -> None:
        """The release that re-verified a held adoption (§3.3). Set once; the original evidence is kept."""
        cur = self.db.execute("UPDATE adoptions SET resolution = 'adopted', resolved_at = ?, resolved_by = ? "
                              "WHERE ws = ? AND bead = ? AND verdict = 'held' AND resolution IS NULL",
                              (now(), by, ws, bead))
        if cur.rowcount != 1:
            raise EntryConflict(f"the adoption of {bead} is not an unresolved hold")

    # --- states, holds, events (plan 6 reads these) ---

    @_locked
    def set_state(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
                  detail: str = "", ref: str | None = None) -> None:
        """Move a bead to `state` (a legal transition only) and append the matching progress event."""
        with self.transaction():
            current = self.state(ws, bead)
            check(None if current is None else current.state, state)
            self._put(ws, bead, current, state, reason, detail, ref)

    @_locked
    def adopt(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
              detail: str = "", ref: str | None = None) -> None:
        """The rebuild from beads and the runtime (§3.3, and each pickup's observation): beads are the
        truth, so the cached row is replaced without a transition check. It still emits the event."""
        with self.transaction():
            self._put(ws, bead, self.state(ws, bead), state, reason, detail, ref)

    def _put(self, ws: str, bead: str, current: BeadRow | None, state: BeadState, reason: Reason | None,
             detail: str, ref: str | None) -> None:
        """Unlocked and unchecked: the caller holds `self.lock` (a `_locked` method, or inside
        `transaction()`), so the poison check has already run."""
        if current is not None and (current.state, current.reason, current.detail) == (state, reason, detail):
            return
        self.db.execute(
            "INSERT INTO beads (ws, bead, state, reason, detail, since) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ws, bead) DO UPDATE SET state = excluded.state, reason = excluded.reason, "
            "detail = excluded.detail, since = excluded.since",
            (ws, bead, state.value, None if reason is None else reason.value, detail, now()))
        self.emit(ws, bead, f"state:{state.value}", state_detail(reason, detail), ref)

    @_locked
    def state(self, ws: str, bead: str) -> BeadRow | None:
        row = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads "
                              "WHERE ws = ? AND bead = ?", (ws, bead)).fetchone()
        return None if row is None else _bead(row)

    @_locked
    def states(self, ws: str) -> list[BeadRow]:
        rows = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads WHERE ws = ? "
                               "ORDER BY bead", (ws,)).fetchall()
        return [_bead(r) for r in rows]

    @_locked
    def hold(self, ws: str, reason: Reason, detail: str = "") -> None:
        with self.transaction():
            if self.holds(ws).get(reason) == detail:
                return
            self.db.execute("INSERT INTO holds (ws, reason, detail, since) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT (ws, reason) DO UPDATE SET detail = excluded.detail",
                            (ws, reason.value, detail, now()))
            self.emit(ws, None, "hold", state_detail(reason, detail))

    @_locked
    def unhold(self, ws: str, reason: Reason) -> None:
        with self.transaction():
            cur = self.db.execute("DELETE FROM holds WHERE ws = ? AND reason = ?", (ws, reason.value))
            if cur.rowcount:
                self.emit(ws, None, "unhold", reason.value)

    @_locked
    def holds(self, ws: str) -> dict[Reason, str]:
        rows = self.db.execute("SELECT reason, detail FROM holds WHERE ws = ? ORDER BY reason",
                               (ws,)).fetchall()
        return {Reason(str(r[0])): str(r[1]) for r in rows}

    @_locked
    def set_ws_state(self, ws: str, state: WsState) -> None:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            if row is not None and str(row[0]) == state.value:
                return
            self.db.execute("INSERT INTO workstreams (ws, state, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT (ws) DO UPDATE SET state = excluded.state, "
                            "updated_at = excluded.updated_at",
                            (ws, state.value, now()))
            self.emit(ws, None, f"ws:{state.value}")

    @_locked
    def emit(self, ws: str, bead: str | None, kind: str, detail: str = "", ref: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), ws, bead, kind, detail, ref))
        return int(cast(int, cur.lastrowid))

    @_locked
    def inbox_pending_count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM inbox WHERE status = 'pending'").fetchone()
        return int(cast(int, row[0]))

    @_locked
    def events_since(self, seq: int, limit: int = 500) -> list[Event]:
        rows = self.db.execute("SELECT seq, at, ws, bead, kind, detail, ref FROM events WHERE seq > ? "
                               "ORDER BY seq LIMIT ?", (seq, limit)).fetchall()
        return [Event(int(cast(int, r[0])), str(r[1]), str(r[2]), _opt(r[3]), str(r[4]), str(r[5]),
                      _opt(r[6])) for r in rows]

    @_locked
    def snapshot(self, ws: str) -> Snapshot:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            last = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            state = None if row is None else WsState(str(row[0]))
            return Snapshot(ws, state, self.holds(ws), self.states(ws), self.ops_open(ws),
                            int(cast(int, last[0])))
