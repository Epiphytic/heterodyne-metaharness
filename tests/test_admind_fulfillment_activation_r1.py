"""Actual HIGH activation regressions; disposable Store, no transport or live migration."""

import os

import msgspec
import pytest
from test_admind_fulfillment import apply, completion, declaration, seed
from test_admind_fulfillment_activation import migrated

from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_projection as p


def test_new_receipt_stamps_current_generation_atomically(tmp_path):
    store = migrated(tmp_path)
    try:
        seed(store)
        result = apply(store)
        assert store.capacity_count() == 0
        assert result.view is not None
        assert result.view["active_after"] == store.capacity_count()
        assert store.fulfillment.view("abcd")["lifecycle"] == "fulfilled"
        store.db.execute("UPDATE asks SET body='old writer'")
        assert store.capacity_count() == 1
    finally:
        store.close()


@pytest.mark.parametrize("damage", ["json-array", "binding-shape", "invalid-utf8", "missing-generation"])
def test_bad_receipt_isolated_from_later_ids(tmp_path, damage):
    store = migrated(tmp_path)
    try:
        for i, name in enumerate(("abcd", "bcde")):
            seed(store, name)
            apply(store, msgspec.structs.replace(completion(store, name), operation_id=f"op:{i}"))
        # Model retained malformed data without weakening the production immutable trigger contract.
        store.db.execute("DROP TRIGGER fq2_receipt_immutable")
        payload = {
            "json-array": "[]",
            "binding-shape": msgspec.json.encode(
                {"binding": {**msgspec.to_builtins(completion(store).binding), "consumed_answers": 4}}
            ).decode(),
            "invalid-utf8": None,
            "missing-generation": None,
        }[damage]
        if payload is not None:
            store.db.execute("UPDATE ask_fulfillments SET receipt=? WHERE ask_id='abcd'", (payload,))
        if damage == "invalid-utf8":
            store.db.execute("UPDATE ask_fulfillments SET receipt=CAST(x'80' AS TEXT) WHERE ask_id='abcd'")
        store.db.execute(p.TRIGGERS[7])
        if damage == "missing-generation":
            store.db.execute("DELETE FROM fulfillment_generations WHERE ask_id='abcd'")
        store.db.execute("UPDATE ask_fulfillments SET valid=0")
        store.db.execute("DELETE FROM fulfillment_verifications")
        p.verify_pass(store)
        assert store.fulfillment.view("bcde")["lifecycle"] == "fulfilled"
        assert store.capacity_count() == 1
    finally:
        store.close()


def test_pending_view_keeps_v1_grammar(tmp_path):
    store = migrated(tmp_path)
    try:
        seed(store)
        store.fulfillment.propose(f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=1800)
        pending = store.fulfillment.view("abcd")["pending"]
        assert isinstance(pending, dict)
        assert set(pending) == {"intent_id", "digest", "status", "created_at"}
    finally:
        store.close()


@pytest.mark.parametrize("owner", [None, "a" * 64])
def test_expiry_does_not_waive_replacement_current_ownership(tmp_path, owner):
    store = migrated(tmp_path)
    try:
        seed(store)
        if owner:
            store.claim_inbound(store.answers("abcd")[0].message_id, sender_key=owner)
        store.fulfillment.notice_authority = lambda: set()
        first = store.fulfillment.propose(
            f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=1800
        )
        store.db.execute("UPDATE ask_fulfillment_intents SET created_at='2020-01-01T00:00:00+00:00'")
        assert first.view is not None
        old_digest = first.view["digest"]
        assert isinstance(old_digest, str)
        changed = msgspec.structs.replace(declaration(store), description="New exact declaration.")
        with pytest.raises(f.Invalid, match="notice-owner-unavailable"):
            store.fulfillment.propose(
                f.Propose("abcd", changed, old_digest), uid=os.getuid(), chunk_chars=1800
            )
        assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone() == (1,)
    finally:
        store.close()


