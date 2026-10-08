import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from fakes.checkpoints import SimulatedCrash
from hypothesis import given, settings
from hypothesis import strategies as st

from heterodyne.wsd import journal as journal_mod
from heterodyne.wsd.journal import (
    InboxStatus,
    Journal,
    JournalBusy,
    JournalCorrupt,
    JournalUnusable,
    OpConflict,
    OpKind,
    OpStatus,
)
from heterodyne.wsd.states import BeadState, IllegalTransition, Reason, WsState, allowed


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "wsd.db")


def _files(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}


def assert_refused_preserved(path: Path) -> None:
    """Refused, and every file keeps its bytes, the main file and an existing -wal included. For a
    WAL-mode file only what `_validate` documents may differ: `-shm` created or updated, and an empty
    `-wal` created. A rollback-journal file gains nothing."""
    before = _files(path.parent)
    wal_mode = before[path.name][18:20] == b"\x02\x02"   # the header's WAL read and write versions
    with pytest.raises(JournalCorrupt):
        Journal(path)
    after = _files(path.parent)
    if wal_mode:
        shm, wal = f"{path.name}-shm", f"{path.name}-wal"
        before.pop(shm, None)
        after.pop(shm, None)
        if wal not in before:
            assert after.pop(wal, b"") == b""
    assert after == before


def _tamper(path: Path, sql: str) -> None:
    Journal(path).close()
    db = sqlite3.connect(path)
    db.executescript(sql)
    db.close()


def test_new_journal_is_fresh_and_private(tmp_path: Path) -> None:
    j = Journal(tmp_path / "state" / "wsd.db")
    assert j.fresh
    assert (tmp_path / "state" / "wsd.db").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    j.close()
    again = Journal(tmp_path / "state" / "wsd.db")
    assert not again.fresh


