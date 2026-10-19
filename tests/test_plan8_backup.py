import sqlite3
import threading

import pytest

from heterodyne.wsd.backup_copy import snapshot


def test_wal_snapshot_is_consistent_while_writer_keeps_committing(tmp_path):
    source = tmp_path / "live.db"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE records(n)")
    writer.execute("INSERT INTO records VALUES(1)")
    writer.commit()
    target = tmp_path / "copy.db"
    try:
        result = snapshot(source, target, threading.Event())
        writer.execute("INSERT INTO records VALUES(2)")
        writer.commit()
        with sqlite3.connect(target) as copy:
            assert copy.execute("SELECT n FROM records").fetchall() == [(1,)]
        assert isinstance(result["bytes"], int)
        assert isinstance(result["sha256"], str)
        assert result["bytes"] > 0
        assert len(result["sha256"]) == 64
        assert target.stat().st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError):
            snapshot(source, target, threading.Event())
    finally:
        writer.close()


def test_cancelled_copy_does_not_claim_verified_result(tmp_path):
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE records(n)")
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(TimeoutError):
            snapshot(source, tmp_path / "partial.db", cancel)
