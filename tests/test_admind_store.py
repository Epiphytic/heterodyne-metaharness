import json
import os
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
    assert s.relay_alert("a1", "alert:a1", "text") is True
    assert s.relay_alert("a1", "alert:a1", "text") is False
    assert s.relayed("a1") and not s.relayed("a2")
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