def test_new_writer_wakes_only_after_commit_and_verifier_does_not_self_wake(tmp_path):
    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        observed = []
        store.fulfillment.verification_changed = lambda: observed.append(store.db.in_transaction)
        with store.transaction():
            store.db.execute("UPDATE asks SET body=body WHERE ask_id='abcd'")
            assert observed == []
        assert observed == [False]
        p.verify_pass(store)
        assert observed == [False]
        assert store.capacity_count() == 0
        with pytest.raises(RuntimeError), store.transaction():
            store.db.execute("UPDATE asks SET body='rolled back'")
            raise RuntimeError("abort")
        assert observed == [False]
    finally:
        store.close()


def test_unmigrated_open_does_not_create_projection(tmp_path):
    from heterodyne.admind.store import Store

    store = Store(tmp_path / "unmigrated.db")
    try:
        seed(store, projection=False)
        assert store.fulfillment.view("abcd")["binding"] is not None
        assert (
            store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='fulfillment_projection_meta'"
            ).fetchone()
            is None
        )
        with pytest.raises(f.Invalid, match="fulfillment-migration-required"):
            apply(store)
        assert store.capacity_count() == 1
    finally:
        store.close()


def test_migration_cli_contains_sqlite_failures(tmp_path, monkeypatch, capsys):
    import argparse
    import sqlite3

    from fakes.settings import make_settings

    from heterodyne.admind import fulfillment_cli

    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("database locked")

    monkeypatch.setattr(fulfillment_cli, "migration", unavailable)
    args = argparse.Namespace(fulfillment_command="migrate", apply=False)
    assert fulfillment_cli.command(make_settings(tmp_path), args) == 1
    assert capsys.readouterr().err == "admind: invalid fulfillment input\n"


@pytest.mark.parametrize("damage", ["missing-ask", "malformed-receipt"])
def test_get_unavailable_preserves_readable_receipt_and_stops_reconcile(tmp_path, monkeypatch, damage):
    import argparse

    from fakes.settings import make_settings
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl
    from heterodyne.admind import fulfillment_cli as cli

    unit = Unit(tmp_path)
    try:
        monkeypatch.setattr(f, "ENABLED", True)
        p.migrate(unit.store, apply=True)
        seed(unit.store)
        apply(unit.store)
        original = unit.store.fulfillment.stored("abcd")
        manifest = tmp_path / "receipts.json"
        manifest.write_bytes(f.canonical([f.builtins(completion(unit.store))]))
        if damage == "missing-ask":
            unit.store.db.execute("DELETE FROM asks WHERE ask_id='abcd'")
        else:
            unit.store.db.execute("DROP TRIGGER fq2_receipt_immutable")
            unit.store.db.execute("UPDATE ask_fulfillments SET receipt='[]'")
            unit.store.db.execute(p.TRIGGERS[7])
        reply = run(unit.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123, os.getuid(), os.getgid())))
        assert (reply.result, reply.message) == ("unavailable", "binding-unavailable")
        calls = []

        def send(_settings, request):
            calls.append(type(request).__name__)
            return reply

        monkeypatch.setattr(cli, "send", send)
        result = cli.reconcile(
            make_settings(tmp_path),
            argparse.Namespace(receipts=manifest, limit=20, apply=True, continuation=None),
        )
        assert result["results"] == [{"ask_id": "abcd", "outcome": "unavailable"}]
        assert str(result["continuation"]).endswith(":0")
        assert calls == ["Get"]
        if damage == "missing-ask":
            assert reply.view["receipt"] == original
            assert reply.view["binding"] is None
            assert reply.view["eligible"] is False
    finally:
        unit.store.close()


def test_verifier_runs_with_producers_disabled_and_wakes_without_poll_delay(tmp_path, monkeypatch):
    import asyncio

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        monkeypatch.setattr(f, "ENABLED", False)
        store.db.execute("UPDATE asks SET body=body")
        completed = []
        original = p.verify_pass

        def checked(s):
            result = original(s)
            completed.append(result)
            return result

        monkeypatch.setattr(p, "verify_pass", checked)

        async def exercise():
            task = asyncio.create_task(p.worker(store))
            try:
                async with asyncio.timeout(5):
                    while not completed:
                        await asyncio.sleep(0.001)
                    assert store.capacity_count() == 0
                    with store.transaction():
                        store.db.execute("UPDATE asks SET body='new instructions'")
                    while len(completed) < 2:
                        await asyncio.sleep(0.001)
                    assert store.capacity_count() == 1
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        asyncio.run(exercise())
    finally:
        store.close()