@pytest.mark.parametrize("content", [b"not a database at all" * 100, b""])
def test_corrupt_journal_is_refused_and_kept(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "wsd.db"
    path.write_bytes(content)
    assert_refused_preserved(path)


@pytest.mark.parametrize("mode", ["delete", "wal"])
def test_foreign_database_is_refused_unchanged(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "wsd.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA journal_mode={mode}")
    db.execute("CREATE TABLE other (x)")
    db.commit()
    db.close()
    assert_refused_preserved(path)


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    _tamper(tmp_path / "wsd.db", "UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    assert_refused_preserved(tmp_path / "wsd.db")


@pytest.mark.parametrize("sql", [
    "DROP TABLE holds",
    "DROP INDEX ops_one_open",
    "DROP INDEX ops_one_open; CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead)",
    "DROP INDEX ops_one_open; CREATE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'open'",
    "DROP INDEX ops_one_open; CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'done'",
    "ALTER TABLE holds RENAME COLUMN detail TO note",
    "ALTER TABLE events DROP COLUMN ref",
    "ALTER TABLE beads ADD COLUMN extra TEXT",
    "CREATE TABLE extra (x)",
    "CREATE INDEX extra ON ops (kind)",
])
def test_altered_schema_is_refused_unchanged(tmp_path: Path, sql: str) -> None:
    # Review finding 2: the one-open-op index (with its predicate) and every column are part of the check.
    _tamper(tmp_path / "wsd.db", sql)
    assert_refused_preserved(tmp_path / "wsd.db")


def test_journal_with_an_unflushed_log_opens(tmp_path: Path) -> None:
    # A non-empty -wal (wsd stopped before a checkpoint) is read, not ignored.
    first = Journal(tmp_path / "wsd.db")
    seq = first.emit("alpha", None, "a")
    assert (tmp_path / "wsd.db-wal").stat().st_size > 0
    second = Journal(tmp_path / "wsd.db")
    assert [e.seq for e in second.events_since(0, 10)] == [seq]
    second.close()
    first.close()


def test_schema_altered_in_an_unflushed_log_is_refused(tmp_path: Path) -> None:
    # The check reads what the log holds, not only the main file.
    path = tmp_path / "wsd.db"
    Journal(path).close()
    other = sqlite3.connect(path, isolation_level=None)
    try:
        other.execute("PRAGMA wal_autocheckpoint=0")
        other.execute("DROP INDEX ops_one_open")
        assert (tmp_path / "wsd.db-wal").stat().st_size > 0
        assert_refused_preserved(path)
    finally:
        other.close()


def _crashed_copy(tmp_path: Path, sql: str) -> Path:
    """A journal whose last writer died before a checkpoint: the main file and a -wal holding `sql`'s
    change, and no -shm."""
    path = tmp_path / "wsd.db"
    Journal(path).close()
    other = sqlite3.connect(path, isolation_level=None)
    try:
        other.execute("PRAGMA wal_autocheckpoint=0")
        other.executescript(sql)
        crash = tmp_path / "crash"
        crash.mkdir()
        for name in ("wsd.db", "wsd.db-wal"):
            (crash / name).write_bytes((tmp_path / name).read_bytes())
    finally:
        other.close()
    assert not (crash / "wsd.db-shm").exists() and (crash / "wsd.db-wal").stat().st_size > 0
    return crash / "wsd.db"


def test_retained_log_without_shm_is_read(tmp_path: Path) -> None:
    path = _crashed_copy(tmp_path, "INSERT INTO events (at, ws, kind, detail) VALUES ('t', 'alpha', 'x', '')")
    j = Journal(path)
    assert [e.kind for e in j.events_since(0, 10)] == ["x"]
    j.close()


def test_retained_log_without_shm_altering_the_schema_is_refused_preserved(tmp_path: Path) -> None:
    # r2 finding 2: the main file and the retained -wal keep their bytes; only -shm may appear.
    assert_refused_preserved(_crashed_copy(tmp_path, "DROP INDEX ops_one_open"))


@pytest.mark.parametrize("checkpointed", [False, True])
def test_schema_changed_after_validation_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    checkpointed: bool) -> None:
    # r2 finding 1: a change committed between the read-only check and the working open, left in the
    # -wal or checkpointed into the main file, is caught by the working connection's own check.
    path = tmp_path / "wsd.db"
    Journal(path).close()
    validate = journal_mod._validate  # pyright: ignore[reportPrivateUsage]
    changed: list[sqlite3.Connection] = []

    def validate_then_change(p: Path) -> None:
        validate(p)
        db = sqlite3.connect(p, isolation_level=None)
        changed.append(db)
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("DROP INDEX ops_one_open")
        if checkpointed:
            db.close()      # the last connection: the change moves into the main file
        else:
            assert (tmp_path / "wsd.db-wal").stat().st_size > 0

    monkeypatch.setattr(journal_mod, "_validate", validate_then_change)
    try:
        with pytest.raises(JournalCorrupt):
            Journal(path)
    finally:
        for db in changed:
            db.close()
    assert changed


def test_refusal_after_validation_keeps_a_log_another_writer_left(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    # r3 finding 1: the refusing connection is the last one; closing it must not checkpoint the log a
    # departed writer left into the main file, nor delete it.
    path = tmp_path / "wsd.db"
    Journal(path).close()
    validate = journal_mod._validate  # pyright: ignore[reportPrivateUsage]
    kept: dict[str, bytes] = {}

    def validate_then_writer_leaves(p: Path) -> None:
        validate(p)
        writer = sqlite3.connect(p, isolation_level=None)
        try:
            writer.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("DROP INDEX ops_one_open")
        finally:
            writer.close()
        kept.update({name: (tmp_path / name).read_bytes() for name in ("wsd.db", "wsd.db-wal")})

    monkeypatch.setattr(journal_mod, "_validate", validate_then_writer_leaves)
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert kept and kept["wsd.db-wal"]
    assert {name: (tmp_path / name).read_bytes() for name in kept} == kept


@pytest.mark.parametrize("step", ["_validate", "_connect"])
def test_each_check_reads_one_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    # A commit that lands between the check's schema read and its version read is not seen by the
    # version read: the check judges one committed state, never a mix of two.
    path = tmp_path / "wsd.db"
    Journal(path).close()
    schema = journal_mod._schema  # pyright: ignore[reportPrivateUsage]
    other = sqlite3.connect(path, isolation_level=None)

    def schema_then_commit(db: sqlite3.Connection) -> set[tuple[str, str, str, str]]:
        found = schema(db)
        other.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        return found

    monkeypatch.setattr(journal_mod, "_schema", schema_then_commit)
    try:
        result = getattr(journal_mod, step)(path)
        if isinstance(result, sqlite3.Connection):
            result.close()
        assert other.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("99",)
    finally:
        other.close()


TRANSIENT = ["SQLITE_BUSY", "SQLITE_BUSY_RECOVERY", "SQLITE_BUSY_SNAPSHOT", "SQLITE_LOCKED",
             "SQLITE_LOCKED_SHAREDCACHE"]
NOT_TRANSIENT = ["SQLITE_IOERR_READ", "SQLITE_READONLY", "SQLITE_CANTOPEN"]


def _error(name: str) -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError(name)
    exc.sqlite_errorcode = getattr(sqlite3, name)
    exc.sqlite_errorname = name
    return exc


class _Failing:
    """A connection whose every statement fails with `exc`."""

    def __init__(self, exc: sqlite3.Error) -> None:
        self.exc = exc

    def execute(self, *args: object) -> None:
        raise self.exc


@pytest.mark.parametrize("name", TRANSIENT)
def test_contention_at_open_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # r2 finding 3: extended codes are contention too.
    def fail(p: Path) -> None:
        raise _error(name)
    monkeypatch.setattr(journal_mod, "_validate", fail)
    with pytest.raises(JournalBusy, match=name):
        Journal(tmp_path / "wsd.db")


@pytest.mark.parametrize("name", NOT_TRANSIENT)
def test_other_errors_at_open_are_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def fail(p: Path) -> None:
        raise _error(name)
    monkeypatch.setattr(journal_mod, "_validate", fail)
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


@pytest.mark.parametrize("name", TRANSIENT)
def test_contention_after_open_is_busy(journal: Journal, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    # r2 finding 4: every Journal method, and opening a transaction, report contention the same way.
    monkeypatch.setattr(journal, "db", _Failing(_error(name)))
    with pytest.raises(JournalBusy, match=name):
        journal.emit("alpha", None, "a")
    with pytest.raises(JournalBusy, match=name), journal.transaction():
        pass


@pytest.mark.parametrize("name", NOT_TRANSIENT)
def test_other_errors_after_open_pass_through(journal: Journal, monkeypatch: pytest.MonkeyPatch,
                                              name: str) -> None:
    monkeypatch.setattr(journal, "db", _Failing(_error(name)))
    with pytest.raises(sqlite3.OperationalError, match=name):
        journal.emit("alpha", None, "a")


class _FailOn:
    """The journal's connection (or another _FailOn, to fail two statements), passing every statement
    through except the `nth` one starting with `prefix`, which fails with `exc` before it runs. `seen`
    counts the statements starting with `prefix`, the failing one included; `ran` lists what ran."""

    def __init__(self, db: "sqlite3.Connection | _FailOn", prefix: str, nth: int, exc: BaseException) -> None:
        self.db, self.prefix, self.nth, self.exc = db, prefix, nth, exc
        self.seen = 0
        self.ran: list[str] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        if sql.startswith(self.prefix):
            self.seen += 1
            if self.seen == self.nth:
                raise self.exc
        self.ran.append(sql)
        return self.db.execute(sql, params)

    @property
    def in_transaction(self) -> bool:
        return self.db.in_transaction

    def close(self) -> None:
        self.db.close()


def _write_then_write(j: Journal) -> None:
    with j.transaction():
        j.emit("alpha", None, "a")
        j.emit("alpha", None, "b")


def _write_then_nested_method(j: Journal) -> None:
    with j.transaction():
        j.emit("alpha", None, "a")
        j.set_state("alpha", "btq-1", BeadState.CLAIMING)


def _method_alone(j: Journal) -> None:
    j.set_state("alpha", "btq-1", BeadState.CLAIMING)    # its own transaction, its emit re-entering


@pytest.mark.parametrize(("body", "prefix", "nth"), [
    (_write_then_write, "INSERT INTO events", 2),
    (_write_then_nested_method, "INSERT INTO events", 2),
    (_method_alone, "INSERT INTO events", 1),
    (_write_then_nested_method, "COMMIT", 1),
])
def test_contention_inside_a_transaction_rolls_it_all_back(journal: Journal, monkeypatch: pytest.MonkeyPatch,
                                                         body: Callable[[Journal], None], prefix: str,
                                                         nth: int) -> None:
    # r3 finding 3: contention after the transaction began (after a write, inside a nested method, at
    # COMMIT) rolls back everything once, and the journal works afterwards.
    real = journal.db
    failing = _FailOn(real, prefix, nth, _error("SQLITE_BUSY"))
    monkeypatch.setattr(journal, "db", failing)
    with pytest.raises(JournalBusy) as caught:
        body(journal)
    assert not hasattr(caught.value, "__notes__")  # the rollback worked: no cleanup note, no poison
    assert failing.seen == nth
    assert failing.ran.count("ROLLBACK") == 1 and not real.in_transaction
    assert journal._depth == 0  # pyright: ignore[reportPrivateUsage]
    assert journal.events_since(0, 10) == [] and journal.state("alpha", "btq-1") is None
    with journal.transaction():
        journal.emit("alpha", None, "after")
    assert [e.kind for e in journal.events_since(0, 10)] == ["after"]


def test_error_that_already_rolled_back_is_raised_as_it_is(journal: Journal) -> None:
    # r3 finding 2: SQLITE_FULL rolls the transaction back itself; the cleanup must not mask it.
    pages = journal.db.execute("PRAGMA page_count").fetchone()[0]
    journal.db.execute(f"PRAGMA max_page_count={pages}")
    with pytest.raises(sqlite3.OperationalError) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
        journal.set_state("alpha", "btq-1", BeadState.CLAIMING, detail="x" * 200_000)
    assert caught.value.sqlite_errorname == "SQLITE_FULL"
    assert not hasattr(caught.value, "__notes__")  # SQLite rolled back itself: no cleanup note, no poison
    assert not journal.db.in_transaction and journal._depth == 0  # pyright: ignore[reportPrivateUsage]
    journal.db.execute("PRAGMA max_page_count=1073741823")
    assert journal.events_since(0, 10) == [] and journal.state("alpha", "btq-1") is None
    journal.emit("alpha", None, "after")
    assert [e.kind for e in journal.events_since(0, 10)] == ["after"]


def test_a_write_lock_held_elsewhere_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(journal_mod, "BUSY_TIMEOUT", 0.0)
    journal = Journal(tmp_path / "wsd.db")
    other = sqlite3.connect(tmp_path / "wsd.db", isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(JournalBusy):
            journal.emit("alpha", None, "a")
        with pytest.raises(JournalBusy), journal.transaction():
            pass
        other.execute("ROLLBACK")
        with journal.transaction():
            journal.emit("alpha", None, "b")
    finally:
        other.close()
    assert [e.kind for e in journal.events_since(0, 10)] == ["b"]
    journal.close()


def test_locked_journal_is_busy_not_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Review finding 5: contention is transient and must never read as corruption.
    Journal(tmp_path / "wsd.db").close()
    monkeypatch.setattr(journal_mod, "BUSY_TIMEOUT", 0.0)
    other = sqlite3.connect(tmp_path / "wsd.db", isolation_level=None)
    try:
        other.execute("PRAGMA locking_mode=EXCLUSIVE")
        other.execute("INSERT INTO events (at, ws, kind, detail) VALUES ('t', 'alpha', 'x', '')")
        with pytest.raises(JournalBusy):
            Journal(tmp_path / "wsd.db")
    finally:
        other.close()
    again = Journal(tmp_path / "wsd.db")
    assert [e.kind for e in again.events_since(0, 10)] == ["x"]
    again.close()


def test_symlinked_journal_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "real.db").close()
    (tmp_path / "wsd.db").symlink_to(tmp_path / "real.db")
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_inbox_dedups_on_surface_and_event(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}", ws="alpha")
    assert seq is not None
    assert journal.inbox_add("marmot", "ev1", "approve", "{}") is None
    assert journal.inbox_add("github", "ev1", "approve", "{}") is not None
    assert [r.event_id for r in journal.inbox_pending()] == ["ev1", "ev1"]


def test_inbox_escalates_after_limit(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.NEEDS_HUMAN
    assert journal.inbox_pending_count() == 0
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_inbox_finish_is_once(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    journal.inbox_finish(seq, InboxStatus.SUPERSEDED)
    row = journal.inbox_get(seq)
    assert row is not None and row.status is InboxStatus.SUPERSEDED
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_one_open_op_per_bead(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    with pytest.raises(OpConflict):
        journal.op_open(OpKind.RESUME, "alpha", "btq-1")
    journal.op_finish(op.op_id, OpStatus.DONE)
    journal.op_open(OpKind.RESUME, "alpha", "btq-1")


def test_op_step_merges_data_and_counts_failures(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    op = journal.op_step(op.op_id, "committed", {"sha": "abc"})
    assert (op.step, op.data) == ("committed", {"blockers": "btq-2", "sha": "abc"})
    assert journal.op_failed(op.op_id) == 1
    assert journal.op_failed(op.op_id) == 2
    journal.op_finish(op.op_id, OpStatus.STUCK)
    with pytest.raises(OpConflict):
        journal.op_step(op.op_id, "labelled")
    assert journal.op_for("alpha", "btq-1") is None


def test_transaction_rolls_back_on_crash(journal: Journal) -> None:
    with pytest.raises(SimulatedCrash), journal.transaction():
        journal.op_open(OpKind.PICKUP, "alpha", "btq-1")
        journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
        raise SimulatedCrash("x")
    assert journal.ops_open() == []
    assert journal.state("alpha", "btq-1") is None
    assert journal.events_since(0, 10) == []


def test_set_state_records_reason_and_event(journal: Journal) -> None:
    for step in (BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.PARKING):
        journal.set_state("alpha", "btq-1", step)
    since = journal.events_since(0, 100)[-1].seq
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    events = journal.events_since(since, 10)
    assert [(e.kind, e.detail, e.ref) for e in events] == [
        ("state:parked", '{"reason":"blocked_on_bead","detail":"btq-2"}', "msg1")]


def test_adopt_skips_the_transition_check(journal: Journal) -> None:
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.adopt("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD)
    row = journal.state("alpha", "btq-1")
    assert row is not None and row.state is BeadState.PARKED


def test_adopt_closes_a_bead_from_any_row(journal: Journal) -> None:
    # Finding 1 (r1): recovery adopting an externally closed bead must not trip the transition table.
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.set_state("alpha", "btq-1", BeadState.STARTING)
    journal.set_state("alpha", "btq-1", BeadState.RUNNING)
    journal.adopt("alpha", "btq-1", BeadState.CLOSED)
    journal.adopt("alpha", "btq-2", BeadState.CLOSED)        # no row at all
    assert [r.state for r in journal.states("alpha")] == [BeadState.CLOSED, BeadState.CLOSED]


def test_backup_is_a_coherent_private_copy(journal: Journal, tmp_path: Path) -> None:
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.op_open(OpKind.PICKUP, "alpha", "btq-1")
    dest = tmp_path / "backups" / "wsd.db"
    dest.parent.mkdir()
    journal.backup(dest)
    assert dest.stat().st_mode & 0o777 == 0o600
    assert not list(dest.parent.glob(".*.part"))
    copy = Journal(dest)            # opens: the integrity and schema checks pass on the copy
    assert copy.state("alpha", "btq-1") is not None and len(copy.ops_open("alpha")) == 1
    copy.close()


def test_holds_and_snapshot(journal: Journal) -> None:
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.set_ws_state("alpha", WsState.HELD)
    snap = journal.snapshot("alpha")
    assert snap.holds == {Reason.BEADS_UNREACHABLE: "RuntimeError"}
    assert snap.state is WsState.HELD
    journal.unhold("alpha", Reason.BEADS_UNREACHABLE)
    assert [e.kind for e in journal.events_since(0, 10)] == ["hold", "ws:held", "unhold"]


def test_backup_is_refused_inside_a_transaction(journal: Journal, tmp_path: Path) -> None:
    # Review finding 4: the copy would wait forever on the caller's own write lock.
    dest = tmp_path / "wsd-backup.db"
    with journal.transaction():
        journal.emit("alpha", None, "a")
        with pytest.raises(RuntimeError):
            journal.backup(dest)
    assert not dest.exists() and not list(tmp_path.glob(".*.part"))
    journal.backup(dest)
    assert dest.exists()


def test_events_since_pages_in_order(journal: Journal) -> None:
    seqs = [journal.emit("alpha", None, f"e{i}") for i in range(7)]
    pages: list[list[int]] = []
    cursor = 0
    while page := journal.events_since(cursor, 3):
        pages.append([e.seq for e in page])
        cursor = page[-1].seq
    assert pages == [seqs[0:3], seqs[3:6], seqs[6:7]]


def test_events_cursor(journal: Journal) -> None:
    first = journal.emit("alpha", None, "a")
    journal.emit("alpha", "btq-1", "b")
    assert [e.kind for e in journal.events_since(first, 10)] == ["b"]


def test_concurrent_writers_serialize(journal: Journal) -> None:
    def work(n: int) -> None:
        for i in range(50):
            journal.emit("alpha", f"b{n}", f"e{i}")
    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            if t.ident is not None:
                t.join()
    assert len(journal.events_since(0, 1000)) == 200


class _SignalBeforeAcquire:
    """Journal.lock, instrumented: `reached` is set just before the watched thread tries to take it."""

    def __init__(self, real: threading.RLock) -> None:
        self.real = real
        self.reached = threading.Event()
        self.watched: threading.Thread | None = None

    def __enter__(self) -> None:
        if threading.current_thread() is self.watched:
            self.reached.set()
        self.real.acquire()

    def __exit__(self, *exc: object) -> None:
        self.real.release()


def test_a_transaction_excludes_other_threads(journal: Journal, monkeypatch: pytest.MonkeyPatch) -> None:
    # Review finding 3: a worker's write must neither run inside another thread's open transaction nor
    # vanish in its rollback; it waits at the lock and lands after.
    lock = _SignalBeforeAcquire(journal.lock)
    monkeypatch.setattr(journal, "lock", lock)
    trace: list[tuple[str, str]] = []
    journal.db.set_trace_callback(lambda sql: trace.append((threading.current_thread().name, sql)))
    seqs: list[int] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            seqs.append(journal.emit("alpha", "btq-w", "worker"))
        except BaseException as exc:  # noqa: BLE001  # re-raised in the test thread
            errors.append(exc)

    worker = threading.Thread(target=work, name="worker")
    lock.watched = worker
    owner = threading.current_thread().name
    try:
        with pytest.raises(SimulatedCrash), journal.transaction():
            journal.emit("alpha", "btq-o", "owner")
            worker.start()
            assert lock.reached.wait(5), "the worker never went through the journal lock"
            raise SimulatedCrash("owner")
    finally:
        if worker.ident is not None:
            worker.join(5)
        journal.db.set_trace_callback(None)
    assert not worker.is_alive()
    if errors:
        raise errors[0]
    rollback = trace.index((owner, "ROLLBACK"))
    worker_sql = [i for i, (name, _) in enumerate(trace) if name == "worker"]
    assert worker_sql and min(worker_sql) > rollback
    assert [(e.seq, e.kind) for e in journal.events_since(0, 10)] == [(seqs[0], "worker")]


# btq-ekktm (docs/superpowers/specs/2026-10-08-wsd-journal-cleanup-errors-design.md): when the cleanup of a
# failed transaction fails too, the block's own error leaves with a note, and the journal refuses all use.

CLEANUP_NOTE = "journal cleanup failed ({}); the journal is unusable; open a new Journal"


def _cleanup_fails(journal: Journal, monkeypatch: pytest.MonkeyPatch, *, commit: BaseException | None = None,
                   rollback: BaseException | None = None) -> tuple[sqlite3.Connection, _FailOn]:
    """Fail the transaction's ROLLBACK (SQLITE_INTERRUPT unless `rollback` says otherwise) and, with
    `commit`, its COMMIT first. Returns the real connection and the ROLLBACK proxy."""
    real = journal.db
    inner: sqlite3.Connection | _FailOn = real if commit is None else _FailOn(real, "COMMIT", 1, commit)
    failing = _FailOn(inner, "ROLLBACK", 1, _error("SQLITE_INTERRUPT") if rollback is None else rollback)
    monkeypatch.setattr(journal, "db", failing)
    return real, failing


def _enter(journal: Journal) -> None:
    with journal.transaction():
        pass


def _assert_unusable(journal: Journal, cause: BaseException | type[BaseException]) -> None:
    with pytest.raises(JournalUnusable) as refused:
        journal.emit("alpha", None, "after")
    if isinstance(cause, BaseException):
        assert refused.value.__cause__ is cause
    else:
        assert isinstance(refused.value.__cause__, cause)


def _kinds(path: Path) -> list[str]:
    reopened = Journal(path)
    try:
        return [e.kind for e in reopened.events_since(0, 10)]
    finally:
        reopened.close()


def test_a_failed_rollback_keeps_the_error_and_poisons_the_journal(tmp_path: Path, journal: Journal,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    real, failing = _cleanup_fails(journal, monkeypatch)
    primary = ValueError("primary")
    with pytest.raises(ValueError) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
        raise primary
    assert caught.value is primary
    assert caught.value.__notes__ == [CLEANUP_NOTE.format("OperationalError: SQLITE_INTERRUPT")]
    assert real.in_transaction and failing.seen == 1
    assert journal._depth == 0  # pyright: ignore[reportPrivateUsage]
    dest = tmp_path / "copy.db"
    for call in (lambda: journal.emit("alpha", None, "b"), lambda: _enter(journal),
                 lambda: journal.backup(dest)):
        with pytest.raises(JournalUnusable) as refused:
            call()
        assert refused.value.__cause__ is failing.exc
    assert not dest.exists()
    journal.close()
    journal.close()
    _assert_unusable(journal, failing.exc)    # still poisoned after close: recovery is a new Journal
    assert _kinds(tmp_path / "state" / "wsd.db") == []


def test_a_failed_rollback_after_a_failed_commit_keeps_the_commit_error(
        tmp_path: Path, journal: Journal, monkeypatch: pytest.MonkeyPatch) -> None:
    commit = _error("SQLITE_IOERR_READ")
    _, failing = _cleanup_fails(journal, monkeypatch, commit=commit)
    with pytest.raises(sqlite3.OperationalError) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
    assert caught.value is commit
    assert caught.value.__notes__ == [CLEANUP_NOTE.format("OperationalError: SQLITE_INTERRUPT")]
    _assert_unusable(journal, failing.exc)
    journal.close()
    assert _kinds(tmp_path / "state" / "wsd.db") == []


def test_busy_at_commit_then_a_failed_rollback_keeps_the_note(journal: Journal,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    # r1 finding 2: the note is added to the raw SQLITE_BUSY, which _busy() then turns into JournalBusy.
    _, failing = _cleanup_fails(journal, monkeypatch, commit=_error("SQLITE_BUSY"))
    with pytest.raises(JournalBusy) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
    assert caught.value.__notes__ == [CLEANUP_NOTE.format("OperationalError: SQLITE_INTERRUPT")]
    _assert_unusable(journal, failing.exc)
    journal.close()


def test_a_failed_rollback_keeps_the_errors_own_chain(journal: Journal,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # r1 finding 3: E is re-raised with a bare raise; its cause, context and notes are its own.
    _cleanup_fails(journal, monkeypatch)
    cause, context = RuntimeError("cause"), KeyError("context")
    primary = ValueError("primary")
    primary.__cause__, primary.__context__ = cause, context
    primary.__suppress_context__ = True
    primary.add_note("prior")
    with pytest.raises(ValueError) as caught, journal.transaction():
        raise primary
    assert caught.value is primary
    assert primary.__cause__ is cause and primary.__context__ is context and primary.__suppress_context__
    assert primary.__notes__ == ["prior", CLEANUP_NOTE.format("OperationalError: SQLITE_INTERRUPT")]
    journal.close()


def test_an_interrupted_rollback_in_sqlite_poisons_the_journal(tmp_path: Path, journal: Journal) -> None:
    # Supplementary, on real SQLite: the injected tests above are the required coverage. A ROLLBACK the
    # progress handler interrupts leaves the transaction open and the write lock held (r1 finding 6).
    path = tmp_path / "state" / "wsd.db"
    with pytest.raises(ValueError, match="primary"), journal.transaction():
        journal.emit("alpha", None, "a")
        journal.db.set_progress_handler(lambda: 1, 1)   # armed after the write: only the ROLLBACK runs
        raise ValueError("primary")
    journal.db.set_progress_handler(None, 1)
    if journal._broken is None and not journal.db.in_transaction:  # pyright: ignore[reportPrivateUsage]
        journal.close()
        pytest.skip(f"SQLite {sqlite3.sqlite_version} did not interrupt the ROLLBACK")
    assert journal.db.in_transaction
    with pytest.raises(JournalUnusable) as refused:
        journal.emit("alpha", None, "b")
    cause = refused.value.__cause__
    assert isinstance(cause, sqlite3.OperationalError) and cause.sqlite_errorname == "SQLITE_INTERRUPT"
    other = sqlite3.connect(path, isolation_level=None, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError) as busy:
            other.execute("BEGIN IMMEDIATE")
        assert busy.value.sqlite_errorname == "SQLITE_BUSY"
        journal.close()                                 # discards the open transaction, releases the lock
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()
    assert _kinds(path) == []


def test_a_journal_closed_inside_the_block_keeps_the_error(tmp_path: Path, journal: Journal) -> None:
    primary = ValueError("primary")
    with pytest.raises(ValueError) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
        journal.close()
        raise primary
    assert caught.value is primary
    assert caught.value.__notes__ == [CLEANUP_NOTE.format(
        "ProgrammingError: Cannot operate on a closed database.")]
    _assert_unusable(journal, sqlite3.ProgrammingError)
    assert _kinds(tmp_path / "state" / "wsd.db") == []


def test_a_journal_closed_inside_a_clean_block_notes_the_commit_error(journal: Journal) -> None:
    with pytest.raises(sqlite3.ProgrammingError) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
        journal.close()
    assert caught.value.__notes__ == [CLEANUP_NOTE.format(
        "ProgrammingError: Cannot operate on a closed database.")]
    _assert_unusable(journal, sqlite3.ProgrammingError)


@pytest.mark.parametrize("kind", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_an_interrupt_during_cleanup_wins_and_poisons(journal: Journal, monkeypatch: pytest.MonkeyPatch,
                                                      kind: type[BaseException]) -> None:
    # Code review r1: every BaseException that is not an Exception wins over the primary, not only
    # KeyboardInterrupt.
    interrupt = kind()
    real, _ = _cleanup_fails(journal, monkeypatch, rollback=interrupt)
    primary = ValueError("primary")
    with pytest.raises(kind) as caught, journal.transaction():
        journal.emit("alpha", None, "a")
        raise primary
    assert caught.value is interrupt and interrupt.__context__ is primary
    assert not hasattr(primary, "__notes__")
    assert journal._depth == 0  # pyright: ignore[reportPrivateUsage]
    _assert_unusable(journal, interrupt)
    real.close()


def test_a_failed_rollback_under_a_nested_block_cleans_up_once(journal: Journal,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    real = journal.db
    insert = _error("SQLITE_IOERR_READ")
    monkeypatch.setattr(journal, "db", _FailOn(real, "INSERT INTO events", 2, insert))
    _, failing = _cleanup_fails(journal, monkeypatch)
    with pytest.raises(sqlite3.OperationalError) as caught:
        _write_then_nested_method(journal)
    assert caught.value is insert and failing.seen == 1
    assert caught.value.__notes__ == [CLEANUP_NOTE.format("OperationalError: SQLITE_INTERRUPT")]
    assert journal._depth == 0  # pyright: ignore[reportPrivateUsage]
    _assert_unusable(journal, failing.exc)
    real.close()


def test_a_thread_queued_on_the_lock_is_refused_without_sql(journal: Journal,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    # r1 finding 4: the check runs after the lock is taken, so a worker queued behind the failing
    # transaction is refused and runs no SQL on the connection still holding the open transaction.
    lock = _SignalBeforeAcquire(journal.lock)
    monkeypatch.setattr(journal, "lock", lock)
    trace: list[tuple[str, str]] = []
    real = journal.db
    real.set_trace_callback(lambda sql: trace.append((threading.current_thread().name, sql)))
    _, failing = _cleanup_fails(journal, monkeypatch)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            journal.emit("alpha", "btq-w", "worker")
        except BaseException as exc:  # noqa: BLE001  # checked in the test thread
            errors.append(exc)

    worker = threading.Thread(target=work, name="worker")
    lock.watched = worker
    try:
        with pytest.raises(SimulatedCrash), journal.transaction():
            journal.emit("alpha", "btq-o", "owner")
            worker.start()
            assert lock.reached.wait(5), "the worker never went through the journal lock"
            raise SimulatedCrash("owner")
    finally:
        if worker.ident is not None:
            worker.join(5)
        real.set_trace_callback(None)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], JournalUnusable), errors
    assert errors[0].__cause__ is failing.exc
    assert not [sql for name, sql in trace if name == "worker"]
    real.close()


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(list(BeadState)), max_size=25))
def test_journal_only_records_legal_transitions(tmp_path_factory: pytest.TempPathFactory,
                                                steps: list[BeadState]) -> None:
    journal = Journal(tmp_path_factory.mktemp("j") / "wsd.db")
    current: BeadState | None = None
    for step in steps:
        if allowed(current, step):
            journal.set_state("alpha", "btq-1", step)
            current = step
        else:
            with pytest.raises(IllegalTransition):
                journal.set_state("alpha", "btq-1", step)
        row = journal.state("alpha", "btq-1")
        assert (None if row is None else row.state) == current
    kinds = [e.kind for e in journal.events_since(0, 1000)]
    assert len(kinds) == len([k for k in kinds if k.startswith("state:")])
    journal.close()
