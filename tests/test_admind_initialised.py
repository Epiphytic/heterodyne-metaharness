"""`admind initialised` (design btq-v7sgt D4): whether admind's store has a group, read without creating or
changing a file, so the installer can ask it of a store a running admind holds open."""

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from heterodyne import config as hconfig
from heterodyne.admind import cli, initialised
from heterodyne.admind.store import Store

GROUP = "ab" * 32      # a fixture group ID; never printed


def snapshot(d: Path) -> dict[str, tuple[bytes, int]]:
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(d.iterdir()) if p.is_file()}


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


def test_a_store_admind_holds_open_reads_its_group_and_nothing_changes(tmp_path: Path) -> None:
    store = made(tmp_path, GROUP)
    try:
        assert (tmp_path / "admind.db-wal").exists()
        before = snapshot(tmp_path)
        assert initialised.state(tmp_path / "admind.db") == initialised.INITIALISED
        after = snapshot(tmp_path)
        # -shm is the readers' shared lock index: any reader of a live WAL database takes a read mark in
        # it. The database and its WAL are unchanged, and no file is created.
        assert after.keys() == before.keys()
        assert {k: after[k] for k in ("admind.db", "admind.db-wal")} == {
            k: before[k] for k in ("admind.db", "admind.db-wal")}
    finally:
        store.close()


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
