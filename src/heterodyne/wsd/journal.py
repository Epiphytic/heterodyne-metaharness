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
import dataclasses
import functools
import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

import msgspec

from heterodyne.fsutil import private_dir
from heterodyne.wsd.headroom import Mark, UsageCache, Window, epoch
from heterodyne.wsd.launches import LAUNCHED, SET_ONCE, LaunchEntry, Receipt
from heterodyne.wsd.states import BeadState, Reason, WsState, check

SCHEMA_VERSION = "5"
V4_VERSION = "4"
V3_VERSION = "3"
V2_VERSION = "2"
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
V2_SCHEMA = V1_SCHEMA + "".join(f"{stmt};\n" for stmt in V2_TABLES)
# Version 3 (plan 4c T7): each launch entry's install pin. SQLite records an added column in the table's
# own CREATE text, so a new journal runs these too, and its schema is exactly an upgraded one's.
V3_COLUMNS = (
    "ALTER TABLE launches ADD COLUMN install TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE launches ADD COLUMN install_digest TEXT NOT NULL DEFAULT ''",
)
V3_SCHEMA = V2_SCHEMA + "".join(f"{stmt};\n" for stmt in V3_COLUMNS)
# Version 4 (plan 4c T10): the managed installs `wsd` stages and their approvals, and the harness ops' log.
HARNESS_STATES = ("staged", "posted", "approved", "published", "denied", "expired", "failed")
V4_TABLES = (
    f"""CREATE TABLE harness_installs (staging_id TEXT PRIMARY KEY, adapter TEXT NOT NULL,
    digest TEXT NOT NULL, version TEXT NOT NULL, approval_bead TEXT NOT NULL, context_digest TEXT NOT NULL,
    ask_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ({", ".join(f"'{s}'" for s in HARNESS_STATES)})),
    install TEXT NOT NULL, failure TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE harness_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, adapter TEXT NOT NULL, install TEXT NOT NULL, detail TEXT NOT NULL)""",
)
V4_SCHEMA = V3_SCHEMA + "".join(f"{stmt};\n" for stmt in V4_TABLES)
# Version 5 (plan 5i part 2): the ask bridge's asks, notices, resume notes, question rounds, denied asks and
# refused requests. `next_at` is epoch seconds; an answer is held locally, at most ANSWER_MAX characters.
ASK_STATES = ("creating", "owed", "posted", "answered", "approved", "denied", "unverified", "ended",
              "refused", "delivered", "released")
NOTICE_STATES = ("owed", "sending", "posted", "uncertain", "refused", "withheld")
NOTICE_KINDS = ("stuck", "waiting", "closed", "review")
ANSWER_MAX = 16000
STEER_ATTEMPTS = 3          # a note steered this often is left to the next resume brief


