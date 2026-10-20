"""Activation contracts against private SQLite only; no live migration or transport."""

import os
from typing import cast

import pytest
from test_admind_fulfillment import apply, completion, seed

from heterodyne.admind import asks
from heterodyne.admind import fulfillment as f
from heterodyne.admind.store import Store


def test_nested_writer_cannot_join_reader(tmp_path):
    store = Store(tmp_path / "state" / "db")
    try:
        with store.transaction(read_only=True):
            with pytest.raises(ValueError, match="read-only-transaction"):
                with store.transaction():
                    store.set("forbidden", "yes")
        assert store.get("forbidden") is None
        with store.transaction():
            store.set("allowed", "yes")
    finally:
        store.close()


def test_duplicate_receipt_identity_survives_invalid_projection(tmp_path):
    store = migrated(tmp_path)
    try:
        seed(store)
        receipt = completion(store)
        apply(store, receipt)
        store.db.execute("UPDATE ask_fulfillments SET valid=0")
        assert apply(store, receipt).result == "already-applied"
    finally:
        store.close()


def migrated(tmp_path):
    from heterodyne.admind.fulfillment_projection import migrate

    store = Store(tmp_path / "state" / "db")
    migrate(store, apply=True)
    return store


def test_old_sqlite_writer_invalidates_without_new_daemon(tmp_path):
    import sqlite3

    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        assert store.capacity_count() == 0
        assert verify_pass(store) == 0
        assert store.capacity_count() == 0
        before = store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone()
        with sqlite3.connect(store.path) as old:
            old.execute("UPDATE asks SET body='changed by old binary'")
        assert store.capacity_count() == 1
        assert store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone() == before
        verify_pass(store)
        assert store.db.execute("SELECT state FROM fulfillment_verifications").fetchone() == ("conflict",)
    finally:
        store.close()


def test_missing_trigger_never_suppresses_capacity(tmp_path):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        verify_pass(store)
        assert store.capacity_count() == 0
        store.db.execute("DROP TRIGGER fq2_answers_insert")
        assert store.capacity_count() == 1
        with pytest.raises(f.Invalid, match="projection-unavailable"):
            verify_pass(store)
    finally:
        store.close()


def test_migration_explicit_atomic_and_idempotent(tmp_path):
    from heterodyne.admind.fulfillment_projection import migrate

    store = Store(tmp_path / "state" / "db")
    try:
        seed(store)
        with pytest.raises(f.Invalid, match="migration-required"):
            apply(store)
        before = store.db.execute("SELECT sql FROM sqlite_master WHERE name='ask_fulfillments'").fetchone()
        assert migrate(store)["changed"] is False
        assert (
            store.db.execute(
                "SELECT name FROM sqlite_master WHERE name='fulfillment_projection_meta'"
            ).fetchone()
            is None
        )
        assert migrate(store, apply=True)["changed"] is True
        assert migrate(store, apply=True)["changed"] is False
        assert (
            store.db.execute("SELECT sql FROM sqlite_master WHERE name='ask_fulfillments'").fetchone()
            == before
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE asks SET title='changed'",
        "UPDATE asks SET status='cancelled'",
        "UPDATE ask_answers SET text='changed'",
        "DELETE FROM ask_answers",
        "INSERT INTO ask_answers SELECT ask_id,seq+1,kind,operator,'new-id',text,at,choice FROM ask_answers",
        "DELETE FROM asks",
    ],
)
def test_every_binding_writer_immediately_restores_capacity(tmp_path, sql):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        verify_pass(store)
        receipt = store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone()
        assert store.capacity_count() == 0
        store.db.execute(sql)
        # Orphan receipts stay diagnostic-only; every still-existing ask is conservative.
        assert store.capacity_count() == (0 if sql == "DELETE FROM asks" else 1)
        assert store.db.execute("SELECT valid FROM ask_fulfillments").fetchone() == (0,)
        assert store.db.execute("SELECT receipt FROM ask_fulfillments").fetchone() == receipt
    finally:
        store.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE ask_fulfillments SET receipt='changed'",
        "DELETE FROM ask_fulfillments",
        "UPDATE kv SET value='changed' WHERE key='ask_store_id'",
        "DELETE FROM kv WHERE key='ask_store_id'",
        "INSERT OR REPLACE INTO kv VALUES ('ask_store_id','changed')",
    ],
)
def test_immutable_history_and_store_identity(tmp_path, sql):
    import sqlite3

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        before = store.db.execute("SELECT * FROM ask_fulfillments").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="fulfillment-history-protected"):
            store.db.execute(sql)
        assert store.db.execute("SELECT * FROM ask_fulfillments").fetchall() == before
    finally:
        store.close()


