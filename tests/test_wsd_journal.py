import sqlite3
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import SimulatedCrash
from hypothesis import given, settings
from hypothesis import strategies as st

from heterodyne.wsd.journal import InboxStatus, Journal, JournalCorrupt, OpConflict, OpKind, OpStatus
from heterodyne.wsd.states import BeadState, IllegalTransition, Reason, WsState, allowed


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "wsd.db")


def test_new_journal_is_fresh_and_private(tmp_path: Path) -> None:
    j = Journal(tmp_path / "state" / "wsd.db")
    assert j.fresh
    assert (tmp_path / "state" / "wsd.db").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    j.close()
    again = Journal(tmp_path / "state" / "wsd.db")
    assert not again.fresh


def test_corrupt_journal_is_refused_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    path.write_bytes(b"not a database at all" * 100)
    before = path.read_bytes()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.read_bytes() == before


def test_foreign_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE other (x)")
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.exists()


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "wsd.db").close()
    db = sqlite3.connect(tmp_path / "wsd.db")
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_journal_missing_a_table_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "wsd.db").close()
    db = sqlite3.connect(tmp_path / "wsd.db")
    db.execute("DROP TABLE holds")
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


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