def _among(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


V5_TABLES = (
    f"""CREATE TABLE asks (ref TEXT PRIMARY KEY, ws TEXT NOT NULL, bead TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('question', 'approval')), child TEXT, ask_id TEXT, context_digest TEXT,
    state TEXT NOT NULL CHECK (state IN ({_among(ASK_STATES)})), round INTEGER NOT NULL DEFAULT 0,
    answer TEXT CHECK (length(answer) <= {ANSWER_MAX}), choice TEXT, why TEXT,
    uncertain INTEGER NOT NULL DEFAULT 0 CHECK (uncertain IN (0, 1)), attempts INTEGER NOT NULL DEFAULT 0,
    next_at INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    f"""CREATE TABLE notices (ref TEXT PRIMARY KEY, ws TEXT NOT NULL, bead TEXT,
    kind TEXT NOT NULL CHECK (kind IN ({_among(NOTICE_KINDS)})), title TEXT NOT NULL, body TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ({_among(NOTICE_STATES)})), notice_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER, why TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL)""",
    """CREATE TABLE resume_notes (seq INTEGER PRIMARY KEY AUTOINCREMENT, ws TEXT NOT NULL, bead TEXT NOT NULL,
    ref TEXT NOT NULL UNIQUE, text TEXT NOT NULL, delivered_at TEXT,
    steer_attempts INTEGER NOT NULL DEFAULT 0 CHECK (steer_attempts >= 0))""",
    """CREATE TABLE nudges (ws TEXT NOT NULL, bead TEXT NOT NULL, rounds INTEGER NOT NULL DEFAULT 0,
    last_head TEXT, PRIMARY KEY (ws, bead))""",
    """CREATE TABLE denied_asks (session_key TEXT NOT NULL, effect_digest TEXT NOT NULL,
    PRIMARY KEY (session_key, effect_digest))""",
    """CREATE TABLE refusals (event_id TEXT PRIMARY KEY, ws TEXT NOT NULL, bead TEXT NOT NULL,
    session_key TEXT NOT NULL, generation INTEGER NOT NULL, turn INTEGER NOT NULL, text TEXT NOT NULL,
    created_at TEXT NOT NULL)""",
)
SCHEMA = V4_SCHEMA + "".join(f"{stmt};\n" for stmt in V5_TABLES)
BUSY_TIMEOUT = 5.0  # seconds a connection waits on another's lock before SQLITE_BUSY


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JournalCorrupt(Exception):
    """The journal failed its integrity check. wsd refuses to start; the file is left for the operator."""


class JournalNeedsUpgrade(Exception):
    """The journal is an earlier version exactly: plan 3's (schema and version 1), for `upgrade.run`; AU-3's
    (version 2), for `upgrade.run_2_to_3`; plan 4c T7's (3) for `run_3_to_4`; or T10's (4) for `run_4_to_5`.
    Nothing was written; each upgrades in one transaction."""

    def __init__(self, version: str) -> None:
        super().__init__(f"the journal is version {version}")
        self.version = version


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
class DeferralRow:
    """A deferral record (D5; AU-4 writes them, AU-5 reads them for the wake time). `defer_until` is the
    stored text: decimal epoch seconds, or None."""
    session_key: str
    number: int
    bead: str
    role: str
    profile: str
    reason: str
    defer_until: str | None
    trust: str


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


@dataclass(frozen=True)
class HarnessInstall:
    """One staging and its approval (plan 4c T10): `state` is one of HARNESS_STATES; `install` is set once
    published, and `failure` holds the constant a `failed` row ended with."""
    staging_id: str
    adapter: str
    digest: str
    version: str
    approval_bead: str
    context_digest: str
    ask_id: str
    state: str
    install: str
    failure: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RefusedRequest:
    """A refused request's fixed text and attribution (plan 5i part 2, part 1's D1). Its delivery is its
    note's, `refused:<event_id>`."""
    ws: str
    bead: str
    session_key: str
    generation: int
    turn: int
    text: str


@dataclass(frozen=True)
class HarnessEvent:
    id: int
    at: str
    actor: str              # "wsctl" for an op a request ran, "wsd" for what wsd did on its own
    action: str
    adapter: str
    install: str
    detail: str


_HARNESS_COLS = ", ".join(f.name for f in dataclasses.fields(HarnessInstall))
_HARNESS_SET = frozenset({"approval_bead", "context_digest", "ask_id", "state", "install", "failure"})
HARNESS_OPEN = "harness_open:"      # a `use` op on an install, open until it ends: `<adapter>:<install>`
HARNESS_TAINTS = "harness_taints:"  # how many times an install's taint was cleared: `<adapter>:<install>`
_DATA = msgspec.json.Decoder(dict[str, str])
_LAUNCH_COLS = ("session_key", "generation", "ws", "bead", "role", "profile", "account", "credential_key",
                "model_passed", "dispatched_at", "native_id", "model_reported", "outcome", "adopted",
                "journaled_at", "install", "install_digest")
LAUNCH_SQL = ", ".join(_LAUNCH_COLS)
LAUNCH_N = len(_LAUNCH_COLS)
_RECEIPT = "session_key, generation, kind, refusal, tmux_session, tmux_pane, pane_pid, native_id, error"
_ADOPTION = "ws, bead, session_key, verdict, detail, facts, settled, resolution, resolved_at, resolved_by"
type ReceiptKind = Literal["started", "refused"]
type Refusal = Literal["failed", "unavailable"]


def launch_row(entry: LaunchEntry) -> tuple[object, ...]:
    return tuple(int(entry.adopted) if c == "adopted" else getattr(entry, c) for c in _LAUNCH_COLS)


def _launch(r: tuple[object, ...]) -> LaunchEntry:
    values = dict(zip(_LAUNCH_COLS, r, strict=True))
    values["adopted"] = bool(values["adopted"])
    return LaunchEntry(**cast(dict[str, Any], values))


def _adoption(r: tuple[object, ...]) -> Adoption:
    return Adoption(str(r[0]), str(r[1]), _opt(r[2]), str(r[3]), str(r[4]), str(r[5]), bool(r[6]),
                    _opt(r[7]), _opt(r[8]), _opt(r[9]))


_WINDOW = "window_id, kind, used_percent, resets_at, observed_at, receipt_seq, source"
_DEFERRAL = "d.session_key, d.number, d.bead, d.role, d.profile, d.reason, d.defer_until, d.trust"
# Each session's highest-numbered deferral record: the current one.
_CURRENT_DEFERRALS = (f"SELECT {_DEFERRAL} FROM deferrals d WHERE d.number = "  # noqa: S608
                      "(SELECT MAX(number) FROM deferrals e WHERE e.session_key = d.session_key)")
