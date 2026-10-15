"""Run preparation against a disposable committed checkout and SQLite WAL fixture."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from heterodyne.admind.store import SCHEMA


def test_preparation_rehearses_snapshot_without_changing_source(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copytree(root / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (checkout / "deploy").mkdir()
    for path in ("prepare_admind_guard.py", "install.sh"):
        shutil.copyfile(root / "deploy" / path, checkout / "deploy" / path)
    git = ["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com"]
    for args in (("init", "-q"), ("add", "."), ("commit", "-qm", "offline fixture")):
        subprocess.run([*git, *args], check=True, capture_output=True)
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    database = tmp_path / "admind.db"
    db = sqlite3.connect(database)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO kv VALUES ('group_id_hex', 'fixture group')")
    db.execute("INSERT INTO kv VALUES ('latched', 'existing hold')")
    db.execute("INSERT INTO outbox(key,text,status) VALUES ('unknown:0','PRIVATE PAYLOAD','pending')")
    db.execute("INSERT INTO outbox(key,text,status) VALUES ('sent','PRIVATE HISTORY','sent')")
    db.commit()  # Keep the source connection open: committed WAL data must be included.
    before = db.execute("SELECT * FROM outbox").fetchall()
    authority = tmp_path / "operator.txt"
    authority.write_text("Fixture-only authority evidence, no real operator grant.")
    output = tmp_path / "rehearsal"
    command = [sys.executable, str(checkout / "deploy/prepare_admind_guard.py"),
               "--checkout", str(checkout), "--reviewed-commit", commit,
               "--database", str(database), "--output", str(output), "--authority-file", str(authority)]
    env = {**os.environ, "PYTHONPATH": str(checkout / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(command, env=env, check=True, capture_output=True, text=True)
    report = json.loads(result.stdout)
    assert report["before_status_counts"] == {"pending": 1, "sent": 1}
    assert report["after_status_counts"] == {"quarantined": 1, "sent": 1}
    assert report["quarantined_groups"] == 1 and not report["deployed"]
    assert "PRIVATE" not in result.stdout and "fixture group" not in result.stdout
    assert db.execute("SELECT * FROM outbox").fetchall() == before
    assert "ownership_kind" not in {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "before.db").stat().st_mode & 0o777 == 0o600
    backup = sqlite3.connect(output / "before.db")
    assert backup.execute("SELECT * FROM outbox").fetchall() == before
    backup.close()
    migrated = sqlite3.connect(output / "rehearsal.db")
    assert migrated.execute("SELECT value FROM kv WHERE key='latched'").fetchone() == ("existing hold",)
    migrated.close()
    subprocess.run(["bash", "-n", str(output / "installer-command.sh")], check=True)
    repeated = subprocess.run(command, env=env, capture_output=True)
    assert repeated.returncode != 0  # Never overwrite an earlier backup/rehearsal.
    db.close()


def test_preparation_rejects_dirty_or_wrong_revision_before_database_access(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git = ["git", "-C", str(checkout), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "--allow-empty", "-qm", "fixture"], check=True)
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    output = tmp_path / "must-not-exist"
    command = [sys.executable, str(root / "deploy/prepare_admind_guard.py"), "--checkout", str(checkout),
               "--database", str(tmp_path / "missing.db"), "--output", str(output),
               "--authority-file", str(tmp_path / "missing-authority")]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    wrong = subprocess.run([*command, "--reviewed-commit", "0" * 40], env=env, capture_output=True)
    assert wrong.returncode != 0 and b"does not match" in wrong.stderr
    (checkout / "dirty").touch()
    dirty = subprocess.run([*command, "--reviewed-commit", commit], env=env, capture_output=True)
    assert dirty.returncode != 0 and b"must be clean" in dirty.stderr
    assert not output.exists()