def test_epoch_cursor_wraps_and_never_skips_unprocessed_prefix(tmp_path, monkeypatch):
    store = migrated(tmp_path)
    try:
        names = ("abcd", "bcde", "cdef", "defg")
        for i, name in enumerate(names):
            seed(store, name)
            apply(store, msgspec.structs.replace(completion(store, name), operation_id=f"op:{i}"))
        store.db.execute("DELETE FROM fulfillment_verifications")
        store.db.execute("INSERT INTO fulfillment_dirty VALUES ('defg',0)")
        # Two maximum-sized units fit, but the third must wait. Epoch and dirty each get a slot.
        monkeypatch.setattr(p, "measure", lambda *_args: 9 * 1024 * 1024)
        assert p.verify_pass(store) == 2
        assert store.db.execute("SELECT scan_cursor FROM fulfillment_projection_meta").fetchone() == ("abcd",)
        assert store.db.execute(
            "SELECT state FROM fulfillment_verifications WHERE ask_id='defg'"
        ).fetchone() == ("valid",)
        assert p.verify_pass(store) == 2
        assert store.capacity_count() == 0
        store.db.execute("DELETE FROM fulfillment_verifications WHERE ask_id='abcd'")
        store.db.execute("UPDATE fulfillment_projection_meta SET scan_cursor='zzzz'")
        assert p.verify_pass(store) == 1
        assert store.capacity_count() == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "revision", ["07637745fe75dc614109a0b8ba72d3489f262a97", "a73e91d367bd626556371f33a46e25f003243c6e"]
)
def test_real_legacy_store_methods_and_rebuild_cannot_restore_current_stamp(tmp_path, revision):
    import io
    import subprocess
    import sys
    import tarfile

    root = tmp_path / "legacy"
    root.mkdir()
    data = subprocess.run(["git", "archive", revision, "src"], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(root, filter="data")
    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        before = store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone()
        code = """
import sys
from pathlib import Path
from heterodyne.admind.store import Store, AskRow
s=Store(Path(sys.argv[1]))
s.insert_ask(AskRow('bcde','question','legacy','title','body',None,None,None,None,False,1,'open',None,None,'2026-10-16T00:00:00+00:00','2026-10-16T00:00:00+00:00'),None)
s.add_answer('bcde','answer','Liam','f'*64,'A','A')
s.set_ask('bcde','answered')
assert s.ask('bcde').status=='answered' and len(s.answers('bcde'))==1
if hasattr(s,'fulfillment'):
    changes = [(s.add_answer,('abcd','answer','Liam','e'*64,'B','B')),
               (s.set_ask,('abcd','cancelled'))]
    for method,args in changes:
        try: method(*args)
        except ValueError: pass
        else: raise AssertionError('legacy protected mutation succeeded')
    s.db.execute("UPDATE asks SET body='old writer' WHERE ask_id='abcd'")
    s.fulfillment.rebuild()
    s.fulfillment.refresh('abcd')
else:
    s.add_answer('abcd','answer','Liam','e'*64,'B','B')
    s.set_ask('abcd','cancelled')
s.close()
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.path)],
            env=dict(os.environ, PYTHONPATH=str(root / "src")),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert store.capacity_count() == 2
        assert store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone() == before
        assert store.db.execute("SELECT COUNT(*) FROM pragma_table_info('ask_fulfillments')").fetchone() == (
            8,
        )
        p.verify_pass(store)
        assert store.fulfillment.view("abcd")["lifecycle"] == "conflict"
    finally:
        store.close()


def test_migration_plan_distinguishes_required_from_already_migrated(tmp_path):
    from heterodyne.admind.store import Store

    store = Store(tmp_path / "store.db")
    try:
        assert p.migrate(store)["migration_required"] is True
        assert p.migrate(store, apply=True)["changed"] is True
        plan = p.migrate(store)
        assert plan["migration_required"] is False
        assert plan["changed"] is False
    finally:
        store.close()