CLAMP_EXHAUSTED = "clamp:exhausted:"
CLAMP_DEFERRAL = "clamp:deferral:"
DEFERRAL_OVER = "deferral_over:"     # AU-4 §2.1: a re-gate marked an `account_changed` wait over
# g08sd: how each launched generation ended, once (`crash_gen:<ws>:<bead>:<key>:<generation>`), and each
# bead's deaths since its last reset (`crash_run:<ws>:<bead>`). Retention is plan 8's, with the journal's.
CRASH_GEN = "crash_gen:"
CRASH_RUN = "crash_run:"
CRASH_CLASSES = frozenset({"died", "stopped", "lifetime", "legacy", "reset"})
# wpyv5: the end a resolve carried from a generation's record, once (`crash_end:<ws>:<bead>:<key>:<gen>`),
# for the guard to account it from. Never changed or removed by accounting; retention is plan 8's.
CRASH_END = "crash_end:"


class ResolvedEnd(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A generation's end as its resolve carried it (the runtime's `Ended`, without its types)."""
    cause: str
    reached_running: bool
    ran_seconds: int | None


def _crash_gen(entry: LaunchEntry) -> str:
    return f"{CRASH_GEN}{entry.ws}:{entry.bead}:{entry.session_key}:{entry.generation}"


def _window(r: tuple[object, ...]) -> Window | None:
    """A stored usage row, or None if a value it needs doesn't parse (unknown, which never blocks)."""
    observed, seq = epoch(r[4]), epoch(r[5])
    if observed is None or seq is None or not isinstance(r[2], int | float):
        return None
    return Window(str(r[0]), str(r[1]), float(r[2]), epoch(r[3]), observed, seq, str(r[6]))


def _window_row(w: Window, seq: int) -> tuple[object, ...]:
    return (w.window_id, w.kind, w.used_percent, None if w.resets_at is None else str(int(w.resets_at)),
            str(int(w.observed_at)), seq, w.source)


def _deferral(r: tuple[object, ...]) -> DeferralRow:
    return DeferralRow(str(r[0]), int(cast(int, r[1])), str(r[2]), str(r[3]), str(r[4]), str(r[5]),
                       _opt(r[6]), str(r[7]))


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
        db.execute("PRAGMA foreign_keys = ON")      # a receipt needs its launch entry
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
EXPECTED_V4 = _expected(V4_SCHEMA)
EXPECTED_V3 = _expected(V3_SCHEMA)
EXPECTED_V2 = _expected(V2_SCHEMA)
EXPECTED_V1 = _expected(V1_SCHEMA)


def _version(db: sqlite3.Connection) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return None if row is None else str(row[0])


def check_v1(db: sqlite3.Connection) -> bool:
    """Whether the file is plan 3's journal exactly: the frozen v1 schema and version 1."""
    return _schema(db) == EXPECTED_V1 and _version(db) == V1_VERSION


def check_v2(db: sqlite3.Connection) -> bool:
    """Whether the file is AU-3's journal exactly: the version 2 schema and version 2."""
    return _schema(db) == EXPECTED_V2 and _version(db) == V2_VERSION


def check_v3(db: sqlite3.Connection) -> bool:
    """Whether the file is plan 4c T7's journal exactly: the version 3 schema and version 3."""
    return _schema(db) == EXPECTED_V3 and _version(db) == V3_VERSION


def check_v4(db: sqlite3.Connection) -> bool:
    """Whether the file is plan 4c T10's journal exactly: the version 4 schema and version 4."""
    return _schema(db) == EXPECTED_V4 and _version(db) == V4_VERSION


def check_current(db: sqlite3.Connection) -> bool:
    """Whether the file is this version's journal exactly: the current schema and version."""
    return _schema(db) == EXPECTED_SCHEMA and _version(db) == SCHEMA_VERSION


