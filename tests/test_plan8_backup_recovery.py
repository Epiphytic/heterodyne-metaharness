import sqlite3
import threading
import time

import pytest

from heterodyne.wsd.backup_cycles import Identity, copy_bundle, new_attempt, retain, verify_bundle
from heterodyne.wsd.effect_owner import EffectOwner
from heterodyne.wsd.effects import EffectStore


def sources(tmp_path):
    paths = (tmp_path / "journal.db", tmp_path / "effects.db")
    for path in paths:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE data(value)")
        connection.execute("INSERT INTO data VALUES('original')")
        connection.commit()
        connection.close()
    return paths


def test_manifest_revalidation_and_tamper_never_becomes_success(tmp_path):
    identity = Identity("install", "generation", "a" * 64)
    directory = tmp_path / "backups"
    attempt = new_attempt()
    result = copy_bundle(directory, sources(tmp_path), 4, "unknown", threading.Event(), identity, attempt)
    assert result["status"] == "partial"
    assert verify_bundle(directory, attempt, 4, "unknown", identity) == result
    with pytest.raises(ValueError, match="identity"):
        verify_bundle(directory, attempt, 5, "unknown", identity)
    target = directory / attempt / "local-0.db"
    target.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="hash"):
        verify_bundle(directory, attempt, 4, "unknown", identity)


def test_owner_restart_settles_only_exact_verified_manifest(tmp_path):
    path = tmp_path / "schedule-effects.db"
    EffectStore.initialize(path, "install", "generation")
    store = EffectStore(path, "install", "generation")
    attempt = new_attempt()
    assert store.begin_backup(6, "unknown") == "queued"
    store.backup_attempt(6, attempt)
    copy_bundle(
        tmp_path / "paired-backups",
        sources(tmp_path),
        6,
        "unknown",
        threading.Event(),
        Identity("install", "generation", "a" * 64),
        attempt,
    )
    store.close()  # simulates process loss before the callback committed completion
    owner = EffectOwner(path, "install", "generation", "a" * 64)
    try:
        owner.opened.result(timeout=3)
        assert owner.copy is not None
        owner.copy.result(timeout=3)
        assert owner.call(lambda s: s.begin_backup(6, "unknown")).result(timeout=3) == "completed"
        assert owner.call(lambda s: s.begin_backup(7, "unknown")).result(timeout=3) == "queued"
    finally:
        owner.close()


def test_latest_verified_success_survives_calendar_retention(tmp_path):
    identity = Identity("install", "generation", "a" * 64)
    directory = tmp_path / "backups"
    attempt = new_attempt()
    copy_bundle(directory, sources(tmp_path), 1, "succeeded", threading.Event(), identity, attempt)
    retain(directory, identity, time.time() + 90 * 86400, None)
    assert verify_bundle(directory, attempt, 1, "succeeded", identity)["status"] == "succeeded"
