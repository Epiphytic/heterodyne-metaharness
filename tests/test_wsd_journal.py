import sqlite3
import threading
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


def assert_refused_unchanged(path: Path) -> None:
    """Refused, and the directory is byte for byte what it was: the file untouched, no -wal or -shm."""
    before = _files(path.parent)
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert _files(path.parent) == before


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
    assert_refused_unchanged(path)


@pytest.mark.parametrize("mode", ["delete", "wal"])
def test_foreign_database_is_refused_unchanged(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "wsd.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA journal_mode={mode}")
    db.execute("CREATE TABLE other (x)")
    db.commit()
    db.close()
    assert_refused_unchanged(path)


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    _tamper(tmp_path / "wsd.db", "UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    assert_refused_unchanged(tmp_path / "wsd.db")


@pytest.mark.parametrize("sql", [
    "DROP TABLE holds",
    "DROP INDEX ops_one_open",
    "DROP INDEX ops_one_open; CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead)",
    "DROP INDEX ops_one_open; CREATE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'open'",
    "DROP INDEX ops_one_open; CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'done'",
    "ALTER TABLE holds RENAME COLUMN detail TO note",
    "ALTER TABLE events DROP COLUMN ref",
    "ALTER TABLE beads ADD COLUMN extra TEXT",
])
def test_altered_schema_is_refused_unchanged(tmp_path: Path, sql: str) -> None:
    # Review finding 2: the one-open-op index (with its predicate) and every column are part of the check.
    _tamper(tmp_path / "wsd.db", sql)
    assert_refused_unchanged(tmp_path / "wsd.db")


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
        before = path.read_bytes()
        with pytest.raises(JournalCorrupt):
            Journal(path)
        assert path.read_bytes() == before
    finally:
        other.close()


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
