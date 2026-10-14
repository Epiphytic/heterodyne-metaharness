import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from heterodyne.admind.audit import Audit
from heterodyne.admind.store import Store


def test_files_are_private(tmp_path: Path) -> None:
    old = os.umask(0)
    try:
        Store(tmp_path / "a" / "admind.db")
        Audit(tmp_path / "a" / "audit.jsonl").write("test")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "a" / "admind.db").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "a" / "audit.jsonl").stat().st_mode) == 0o600


def test_kv_round_trip_and_persistence(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.get("k") is None
    s.set("k", "v1")
    s.set("k", "v2")
    s.close()
    s = Store(tmp_path / "db")
    assert s.get("k") == "v2"
    s.delete("k")
    assert s.get("k") is None


def test_claim_inbound_is_once_only(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.claim_inbound("m1") is True
    assert s.claim_inbound("m1") is False
    assert s.inbound_with_status("received") == ["m1"]
    s.set_inbound("m1", "dispatched")
    assert s.inbound_with_status("received") == []
    with pytest.raises(Exception, match="CHECK"):
        s.set_inbound("m1", "bogus")


def test_outbox_order_retry_and_dedup(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.enqueue("k1", "one", None) is True
    assert s.enqueue("k2", "two", "m1") is True
    assert s.enqueue("k1", "dup", None) is False
    rows = s.pending()
    assert [(r.key, r.text, r.reply_to) for r in rows] == [("k1", "one", None), ("k2", "two", "m1")]
    assert s.mark_attempt(rows[0].seq) == 1
    assert s.mark_attempt(rows[0].seq) == 2
    s.mark_sent(rows[0].seq, "x1")
    s.mark_failed(rows[1].seq)
    assert s.pending() == []


def test_store_is_usable_from_worker_threads(tmp_path: Path) -> None:
    import concurrent.futures
    s = Store(tmp_path / "db")
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: s.enqueue(f"k{i}", "t", None), range(50)))
    assert len(s.pending()) == 50


def test_relay_alert_is_atomic_and_once(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    assert s.relay_alert(b"a1", "alert:a1", "text") is True
    assert s.relay_alert(b"a1", "alert:a1", "text") is False
    assert s.relayed(b"a1") and not s.relayed(b"a2")
    assert [r.key for r in s.pending()] == ["alert:a1"]


def test_audit_appends_json_lines(tmp_path: Path) -> None:
    a = Audit(tmp_path / "audit.jsonl")
    a.write("inbound", message_id="m1", text="héllo\nworld")
    a.write("drop", reason="x")
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert first["kind"] == "inbound" and first["text"] == "héllo\nworld" and "ts" in first
    assert json.loads(lines[1])["kind"] == "drop"


def test_audit_refuses_a_symlink(tmp_path: Path) -> None:
    (tmp_path / "target").write_text("")
    (tmp_path / "audit.jsonl").symlink_to(tmp_path / "target")
    with pytest.raises(OSError):
        Audit(tmp_path / "audit.jsonl").write("x")


def test_audit_isolates_a_preseeded_torn_fragment(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b'{"ts": "x", "kind": "tor')
    Audit(path).write("after", n=1)
    last = json.loads(path.read_text().splitlines()[-1])
    assert last["kind"] == "after" and last["n"] == 1
    assert path.read_bytes().endswith(b"\n")


def test_audit_recovers_after_a_partial_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from heterodyne.admind import audit as audit_mod

    real_write = os.write
    state = {"armed": True}

    def flaky(fd: int, data: "bytes | memoryview") -> int:
        if state["armed"]:
            state["armed"] = False
            real_write(fd, bytes(data)[:10])
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    a = Audit(tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit_mod.os, "write", flaky)
    with pytest.raises(OSError):
        a.write("lost", n=1)
    a.write("kept", n=2)
    a.write("kept2", n=3)
    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert [json.loads(x)["kind"] for x in lines[-2:]] == ["kept", "kept2"]


def test_relay_alert_rejects_outbox_key_collision(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    s.enqueue("alert:a1", "unrelated", None)
    with pytest.raises(ValueError, match="alert:a1") as exc:
        s.relay_alert(b"a1", "alert:a1", "text")
    assert "unrelated" not in str(exc.value)
    assert not s.relayed(b"a1")
    assert [r.text for r in s.pending()] == ["unrelated"]


def test_relay_alert_rolls_back_when_outbox_insert_fails(tmp_path: Path) -> None:
    s = Store(tmp_path / "db")
    s.db.execute("CREATE TRIGGER boom BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(Exception, match="boom"):
        s.relay_alert(b"a1", "alert:a1", "text")
    s.db.execute("DROP TRIGGER boom")
    assert not s.relayed(b"a1")
    assert s.pending() == []
    assert s.relay_alert(b"a1", "alert:a1", "text") is True



# --- cxmkf K26: the migration from the schema before card kinds ----------------------------------
# A frozen copy of `store.SCHEMA` at 5a9ec58, the last schema without notices, forms or threading.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbound (
    message_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('received', 'dispatched', 'executing', 'done', 'dropped')),
    received_at TEXT NOT NULL,
    reply_to TEXT);     -- a reaction (`r:<event id>`): the reacted message, which its replies thread to
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
CREATE TABLE IF NOT EXISTS asks (
    ask_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('question', 'merge', 'approval')),
    poster TEXT NOT NULL,
    peer_pid INTEGER,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    pr_url TEXT, head_sha TEXT,
    bead TEXT, digest TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    card_parts INTEGER NOT NULL,
    attempt TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'deciding', 'approved', 'denied', 'stale',
                                           'superseded', 'cancelled', 'blocked', 'uncertain')),
    outcome TEXT,
    decided_by TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    refreshed_from TEXT,        -- the stale ask this fresh card replaced (relay delta R29)
    last_activity_at TEXT);     -- the automatic-bump clock, moved only forward (asks bump B14)