def test_excluded_activity_does_not_dirty_generation(tmp_path):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        verify_pass(store)
        before = store.db.execute("SELECT * FROM fulfillment_generations").fetchall()
        store.db.execute("UPDATE asks SET updated_at='activity only',card_parts=2")
        assert store.db.execute("SELECT * FROM fulfillment_generations").fetchall() == before
        assert store.capacity_count() == 0
    finally:
        store.close()


def test_worker_pass_bounded_and_progresses(tmp_path):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        for i in range(45):
            name = (
                "aa"
                + asks.ID_ALPHABET[i // len(asks.ID_ALPHABET)]
                + asks.ID_ALPHABET[i % len(asks.ID_ALPHABET)]
            )
            seed(store, name)
            apply(
                store, __import__("msgspec").structs.replace(completion(store, name), operation_id=f"op:{i}")
            )
        store.db.execute("UPDATE asks SET body=body")  # Genuine legacy trigger invalidation backlog.
        counts = [verify_pass(store) for _ in range(8)]
        assert max(counts) <= 20
        assert sum(counts) == 45
        assert store.capacity_count() == 0
        assert store.db.execute("SELECT COUNT(*) FROM fulfillment_dirty").fetchone() == (0,)
    finally:
        store.close()


def test_actual_legacy_source_writers(tmp_path):
    import io
    import subprocess
    import sys
    import tarfile

    from heterodyne.admind.fulfillment_projection import verify_pass

    for revision in ("07637745fe75dc614109a0b8ba72d3489f262a97", "a73e91d367bd626556371f33a46e25f003243c6e"):
        root = tmp_path / revision
        root.mkdir()
        data = subprocess.run(["git", "archive", revision, "src"], check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(root, filter="data")
        store = migrated(root)
        try:
            seed(store)
            apply(store)
            verify_pass(store)
            assert store.capacity_count() == 0
            env = dict(os.environ, PYTHONPATH=str(root / "src"))
            code = """
import sys
from heterodyne.admind.store import Store
s=Store(__import__('pathlib').Path(sys.argv[1]))
s.db.execute("UPDATE asks SET body='historical binary mutation'")
s.close()
"""
            result = subprocess.run(
                [sys.executable, "-c", code, str(store.path)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert store.capacity_count() == 1
            assert store.db.execute(
                "SELECT COUNT(*) FROM pragma_table_info('ask_fulfillments')"
            ).fetchone() == (8,)
        finally:
            store.close()


def test_actual_old_positional_receipt_insert(tmp_path):
    import io
    import subprocess
    import sys
    import tarfile

    import msgspec

    from heterodyne.admind.fulfillment_projection import verify_pass

    revision = "a73e91d367bd626556371f33a46e25f003243c6e"
    root = tmp_path / "old"
    root.mkdir()
    data = subprocess.run(["git", "archive", revision, "src"], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(root, filter="data")
    store = migrated(tmp_path)
    try:
        seed(store)
        payload = tmp_path / "completion.json"
        payload.write_bytes(msgspec.json.encode(completion(store)))
        code = """
import os, sys, msgspec
from pathlib import Path
from heterodyne.admind.store import Store
from heterodyne.admind import asks
from heterodyne.admind import fulfillment as f
s=Store(Path(sys.argv[1]))
r=msgspec.json.decode(Path(sys.argv[2]).read_bytes(),type=f.Completion)
assert s.fulfillment.apply(r,uid=os.getuid(),pid=os.getpid()).result=='applied'
s.close()
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.path), str(payload)],
            env=dict(os.environ, PYTHONPATH=str(root / "src")),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert store.capacity_count() == 1
        assert verify_pass(store) == 1
        assert store.capacity_count() == 0
    finally:
        store.close()


def test_notice_unknown_disposal_and_replacement_boundaries(tmp_path):
    from test_admind_fulfillment import declaration

    store = migrated(tmp_path)
    try:
        seed(store)
        req = f.Propose("abcd", declaration(store))
        reply = store.fulfillment.propose(req, uid=os.getuid(), chunk_chars=1800)
        assert reply.message == "notice-quarantined"
        assert reply.view is not None
        entry = cast(list[dict[str, object]], store.fulfillment.inspect()["entries"])[0]
        assert entry["state"] == "quarantined"
        assert entry["actions"] == ["dispose", "controller-receipt", "hold"]
        assert (
            store.fulfillment.propose(req, uid=os.getuid(), chunk_chars=1800).message == "notice-quarantined"
        )
        import msgspec

        changed = msgspec.structs.replace(req.declaration, description="Different completed work.")
        with pytest.raises(f.Invalid, match="notice-owner-unavailable"):
            store.fulfillment.propose(
                f.Propose("abcd", changed, str(reply.view["digest"])), uid=os.getuid(), chunk_chars=1800
            )
        with store.transaction():
            result = store.fulfillment.retire("abcd", str(reply.view["digest"]), "operator-withdrawal")
        assert result.result == "retired"
        assert store.fulfillment.inspect()["entries"] == []
    finally:
        store.close()


def test_notice_exact_delivery_and_fresh_policy(tmp_path):
    from test_admind_fulfillment import declaration

    store = migrated(tmp_path)
    try:
        seed(store)
        mid = store.answers("abcd")[0].message_id
        store.claim_inbound(mid, sender_key="a" * 64)
        store.fulfillment.notice_authority = lambda: {"a" * 64}
        result = store.fulfillment.propose(
            f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=1800
        )
        assert result.message == "notice-pending"
        assert result.view is not None
        intent = str(result.view["intent_id"])
        store.db.execute("UPDATE outbox SET status='sent',message_id='delivery-'||seq")
        assert store.fulfillment.notice_state(intent) == ("delivered", "delivery-complete")
        store.fulfillment.notice_authority = lambda: set()
        assert store.fulfillment.notice_state(intent) == ("suppressed", "requester-revoked")
        store.fulfillment.notice_authority = lambda: None
        assert store.fulfillment.notice_state(intent) == ("unavailable", "delivery-unresolved")
    finally:
        store.close()


@pytest.mark.parametrize(
    "damage",
    [
        "DROP INDEX fulfillment_verification_epoch",
        "UPDATE fulfillment_projection_meta SET scheme='unknown'",
        "DELETE FROM fulfillment_projection_meta",
    ],
)
def test_damaged_projection_counts_receipts_and_approvals_conservatively(tmp_path, damage):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        seed(store, "bcde", kind="approval", status="open")
        verify_pass(store)
        assert store.capacity_count() == 1
        store.db.execute(damage)
        assert store.capacity_count() == 2
        assert {row.ask_id for row in store.capacity_asks()} == {"abcd", "bcde"}
    finally:
        store.close()


def test_answer_replace_invalidates_both_unique_key_victims(tmp_path):
    import msgspec

    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        for i, name in enumerate(("abcd", "bcde")):
            seed(store, name)
            apply(store, msgspec.structs.replace(completion(store, name), operation_id=f"operation:{i}"))
        verify_pass(store)
        first = store.answers("abcd")[0]
        # The destination (ask_id,seq) and message_id identify different old rows.
        store.db.execute(
            "INSERT OR REPLACE INTO ask_answers "
            "SELECT 'bcde',seq,kind,operator,message_id,text,at,choice "
            "FROM ask_answers WHERE message_id=?",
            (first.message_id,),
        )
        assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillments WHERE valid=1").fetchone() == (0,)
        assert store.capacity_count() == 2
    finally:
        store.close()


def test_generation_overflow_aborts_source_mutation_atomically(tmp_path):
    import sqlite3

    from heterodyne.admind.fulfillment_projection import MAX_GENERATION

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        store.db.execute("UPDATE fulfillment_generations SET generation=?", (MAX_GENERATION,))
        before = store.db.execute("SELECT body FROM asks").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute("UPDATE asks SET body='not committed'")
        assert store.db.execute("SELECT body FROM asks").fetchone() == before
    finally:
        store.close()


def test_oversize_answer_is_stamped_once_and_never_hides_capacity(tmp_path):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        store.db.execute("UPDATE ask_answers SET text=?", ("x" * 8388609,))
        assert verify_pass(store) == 1
        assert store.db.execute("SELECT state FROM fulfillment_verifications").fetchone() == ("too-large",)
        assert verify_pass(store) == 0
        assert store.capacity_count() == 1
    finally:
        store.close()


def test_verification_cas_never_stamps_concurrent_old_writer(tmp_path, monkeypatch):
    import sqlite3

    from heterodyne.admind import fulfillment_projection as projection

    store = migrated(tmp_path)
    original = projection.bounded_binding

    def changed(db, ask_id):
        result = original(db, ask_id)
        with sqlite3.connect(store.path) as other:
            other.execute("UPDATE asks SET body='concurrent old writer'")
        return result

    try:
        seed(store)
        apply(store)
        store.db.execute("UPDATE asks SET body=body")
        store.db.execute("DELETE FROM fulfillment_verifications")  # Preverification restart fixture.
        monkeypatch.setattr(projection, "bounded_binding", changed)
        projection.verify_pass(store)
        assert store.db.execute("SELECT COUNT(*) FROM fulfillment_verifications").fetchone() == (0,)
        assert store.db.execute("SELECT COUNT(*) FROM fulfillment_dirty").fetchone() == (1,)
        assert store.capacity_count() == 1
    finally:
        store.close()


def test_reconcile_retains_prefix_and_cursor_on_transport_unknown(tmp_path, monkeypatch):
    import argparse

    import msgspec
    from fakes.settings import make_settings

    from heterodyne.admind import ctl
    from heterodyne.admind import fulfillment_cli as cli

    store = migrated(tmp_path)
    try:
        seed(store)
        seed(store, "bcde")
        first = completion(store)
        invalid = msgspec.structs.replace(first, namespace="")
        second = completion(store, "bcde")
        manifest = tmp_path / "manifest.json"
        manifest.write_bytes(msgspec.json.encode([invalid, second]))
        seen = []

        def unavailable(settings, request):
            seen.append(request)
            raise ctl.CtlUnavailable("unknown")

        monkeypatch.setattr(cli, "send", unavailable)
        args = argparse.Namespace(receipts=manifest, limit=20, continuation=None, apply=True)
        result = cli.reconcile(make_settings(tmp_path), args)
        assert result["results"] == [
            {"ask_id": "abcd", "outcome": "incomplete"},
            {"ask_id": "bcde", "outcome": "unavailable"},
        ]
        assert str(result["continuation"]).endswith(":1")
        assert len(seen) == 1 and isinstance(seen[0], f.Get)
    finally:
        store.close()


@pytest.mark.parametrize("latched", [False, True])
def test_fulfill_missing_durable_principal_or_latch_uses_existing_denial(tmp_path, monkeypatch, latched):
    from test_admind_r1 import Unit, run

    from heterodyne.admind.fulfillment_projection import migrate

    unit = Unit(tmp_path, fulfillment_enabled=True)
    migrate(unit.store, apply=True)
    calls = []
    monkeypatch.setattr(unit.store.fulfillment, "confirm", lambda *a, **k: calls.append((a, k)))
    try:
        mid = unit.mid()
        unit.store.claim_inbound(mid, sender_key=None)
        if latched:
            unit.daemon.latch("fixture")
        run(unit.daemon.handle(mid, "!fulfill", "missing-notice"))
        assert calls == []
        assert unit.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (0,)
        assert not any("Fulfillment" in str(row) for row in unit.outbox())
    finally:
        unit.store.close()


def test_disabled_fulfill_reaches_legacy_command_parser(tmp_path, monkeypatch):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import commands

    unit = Unit(tmp_path)
    calls = []
    original = commands.parse

    def recording(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(commands, "parse", recording)
    try:
        mid = unit.mid()
        unit.store.claim_inbound(mid, sender_key=None)
        run(unit.daemon.handle(mid, "!fulfill"))
        assert calls == ["!fulfill"]
    finally:
        unit.store.close()


def test_inspect_limit_and_literal_prefix_do_not_accept_extra_chunks(tmp_path):
    from test_admind_fulfillment import declaration

    store = migrated(tmp_path)
    try:
        seed(store)
        mid = store.answers("abcd")[0].message_id
        store.claim_inbound(mid, sender_key="a" * 64)
        store.fulfillment.notice_authority = lambda: {"a" * 64}
        result = store.fulfillment.propose(
            f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=1800
        )
        assert result.view is not None
        intent = str(result.view["intent_id"])
        # An extra literal-prefix part is ambiguous, never a successful partial delivery.
        store.db.execute("UPDATE outbox SET key=key||'extra'")
        assert store.fulfillment.notice_state(intent) == ("unavailable", "delivery-unresolved")
        entries = cast(list[dict[str, object]], store.fulfillment.inspect()["entries"])
        assert entries[0]["state"] == "unavailable"
    finally:
        store.close()


def test_view_requires_current_verified_stamp_and_retains_orphan_receipt(tmp_path):
    from heterodyne.admind.fulfillment_projection import verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        lifecycle = store.fulfillment
        store.db.execute("UPDATE asks SET body=body")
        assert lifecycle.view("abcd")["lifecycle"] == "unverified"
        verify_pass(store)
        assert lifecycle.view("abcd")["lifecycle"] == "fulfilled"
        store.db.execute("DROP TRIGGER fq2_answers_insert")
        assert lifecycle.view("abcd")["lifecycle"] == "unverified"
        store.db.execute("DELETE FROM asks WHERE ask_id='abcd'")
        view = lifecycle.view("abcd")
        assert view["binding"] is None
        assert view["receipt"] is not None
        assert view["lifecycle"] == "unverified"
        assert view["eligible"] is False
    finally:
        store.close()


@pytest.mark.parametrize("latched", [False, True])
def test_authenticated_fulfill_records_real_principal_or_denies_latch(tmp_path, monkeypatch, latched):
    from fakes.settings import OPERATOR_HEX
    from test_admind_fulfillment import declaration
    from test_admind_r1 import Unit, run

    from heterodyne.admind.fulfillment_projection import migrate

    unit = Unit(tmp_path, fulfillment_enabled=True)
    migrate(unit.store, apply=True)
    try:
        seed(unit.store)
        answer = unit.store.answers("abcd")[0].message_id
        unit.store.claim_inbound(answer, sender_key=OPERATOR_HEX)
        unit.store.fulfillment.propose(
            f.Propose("abcd", declaration(unit.store)), uid=os.getuid(), chunk_chars=1800
        )
        unit.store.db.execute("UPDATE outbox SET status='sent',message_id='notice-'||seq")
        target = unit.store.db.execute("SELECT message_id FROM outbox ORDER BY seq LIMIT 1").fetchone()[0]
        mid = unit.mid()
        unit.store.claim_inbound(mid, sender_key=OPERATOR_HEX)
        unit.daemon.remember_sender(mid, OPERATOR_HEX)
        if latched:
            unit.daemon.latch("fixture")
        run(unit.daemon.handle(mid, "!fulfill", target))
        receipt = unit.store.fulfillment.stored("abcd")
        if latched:
            assert receipt is None
            assert unit.store.inbound_status(mid) == "dropped"
            assert unit.outbox() == []
        else:
            assert receipt is not None
            assert receipt["authority"] == {"principal": OPERATOR_HEX, "message_id": mid}
    finally:
        unit.store.close()


def test_worker_accepts_bounded_heavily_escaped_answer_inventory(tmp_path):
    import hashlib

    from heterodyne.admind.fulfillment_projection import measure, verify_pass

    store = migrated(tmp_path)
    try:
        seed(store)
        store.db.execute("UPDATE ask_answers SET text=?", ("\x01" * 16384,))
        for index in range(49):
            store.add_answer(
                "abcd", "answer", "Liam", hashlib.sha256(str(index).encode()).hexdigest(), "\x01" * 16384, "A"
            )
        apply(store)
        store.db.execute("UPDATE asks SET body=body")
        assert 4_900_000 < measure(store.db, "abcd") < 8_388_608
        assert verify_pass(store) == 1
        assert store.capacity_count() == 0
    finally:
        store.close()


def test_cli_migration_is_explicit_constructor_free_and_plan_read_only(tmp_path, monkeypatch):
    from fakes.settings import make_settings

    from heterodyne.admind import fulfillment_cli as cli
    from heterodyne.admind import store as store_module

    settings = make_settings(tmp_path)
    store = Store(settings.state_dir / "admind.db")
    seed(store)
    original = store.db.execute("SELECT sql FROM sqlite_master WHERE name='ask_fulfillments'").fetchone()
    store.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("migration must not construct Store")

    monkeypatch.setattr(store_module, "Store", forbidden)
    plan = cli.migration(settings, apply=False)
    assert plan["changed"] is False
    assert plan["before_schema_sha256"] == plan["after_schema_sha256"]
    applied = cli.migration(settings, apply=True)
    assert applied["changed"] is True
    assert cli.migration(settings, apply=True)["changed"] is False
    import sqlite3

    with sqlite3.connect(settings.state_dir / "admind.db") as db:
        assert (
            db.execute("SELECT sql FROM sqlite_master WHERE name='ask_fulfillments'").fetchone() == original
        )


def test_inspect_wire_closed_and_cli_migration_modes_exclusive():
    import argparse

    import msgspec

    from heterodyne.admind import fulfillment_cli as cli

    assert f.parse(b'{"type":"fulfillment.inspect"}', f.Inspect) == f.Inspect()
    with pytest.raises((f.Invalid, msgspec.ValidationError)):
        f.parse(b'{"type":"fulfillment.inspect","cursor":1}', f.Inspect)
    parser = argparse.ArgumentParser()
    cli.parser(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["migrate", "--plan", "--apply"])
    assert parser.parse_args(["inspect"]).fulfillment_command == "inspect"
