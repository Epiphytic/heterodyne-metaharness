"""`admind initialised` (design btq-v7sgt D4): whether admind's store has a group, read without creating or
changing a file, so the installer can ask it of a store a running admind holds open."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from heterodyne import config as hconfig
from heterodyne.admind import cli, initialised
from heterodyne.admind.store import Store

GROUP = "ab" * 32      # a fixture group ID; never printed


def snapshot(d: Path) -> dict[str, tuple[bytes, int, int]]:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
            for p in sorted(d.iterdir()) if p.is_file()}


def made(d: Path, group: str | None) -> Store:
    store = Store(d / "admind.db")
    if group is not None:
        store.set("group_id_hex", group)
    return store


def test_a_stopped_store_with_a_group_is_initialised_and_nothing_changes(tmp_path: Path) -> None:
    made(tmp_path, GROUP).close()
    before = snapshot(tmp_path)
    assert initialised.state(tmp_path / "admind.db") == initialised.INITIALISED
    assert snapshot(tmp_path) == before


def writer(d: Path) -> sqlite3.Connection:
    """A second connection, as a running admind holds one: WAL, and no checkpoint, so commits stay in the
    WAL."""
    con = sqlite3.connect(d / "admind.db", isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    return con


def test_a_store_held_open_with_commits_only_in_its_wal_is_read_and_nothing_changes(tmp_path: Path) -> None:
    made(tmp_path, None).close()
    con = writer(tmp_path)
    try:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('group_id_hex', ?)", (GROUP,))
        assert (tmp_path / "admind.db-wal").stat().st_size > 0
        before = snapshot(tmp_path)
        assert initialised.state(tmp_path / "admind.db") == initialised.INITIALISED
        assert snapshot(tmp_path) == before          # -shm included: no read mark is taken
    finally:
        con.close()


def test_an_uncommitted_write_is_not_seen_and_nothing_changes(tmp_path: Path) -> None:
    made(tmp_path, GROUP).close()
    con = writer(tmp_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM kv WHERE key = 'group_id_hex'")
        before = snapshot(tmp_path)
        assert initialised.state(tmp_path / "admind.db") == initialised.INITIALISED
        assert snapshot(tmp_path) == before
        con.execute("ROLLBACK")
    finally:
        con.close()


def test_a_deletion_committed_during_the_read_is_seen(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """The r1 review's case: no WAL when the read starts, and a deletion committed while it runs."""
    made(tmp_path, GROUP).close()
    assert not (tmp_path / "admind.db-wal").exists()
    real, cons = initialised._read, []

    def racing(path: str) -> object:
        got = real(path)
        if not cons:
            con = writer(tmp_path)
            cons.append(con)
            con.execute("DELETE FROM kv WHERE key = 'group_id_hex'")
        return got
    monkeypatch.setattr(initialised, "_read", racing)
    try:
        assert initialised.state(tmp_path / "admind.db") == initialised.NOT_INITIALISED
    finally:
        for con in cons:
            con.close()


def test_a_writer_that_commits_during_every_pass_cannot_tell(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    made(tmp_path, GROUP).close()
    con = writer(tmp_path)
    real, n = initialised._read, [0]

    def busy(path: str) -> object:
        got = real(path)
        n[0] += 1
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('tick', ?)", (str(n[0]),))
        return got
    monkeypatch.setattr(initialised, "_read", busy)
    try:
        assert initialised.state(tmp_path / "admind.db") == initialised.UNKNOWN
        assert n[0] == 4 * initialised.ATTEMPTS
    finally:
        con.close()


def test_a_rollback_journal_or_a_wal_without_its_database_cannot_tell(tmp_path: Path) -> None:
    made(tmp_path, GROUP).close()
    (tmp_path / "admind.db-journal").write_bytes(b"x")
    assert initialised.state(tmp_path / "admind.db") == initialised.UNKNOWN
    other = tmp_path / "w"
    other.mkdir()
    (other / "admind.db-wal").write_bytes(b"x")
    assert initialised.state(other / "admind.db") == initialised.UNKNOWN


@pytest.mark.parametrize("side", ["", "-wal"])
def test_a_fifo_at_the_database_or_its_wal_cannot_tell_and_does_not_block(tmp_path: Path, side: str) -> None:
    if side:
        made(tmp_path, GROUP).close()
    os.mkfifo(tmp_path / f"admind.db{side}")
    code = ("import sys; from pathlib import Path; from heterodyne.admind import initialised; "
            "print(initialised.state(Path(sys.argv[1])))")
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "admind.db")],
                         capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout.strip() == str(initialised.UNKNOWN)