CREATE TABLE IF NOT EXISTS ask_answers (
    ask_id TEXT NOT NULL, seq INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('answer', 'note')),
    operator TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE, text TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (ask_id, seq));
CREATE TABLE IF NOT EXISTS ask_details (
    ask_id TEXT NOT NULL, operator TEXT NOT NULL, message_id TEXT NOT NULL,
    parts INTEGER NOT NULL,
    PRIMARY KEY (ask_id, message_id));
CREATE TABLE IF NOT EXISTS ask_attempts (
    ask_id TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL CHECK (action IN ('approve', 'deny')),
    operator TEXT NOT NULL, ref TEXT NOT NULL, digest TEXT NOT NULL, note TEXT,
    started_at TEXT NOT NULL, exit_status INTEGER,
    settled TEXT CHECK (settled IN ('refused', 'recorded', 'untouched', 'blocked', 'uncertain')),
    PRIMARY KEY (ask_id, message_id));
CREATE TABLE IF NOT EXISTS ask_repeats (
    ask_id TEXT NOT NULL, request TEXT NOT NULL,
    parts INTEGER NOT NULL,             -- this repeat's own chunk count (asks bump B11, B12)
    created_at TEXT NOT NULL,
    PRIMARY KEY (ask_id, request));
CREATE INDEX IF NOT EXISTS outbox_message_id ON outbox(message_id);
"""

def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in db.execute(f"PRAGMA table_info({table})")]


def test_cxmkf_migration_adds_each_column_and_the_table_once_and_keeps_rows(tmp_path: Path) -> None:
    path = tmp_path / "admind.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_SCHEMA)
    old.execute("INSERT INTO asks(ask_id, kind, poster, title, body, card_parts, status, created_at, "
                "updated_at) VALUES ('k7m2', 'question', 'p', 'T', 'B', 1, 'open', 't0', 't0')")
    old.execute("INSERT INTO ask_answers VALUES ('k7m2', 1, 'answer', 'op', 'm1', 'yes', 't1')")
    old.execute("INSERT INTO outbox(key, reply_to, text, status) "
                "VALUES ('ask:k7m2:0', NULL, 'card', 'pending')")
    old.commit()
    old.close()
    for _ in range(2):              # and again: idempotent on restart
        store = Store(path)
        assert columns(store.db, "outbox").count("thread_key") == 1
        added = [columns(store.db, "asks").count(c) for c in ("question_form", "choices", "links")]
        assert added == [1, 1, 1]
        assert columns(store.db, "ask_answers").count("choice") == 1
        assert columns(store.db, "notices") == ["notice_id", "poster", "peer_pid", "title_chars",
                                                "body_chars", "links", "parts", "created_at"]
        row = store.ask("k7m2")
        assert row is not None and row.title == "T"
        assert (row.question_form, row.choices, row.links) == (None, None, None)
        answers = store.answers("k7m2")
        assert [(a.text, a.choice) for a in answers] == [("yes", None)]
        pending = store.pending()
        assert [(r.key, r.text, r.thread_key) for r in pending] == [("ask:k7m2:0", "card", None)]
        store.db.close()