def _check(db: sqlite3.Connection) -> None:
    """Version 5 opens; version 1, 2, 3 or 4, exactly, needs its upgrade; anything else is refused."""
    rows = db.execute("PRAGMA integrity_check").fetchall()
    if [tuple(r) for r in rows] != [("ok",)]:
        raise JournalCorrupt("integrity_check failed")
    schema = _schema(db)
    if schema == EXPECTED_SCHEMA:
        if _version(db) != SCHEMA_VERSION:
            raise JournalCorrupt("unknown schema version")
        return
    older = ((EXPECTED_V4, V4_VERSION), (EXPECTED_V3, V3_VERSION), (EXPECTED_V2, V2_VERSION),
             (EXPECTED_V1, V1_VERSION))
    for expected, version in older:
        if schema == expected:
            if _version(db) != version:
                raise JournalCorrupt("unknown schema version")
            raise JournalNeedsUpgrade(version)
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
                f"INSERT INTO launches ({LAUNCH_SQL}, rebuilt_at) VALUES ({', '.join('?' * LAUNCH_N)}, ?)",  # noqa: S608
                (*launch_row(entry), now() if rebuilt else None))

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
        row = self.db.execute(f"SELECT {LAUNCH_SQL} FROM launches WHERE session_key = ? AND generation = ?",  # noqa: S608
                              (session_key, generation)).fetchone()
        return None if row is None else _launch(row)

    @_locked
    def launches(self, session_key: str) -> list[LaunchEntry]:
        rows = self.db.execute(f"SELECT {LAUNCH_SQL} FROM launches WHERE session_key = ? "  # noqa: S608
                               "ORDER BY generation", (session_key,)).fetchall()
        return [_launch(r) for r in rows]

    @_locked
    def launches_of(self, ws: str, bead: str) -> list[LaunchEntry]:
        rows = self.db.execute(f"SELECT {LAUNCH_SQL} FROM launches WHERE ws = ? AND bead = ? "  # noqa: S608
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

    # --- usage (AU-5 §2.5) ---

    @_locked
    def usage_seq_next(self) -> int:
        """The next receipt sequence. Call it inside the transaction that writes the row it orders."""
        self.db.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'usage_receipt_seq'")
        row = self.db.execute("SELECT value FROM meta WHERE key = 'usage_receipt_seq'").fetchone()
        return int(str(row[0]))

    @_locked
    def usage_put_trusted(self, key: str, window: Window, seq: int) -> None:
        """A trusted window; within (key, window_id) the higher receipt sequence wins."""
        self.db.execute(
            f"INSERT INTO account_usage (credential_key, {_WINDOW}) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "  # noqa: S608
            "ON CONFLICT (credential_key, window_id) DO UPDATE SET kind = excluded.kind, "
            "used_percent = excluded.used_percent, resets_at = excluded.resets_at, "
            "observed_at = excluded.observed_at, receipt_seq = excluded.receipt_seq, "
            "source = excluded.source WHERE excluded.receipt_seq > account_usage.receipt_seq",
            (key, *_window_row(window, seq)))

    @_locked
    def usage_put_untrusted(self, session_key: str, generation: int, window: Window, seq: int) -> None:
        """A launch's own reported window; within (session, generation, window_id) the higher receipt
        sequence wins."""
        self.db.execute(
            f"INSERT INTO account_usage_untrusted (session_key, generation, {_WINDOW}) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (session_key, generation, window_id) DO UPDATE SET kind = excluded.kind, "
            "used_percent = excluded.used_percent, resets_at = excluded.resets_at, "
            "observed_at = excluded.observed_at, receipt_seq = excluded.receipt_seq, "
            "source = excluded.source WHERE excluded.receipt_seq > account_usage_untrusted.receipt_seq",
            (session_key, generation, *_window_row(window, seq)))

    @_locked
    def exhausted_put(self, key: str, mark: Mark, seq: int) -> None:
        """A trusted exhaustion mark; the higher receipt sequence wins. The replaced mark's clamp marker
        goes in the same transaction."""
        with self.transaction():
            old = self.db.execute("SELECT receipt_seq FROM account_exhausted WHERE credential_key = ?",
                                  (key,)).fetchone()
            cur = self.db.execute(
                "INSERT INTO account_exhausted (credential_key, until, observed_at, receipt_seq) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (credential_key) DO UPDATE SET until = excluded.until, "
                "observed_at = excluded.observed_at, receipt_seq = excluded.receipt_seq "
                "WHERE excluded.receipt_seq > account_exhausted.receipt_seq",
                (key, str(int(mark.until)), str(int(mark.observed_at)), seq))
            if old is not None and cur.rowcount:
                self.db.execute("DELETE FROM meta WHERE key = ?", (f"{CLAMP_EXHAUSTED}{key}:{old[0]}",))

    @_locked
    def usage_cache(self, keys: Iterable[str]) -> UsageCache:
        """The trusted windows and marks of exactly `keys`: rows of any other key are never read (D1)."""
        wanted = sorted(set(keys))
        marks = ", ".join("?" * len(wanted))
        windows: dict[str, list[Window]] = {}
        found: dict[str, Mark] = {}
        with self.transaction():
            for r in self.db.execute(f"SELECT credential_key, {_WINDOW} FROM account_usage "  # noqa: S608
                                     f"WHERE credential_key IN ({marks}) ORDER BY credential_key, window_id",
                                     wanted).fetchall():
                w = _window(r[1:])
                if w is not None:
                    windows.setdefault(str(r[0]), []).append(w)
            for r in self.db.execute("SELECT credential_key, until, observed_at, receipt_seq "  # noqa: S608
                                     f"FROM account_exhausted WHERE credential_key IN ({marks})",
                                     wanted).fetchall():
                until, observed = epoch(r[1]), epoch(r[2])
                if until is not None and observed is not None:
                    found[str(r[0])] = Mark(until, observed, int(cast(int, r[3])))
        return UsageCache({k: tuple(v) for k, v in windows.items()}, found)

    @_locked
    def usage_untrusted(self, session_key: str, generation: int) -> tuple[Window, ...]:
        """One launch's own reported rows."""
        rows = self.db.execute(f"SELECT {_WINDOW} FROM account_usage_untrusted "  # noqa: S608
                               "WHERE session_key = ? AND generation = ? ORDER BY window_id",
                               (session_key, generation)).fetchall()
        return tuple(w for w in map(_window, rows) if w is not None)

    @_locked
    def deferrals_current(self, ws: str, role: str) -> list[DeferralRow]:
        """Each session's current deferral record for `role`, for beads the journal has in `ws`."""
        rows = self.db.execute(f"{_CURRENT_DEFERRALS} AND d.role = ? AND d.bead IN "  # noqa: S608
                               "(SELECT bead FROM beads WHERE ws = ?) ORDER BY d.bead, d.session_key",
                               (role, ws)).fetchall()
        return [_deferral(r) for r in rows]

    # --- deferrals (AU-4 §2.1) ---

    @_locked
    def deferral_insert(self, row: DeferralRow) -> None:
        """A new deferral record. Call it inside the transaction that journals its defer intent."""
        self.db.execute(f"INSERT INTO deferrals ({_DEFERRAL.replace('d.', '')}) "  # noqa: S608
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (row.session_key, row.number, row.bead, row.role, row.profile, row.reason,
                         row.defer_until, row.trust))

    @_locked
    def deferral_current(self, session_key: str) -> DeferralRow | None:
        """The session's highest-numbered record: lower numbers are superseded and never due."""
        row = self.db.execute(f"SELECT {_DEFERRAL} FROM deferrals d WHERE d.session_key = ? "  # noqa: S608
                              "ORDER BY d.number DESC LIMIT 1", (session_key,)).fetchone()
        return None if row is None else _deferral(row)

    @_locked
    def deferral_next(self, session_key: str) -> int:
        row = self.db.execute("SELECT MAX(number) FROM deferrals WHERE session_key = ?",
                              (session_key,)).fetchone()
        return 1 if row[0] is None else int(cast(int, row[0])) + 1

    @_locked
    def mark_over(self, session_key: str, number: int) -> bool:
        """The re-gate found an account for this `account_changed` wait (§3.4): True if newly marked. The
        value is the epoch it was marked; the mark is dropped when its deferral stops being current."""
        marked = str(int(datetime.now(UTC).timestamp()))
        cur = self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                              (f"{DEFERRAL_OVER}{session_key}:{number}", marked))
        return bool(cur.rowcount)

    @_locked
    def is_over(self, session_key: str, number: int) -> bool:
        return self.db.execute("SELECT 1 FROM meta WHERE key = ?",
                               (f"{DEFERRAL_OVER}{session_key}:{number}",)).fetchone() is not None

    @_locked
    def drop_over(self, session_key: str, number: int) -> None:
        self.db.execute("DELETE FROM meta WHERE key = ?", (f"{DEFERRAL_OVER}{session_key}:{number}",))

    @_locked
    def crash_class(self, entry: LaunchEntry) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (_crash_gen(entry),)).fetchone()
        return None if row is None else str(row[0])

    def crash_accounted(self, entry: LaunchEntry) -> bool:
        return self.crash_class(entry) is not None

    @_locked
    def crash_count(self, ws: str, bead: str) -> int:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (f"{CRASH_RUN}{ws}:{bead}",)).fetchone()
        return 0 if row is None else int(str(row[0]))

    @_locked
    def crash_account(self, ws: str, bead: str, entry: LaunchEntry, klass: str) -> int:
        """g08sd: account `entry` as `klass`, once; the bead's count moves only when the row is new (a death
        adds one, a live lifetime end zeroes it, anything else leaves it). Returns the count."""
        if klass not in CRASH_CLASSES:
            raise ValueError(f"unknown crash class {klass!r}")
        with self.transaction():
            n = self.crash_count(ws, bead)
            cur = self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                                  (_crash_gen(entry), klass))
            if cur.rowcount and klass in ("died", "lifetime"):
                n = n + 1 if klass == "died" else 0
                self._crash_set(ws, bead, n)
        return n

    @_locked
    def crash_reset(self, ws: str, bead: str) -> None:
        """An operator's reset (a release, a new claim): the count goes to 0 and every launched generation
        not yet accounted is accounted `reset`, never counted. The rows are kept."""
        with self.transaction():
            for entry in self.launches_of(ws, bead):
                if entry.outcome == LAUNCHED:
                    self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, 'reset')",
                                    (_crash_gen(entry),))
            self._crash_set(ws, bead, 0)

    def _crash_set(self, ws: str, bead: str, n: int) -> None:
        """Unlocked: inside the callers' transactions."""
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET "
                        "value = excluded.value", (f"{CRASH_RUN}{ws}:{bead}", str(n)))

    @_locked
    def clamp_deadlines(self, bound: int) -> int:
        """§3.5, in one transaction: every unmarked exhaustion `until` and current `defer_until` later than
        `bound` is rewritten to `bound` and marked, so it is never moved again. A value is marked while its
        marker (keyed by the value's own identity) exists and equals it. Markers of values that no longer
        exist are pruned. Returns how many values changed."""
        changed = 0
        with self.transaction():
            markers = {str(k): str(v) for k, v in self.db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'clamp:%'").fetchall()}
            live: set[str] = set()
            for key, until, seq in self.db.execute(
                    "SELECT credential_key, until, receipt_seq FROM account_exhausted").fetchall():
                marker = f"{CLAMP_EXHAUSTED}{key}:{seq}"
                live.add(marker)
                if self._clamp(marker, _opt(until), bound, markers):
                    self.db.execute("UPDATE account_exhausted SET until = ? WHERE credential_key = ?",
                                    (str(bound), key))
                    changed += 1
            for d in map(_deferral, self.db.execute(_CURRENT_DEFERRALS).fetchall()):
                marker = f"{CLAMP_DEFERRAL}{d.session_key}:{d.number}"
                live.add(marker)
                if self._clamp(marker, d.defer_until, bound, markers):
                    self.db.execute("UPDATE deferrals SET defer_until = ? WHERE session_key = ? "
                                    "AND number = ?", (str(bound), d.session_key, d.number))
                    changed += 1
            for marker in set(markers) - live:
                self.db.execute("DELETE FROM meta WHERE key = ?", (marker,))
        return changed

    def _clamp(self, marker: str, value: str | None, bound: int, markers: dict[str, str]) -> bool:
        """Whether to rewrite `value`; if so, its marker is written. Unlocked: inside `clamp_deadlines`."""
        at = epoch(value)
        if at is None or at <= bound or markers.get(marker) == value:
            return False
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET "
                        "value = excluded.value", (marker, str(bound)))
        return True

    @_locked
    def emit(self, ws: str, bead: str | None, kind: str, detail: str = "", ref: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), ws, bead, kind, detail, ref))
        return int(cast(int, cur.lastrowid))

    @_locked
    def emit_once(self, ws: str, bead: str | None, kind: str, detail: str, ref: str) -> bool:
        """`emit`, unless an event of this kind with this ref is already there: one transaction, so a
        replayed step (an unlanded resolve's, btq-xb56t) is journaled once. True if it wrote one."""
        with self.transaction():
            if self.db.execute("SELECT 1 FROM events WHERE kind = ? AND ref = ?", (kind, ref)).fetchone():
                return False
            self.db.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) VALUES (?, ?, ?, ?, ?, ?)",
                            (now(), ws, bead, kind, detail, ref))
            return True

    @_locked
    def emit_resolved(self, ws: str, bead: str, detail: str, ref: str, key: str, generation: int,
                      end: ResolvedEnd | None) -> bool:
        """wpyv5: `emit_once` of the `unlanded_resolved` event, and in the same transaction the end it
        carries, if any, written once (an event journaled before wpyv5 gets its end on a repeat). True if
        it wrote the event."""
        with self.transaction():
            wrote = self.emit_once(ws, bead, "unlanded_resolved", detail, ref)
            if end is not None:
                row = f"{CRASH_END}{ws}:{bead}:{key}:{generation}"
                self.db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                                (row, msgspec.json.encode(end).decode()))
            return wrote

    @_locked
    def resolved_end(self, entry: LaunchEntry) -> ResolvedEnd | None:
        """The end a resolve carried for exactly this entry's generation; a row that doesn't decode is no
        evidence."""
        row = self.db.execute("SELECT value FROM meta WHERE key = ?",
                              (f"{CRASH_END}{entry.ws}:{entry.bead}:{entry.session_key}:{entry.generation}",)
                              ).fetchone()
        if row is None:
            return None
        try:
            return msgspec.json.decode(str(row[0]), type=ResolvedEnd)
        except msgspec.MsgspecError:
            return None

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

    # --- the managed installs (plan 4c T10) ---

    @_locked
    def harness_insert(self, row: HarnessInstall) -> None:
        self.db.execute(f"INSERT INTO harness_installs ({_HARNESS_COLS}) "  # noqa: S608
                        f"VALUES ({', '.join('?' * len(dataclasses.fields(HarnessInstall)))})",
                        dataclasses.astuple(row))

    @_locked
    def harness_set(self, staging_id: str, at: str, **fields: str) -> HarnessInstall:
        """Set `fields` (of `_HARNESS_SET`) and `updated_at` on the row, which must exist."""
        if not fields or not set(fields) <= _HARNESS_SET:
            raise ValueError("harness_set: no such field")
        names = ", ".join(f"{k} = ?" for k in fields)
        cur = self.db.execute(f"UPDATE harness_installs SET {names}, updated_at = ? "  # noqa: S608
                              "WHERE staging_id = ?", (*fields.values(), at, staging_id))
        if cur.rowcount != 1:
            raise KeyError("harness_set: no such staging")
        row = self.harness_install(staging_id)
        if row is None:
            raise KeyError("harness_set: no such staging")
        return row

    @_locked
    def harness_install(self, staging_id: str) -> HarnessInstall | None:
        row = self.db.execute(f"SELECT {_HARNESS_COLS} FROM harness_installs "  # noqa: S608
                              "WHERE staging_id = ?", (staging_id,)).fetchone()
        return None if row is None else HarnessInstall(*(str(v) for v in row))

    @_locked
    def harness_installs(self, *states: str) -> list[HarnessInstall]:
        """The rows in `states` (every row if none), oldest first."""
        where = f" WHERE state IN ({', '.join('?' * len(states))})" if states else ""
        rows = self.db.execute(f"SELECT {_HARNESS_COLS} FROM harness_installs{where} "  # noqa: S608
                               "ORDER BY created_at, staging_id", states).fetchall()
        return [HarnessInstall(*(str(v) for v in r)) for r in rows]

    @_locked
    def harness_event(self, at: str, actor: str, action: str, adapter: str, install: str,
                      detail: str = "") -> None:
        self.db.execute("INSERT INTO harness_events (at, actor, action, adapter, install, detail) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (at, actor, action, adapter, install, detail))

    @_locked
    def harness_events(self) -> list[HarnessEvent]:
        rows = self.db.execute("SELECT id, at, actor, action, adapter, install, detail FROM harness_events "
                               "ORDER BY id").fetchall()
        return [HarnessEvent(int(cast(int, r[0])), *(str(v) for v in r[1:])) for r in rows]

    @_locked
    def harness_open(self, adapter: str, install: str, at: str) -> None:
        """Journal a `use` op on the install as open (E16: in use until it ends, or until start-up's
        `harness_close_all` after a crash)."""
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET "
                        "value = excluded.value", (f"{HARNESS_OPEN}{adapter}:{install}", at))

    @_locked
    def harness_close(self, adapter: str, install: str) -> None:
        self.db.execute("DELETE FROM meta WHERE key = ?", (f"{HARNESS_OPEN}{adapter}:{install}",))

    @_locked
    def harness_taints(self, adapter: str, install: str) -> int:
        """How many times the install's taint was cleared (by `verify` or a removal): its taint alert's
        sequence, so a taint after a clear alerts again."""
        row = self.db.execute("SELECT value FROM meta WHERE key = ?",
                              (f"{HARNESS_TAINTS}{adapter}:{install}",)).fetchone()
        return 0 if row is None else int(row[0])

    @_locked
    def harness_taint_cleared(self, adapter: str, install: str) -> None:
        self.db.execute("INSERT INTO meta (key, value) VALUES (?, '1') ON CONFLICT (key) DO UPDATE SET "
                        "value = CAST(value AS INTEGER) + 1", (f"{HARNESS_TAINTS}{adapter}:{install}",))

    @_locked
    def harness_close_all(self) -> int:
        cur = self.db.execute("DELETE FROM meta WHERE key GLOB ?", (f"{HARNESS_OPEN}*",))
        return cur.rowcount

    @_locked
    def harness_opened(self) -> set[tuple[str, str]]:
        """The (adapter, install) of every open `use` op."""
        rows = self.db.execute("SELECT key FROM meta WHERE key GLOB ?", (f"{HARNESS_OPEN}*",)).fetchall()
        pairs = (str(r[0]).removeprefix(HARNESS_OPEN).split(":", 1) for r in rows)
        return {(p[0], p[1]) for p in pairs if len(p) == 2}

    # --- resume notes and refusals (plan 5i part 2; part 3 adds notes too) ---

    @_locked
    def note_add(self, ws: str, bead: str, ref: str, text: str) -> bool:
        """A note owed to the bead's agent, once per `ref` (delivered or not), inside the caller's
        transaction. True only if it is new: a second add with the same ref keeps the first text."""
        cur = self.db.execute("INSERT OR IGNORE INTO resume_notes (ws, bead, ref, text) VALUES (?, ?, ?, ?)",
                              (ws, bead, ref, text))
        return cur.rowcount == 1

    @_locked
    def notes(self, ws: str, bead: str) -> list[tuple[int, str]]:
        """Every undelivered note of the bead as (seq, text), oldest first."""
        rows = self.db.execute("SELECT seq, text FROM resume_notes WHERE ws = ? AND bead = ? "
                               "AND delivered_at IS NULL ORDER BY seq", (ws, bead)).fetchall()
        return [(int(cast(int, r[0])), str(r[1])) for r in rows]

    @_locked
    def notes_delivered(self, ws: str, bead: str, upto: int) -> int:
        """Mark the bead's undelivered notes with `seq <= upto` delivered (a brief carried them); how many.
        A note added after the brief was composed has a higher seq, so it stays undelivered."""
        cur = self.db.execute("UPDATE resume_notes SET delivered_at = ? WHERE ws = ? AND bead = ? "
                              "AND seq <= ? AND delivered_at IS NULL", (now(), ws, bead, upto))
        return cur.rowcount

    @_locked
    def note_delivered(self, ws: str, bead: str, ref: str) -> bool:
        """Mark one undelivered note of the bead delivered by its ref (a steer carried it)."""
        cur = self.db.execute("UPDATE resume_notes SET delivered_at = ? WHERE ws = ? AND bead = ? "
                              "AND ref = ? AND delivered_at IS NULL", (now(), ws, bead, ref))
        return cur.rowcount == 1

    @_locked
    def steerable_notes(self, ws: str, bead: str, limit_bytes: int) -> list[tuple[int, str, str]]:
        """The bead's undelivered notes a steer may carry, as (seq, ref, text), oldest first: fewer than
        STEER_ATTEMPTS attempts so far, and at most `limit_bytes` of UTF-8."""
        rows = self.db.execute("SELECT seq, ref, text FROM resume_notes WHERE ws = ? AND bead = ? "
                               "AND delivered_at IS NULL AND steer_attempts < ? "
                               "AND length(CAST(text AS BLOB)) <= ? ORDER BY seq",
                               (ws, bead, STEER_ATTEMPTS, limit_bytes)).fetchall()
        return [(int(cast(int, r[0])), str(r[1]), str(r[2])) for r in rows]

    @_locked
    def note_steer_attempt(self, refs: Iterable[str], delta: int) -> None:
        """Count a steer attempt on each note (`delta` 1), or undo one (-1); never below 0."""
        self.db.executemany("UPDATE resume_notes SET steer_attempts = max(0, steer_attempts + ?) "
                            "WHERE ref = ?", [(delta, ref) for ref in refs])

    @_locked
    def refusal_add(self, ws: str, bead: str, event_id: str, session_key: str, generation: int, turn: int,
                    text: str) -> bool:
        """Record a refused request once per event, inside the caller's transaction; True only if new."""
        cur = self.db.execute("INSERT OR IGNORE INTO refusals (event_id, ws, bead, session_key, generation, "
                              "turn, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (event_id, ws, bead, session_key, generation, turn, text, now()))
        return cur.rowcount == 1

    @_locked
    def refusal(self, event_id: str) -> RefusedRequest | None:
        row = self.db.execute("SELECT ws, bead, session_key, generation, turn, text FROM refusals "
                              "WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        return RefusedRequest(str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[4]), str(row[5]))

    @_locked
    def launches_naming(self, install: str) -> list[LaunchEntry]:
        """Every launch entry, of any workstream, whose pin names `install`."""
        rows = self.db.execute(f"SELECT {LAUNCH_SQL} FROM launches WHERE install = ? "  # noqa: S608
                               "ORDER BY session_key, generation", (install,)).fetchall()
        return [_launch(r) for r in rows]

    @_locked
    def snapshot(self, ws: str) -> Snapshot:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            last = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            state = None if row is None else WsState(str(row[0]))
            return Snapshot(ws, state, self.holds(ws), self.states(ws), self.ops_open(ws),
                            int(cast(int, last[0])))