def test_the_snapshot_leaves_no_copy_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    store = tmp_path / "s"
    store.mkdir()
    made(store, GROUP).close()
    assert initialised.state(store / "admind.db") == initialised.INITIALISED
    assert list(scratch.iterdir()) == []


def test_no_group_row_is_not_initialised(tmp_path: Path) -> None:
    made(tmp_path, None).close()
    before = snapshot(tmp_path)
    assert initialised.state(tmp_path / "admind.db") == initialised.NOT_INITIALISED
    assert snapshot(tmp_path) == before


def test_no_database_is_not_initialised_and_none_is_created(tmp_path: Path) -> None:
    assert initialised.state(tmp_path / "admind.db") == initialised.NOT_INITIALISED
    assert initialised.state(tmp_path / "absent" / "admind.db") == initialised.NOT_INITIALISED
    assert list(tmp_path.iterdir()) == []


def test_a_database_that_is_not_a_regular_file_cannot_tell(tmp_path: Path) -> None:
    (tmp_path / "dir.db").mkdir()
    assert initialised.state(tmp_path / "dir.db") == initialised.UNKNOWN
    made(tmp_path, GROUP).close()
    (tmp_path / "link.db").symlink_to(tmp_path / "admind.db")
    assert initialised.state(tmp_path / "link.db") == initialised.UNKNOWN


def test_an_unreadable_database_cannot_tell(tmp_path: Path) -> None:
    made(tmp_path, GROUP).close()
    db = tmp_path / "admind.db"
    db.chmod(0)
    try:
        if os.access(db, os.R_OK):
            pytest.skip("this user reads mode-0 files")
        assert initialised.state(db) == initialised.UNKNOWN
    finally:
        db.chmod(0o600)


def test_a_corrupt_database_or_one_without_kv_cannot_tell(tmp_path: Path) -> None:
    (tmp_path / "admind.db").write_bytes(b"not sqlite" * 100)
    assert initialised.state(tmp_path / "admind.db") == initialised.UNKNOWN
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    assert initialised.state(other) == initialised.UNKNOWN


@pytest.mark.parametrize(("group", "code", "line"), [(GROUP, 0, "initialised"), (None, 1, "not initialised")])
def test_the_command_prints_one_word_and_never_the_group(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        group: str | None, code: int, line: str) -> None:
    made(tmp_path, group).close()
    monkeypatch.setattr(cli, "_settings_only", lambda: SimpleNamespace(state_dir=tmp_path))
    assert cli.main(["initialised"]) == code
    seen = capsys.readouterr()
    assert (seen.out, seen.err) == (f"{line}\n", "")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["admind.db"]


def test_the_command_exits_78_on_a_config_error(monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    def bad() -> None:
        raise hconfig.ConfigError("[admind] profile must name a profile from [profiles]")
    monkeypatch.setattr(cli, "_settings_only", bad)
    assert cli.main(["initialised"]) == cli.EX_CONFIG
    assert "config error" in capsys.readouterr().err


def test_the_command_says_when_it_cannot_tell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "admind.db").mkdir()
    monkeypatch.setattr(cli, "_settings_only", lambda: SimpleNamespace(state_dir=tmp_path))
    assert cli.main(["initialised"]) == 2
    assert capsys.readouterr().out == "cannot tell\n"
