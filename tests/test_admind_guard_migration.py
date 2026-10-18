"""Offline fixtures for the guard provenance upgrade; no live state or sends."""

import sqlite3
from pathlib import Path

import pytest

from heterodyne.admind.guard_state import migrate
from heterodyne.admind.store import SCHEMA, Store


def legacy(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(SCHEMA)
    return db


def seed(db: sqlite3.Connection, key: str, status: str = "pending", thread: str | None = None) -> int:
    cursor = db.execute("INSERT INTO outbox(key,text,status,thread_key,message_id) VALUES (?,?,?,?,?)",
                        (key, "preserved payload", status, thread, "receipt" if status == "sent" else None))
    return int(cursor.lastrowid or 0)


def test_upgrade_preserves_rows_indexes_triggers_sequence_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "db"
    db = legacy(path)
    seqs = [seed(db, "unknown:0"), seed(db, "sent", "sent"), seed(db, "failed", "failed")]
    deleted = seed(db, "deleted")
    db.execute("DELETE FROM outbox WHERE seq = ?", (deleted,))
    db.execute("CREATE INDEX custom_outbox ON outbox(status,seq)")
    db.execute("CREATE TRIGGER custom_outbox_trigger AFTER UPDATE OF status ON outbox BEGIN "
               "INSERT OR REPLACE INTO kv VALUES ('last_status',new.status); END")
    before = db.execute("SELECT seq,key,text,message_id,attempts FROM outbox ORDER BY seq").fetchall()
    db.close()
    store = Store(path)
    assert store.db.execute(
        "SELECT seq,key,text,message_id,attempts FROM outbox ORDER BY seq").fetchall() == before
    assert store.db.execute("SELECT status FROM outbox ORDER BY seq").fetchall() == [
        ("quarantined",), ("sent",), ("failed",)]
    assert store.db.execute("SELECT seq,original_status FROM guard_migration ORDER BY seq").fetchall() == [
        (seqs[0], "pending"), (seqs[1], "sent"), (seqs[2], "failed")]
    assert store.enqueue("next", "safe", None, ownership_kind="system")
    assert store.next_pending().seq > deleted  # type: ignore[union-attr]
    store.mark_sent(store.next_pending().seq, "new receipt")  # type: ignore[union-attr]
    assert store.get("last_status") == "sent"
    assert store.db.execute("SELECT name FROM sqlite_master WHERE name = 'custom_outbox'").fetchone()
    store.close()
    reopened = Store(path)
    assert reopened.db.execute("SELECT COUNT(*) FROM guard_migration").fetchone()[0] == 3
    assert reopened.pending() == []
    reopened.close()


def test_failed_rebuild_rolls_back_every_row_and_schema(tmp_path: Path) -> None:
    db = legacy(tmp_path / "db")
    seed(db, "original")
    deleted = seed(db, "deleted-high-water")
    db.execute("DELETE FROM outbox WHERE seq = ?", (deleted,))
    original_sequence = db.execute("SELECT * FROM sqlite_sequence").fetchall()
    # A deliberate final revision collision fails after the destructive DDL, inside caller transaction.
    db.execute("INSERT INTO kv VALUES ('guard_schema_revision','conflict')")
    original = db.execute("SELECT * FROM outbox").fetchall()
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.IntegrityError):
        migrate(db)
    db.execute("ROLLBACK")
    assert db.execute("SELECT * FROM outbox").fetchall() == original
    assert db.execute("SELECT * FROM sqlite_sequence").fetchall() == original_sequence
    assert "ownership_kind" not in {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'guard_migration'").fetchone()
    db.close()


def test_false_system_prefix_and_manual_request_groups_are_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "db"
    db = legacy(path)
    seed(db, "ask:aaaa:0")      # grammar alone is not a durable ask record
    seed(db, "asknote:aaaa:autobump:1:0")
    seed(db, "askr:aaaa:" + "a" * 64 + ":0")
    seed(db, "askd:aaaa:" + "a" * 64 + ":0")
    seed(db, "notice:aaaaaa:0")
    db.close()
    store = Store(path)
    assert not store.pending()
    assert store.db.execute("SELECT DISTINCT status,ownership_kind FROM outbox").fetchall() == [
        ("quarantined", "unknown")]
    store.close()


def test_exact_notice_record_proves_whole_group_but_unknown_child_holds_it(tmp_path: Path) -> None:
    path = tmp_path / "db"
    db = legacy(path)
    db.execute("INSERT INTO notices VALUES ('aaaaaa','local',NULL,1,1,0,2,'2026')")
    seed(db, "notice:aaaaaa:0")
    seed(db, "notice:aaaaaa:1", thread="notice:aaaaaa:0")
    seed(db, "forged-child", thread="notice:aaaaaa:0")
    db.close()
    store = Store(path)
    assert not store.pending()
    assert store.db.execute("SELECT DISTINCT delivery_group,status FROM outbox").fetchall() == [
        ("notice:aaaaaa", "quarantined")]
    # Held parent never falls back to top-level, including newly enqueued proven request chunks.
    store.enqueue("child:1", "secret", None, thread_key="notice:aaaaaa:0", ownership_kind="request",
                  owners=("a" * 64,), delivery_group="notice:aaaaaa")
    assert not store.pending()
    store.close()


def test_proven_notice_does_not_wait_behind_unknown_legacy_row(tmp_path: Path) -> None:
    path = tmp_path / "db"
    db = legacy(path)
    seed(db, "unknown-first")
    db.execute("INSERT INTO notices VALUES ('aaaaaa','local',NULL,1,1,0,1,'2026')")
    seed(db, "notice:aaaaaa:0")
    db.close()
    store = Store(path)
    assert store.next_pending().key == "notice:aaaaaa:0"  # type: ignore[union-attr]
    assert store.next_pending().ownership_kind == "system"  # type: ignore[union-attr]
    store.close()


def test_inbound_provenance_and_staged_details_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "db"
    store = Store(path)
    mid, owner = "b" * 64, "a" * 64
    assert store.claim_inbound(mid, sender_key=owner)
    assert not store.claim_inbound(mid, sender_key="c" * 64)
    store.stage_details(mid, 0, ["one", "two"])
    store.publish_details(mid, f"details:{mid}", mid, 2)
    store.close()
    store = Store(path)
    assert store.inbound_sender(mid) == owner
    rows = store.pending()
    assert len(rows) == 2 and len({row.delivery_group for row in rows}) == 1
    assert all(row.ownership_kind == "request" and store.row_owners(row.seq) == (owner,) for row in rows)
    store.close()


def test_foreign_key_children_survive_store_rebuild_without_delete_cascade(tmp_path: Path) -> None:
    path = tmp_path / "db"
    db = legacy(path)
    seq = seed(db, "parent")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("CREATE TABLE child (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES outbox(seq) "
               "ON DELETE CASCADE)")
    db.execute("INSERT INTO child VALUES (1,?)", (seq,))
    db.close()
    store = Store(path)
    assert store.db.execute("SELECT * FROM child").fetchall() == [(1, seq)]
    assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
    store.close()


def test_enforced_foreign_keys_fail_before_destructive_rebuild(tmp_path: Path) -> None:
    db = legacy(tmp_path / "db")
    seq = seed(db, "parent")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("CREATE TABLE child (parent INTEGER REFERENCES outbox(seq) ON DELETE CASCADE)")
    db.execute("INSERT INTO child VALUES (?)", (seq,))
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(ValueError, match="enforcement disabled"):
        migrate(db)
    db.execute("ROLLBACK")
    assert db.execute("SELECT parent FROM child").fetchall() == [(seq,)]
    assert db.execute("SELECT key FROM outbox").fetchall() == [("parent",)]
    db.close()


def test_partial_upgrade_is_rejected_even_with_marker(tmp_path: Path) -> None:
    db = legacy(tmp_path / "db")
    db.execute("ALTER TABLE outbox ADD COLUMN ownership_kind TEXT")
    db.execute("INSERT INTO kv VALUES ('guard_schema_revision','1')")
    db.close()
    with pytest.raises(ValueError, match="incomplete guard schema"):
        Store(tmp_path / "db")


@pytest.mark.parametrize("second_kind", ["request", "unknown"])
def test_redaction_preserves_all_source_provenance(tmp_path: Path, second_kind: str) -> None:
    store = Store(tmp_path / "db")
    store.enqueue("reply:session:1:0", "first", None, ownership_kind="request", owners=("a" * 64,))
    store.enqueue("reply:session:1:1", "second", None, ownership_kind=second_kind,
                  owners=("b" * 64,) if second_kind == "request" else ())
    store.set("outbox_needs_redaction", "1")
    assert store.redact_pending_outbox(100) == 2
    row = store.next_pending()
    assert row is not None
    assert row.ownership_kind == second_kind
    assert store.row_owners(row.seq) == (("a" * 64, "b" * 64) if second_kind == "request" else ())
    store.close()


def test_delayed_parent_keeps_deadline_and_child_can_resume(tmp_path: Path) -> None:
    store = Store(tmp_path / "db")
    store.enqueue("parent", "parent", None, ownership_kind="system", delivery_group="parent")
    store.hold_group("parent", "delayed", "cadence", not_before=200)
    store.enqueue("child", "child", None, thread_key="parent", ownership_kind="system")
    child = store.next_pending()
    assert child is not None
    assert store.resolve_thread(child.seq)[1] == "parent-held"
    assert store.db.execute("SELECT not_before FROM outbox WHERE key='child'").fetchone() == (200,)
    store.release_delayed(200)
    assert len(store.pending()) == 2
    store.close()
