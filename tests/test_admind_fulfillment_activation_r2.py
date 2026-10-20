"""Second HIGH regressions against disposable SQLite and synthetic daemon fixtures only."""

import asyncio
import os

import msgspec
import pytest
from test_admind_fulfillment import apply, completion, declaration, seed
from test_admind_fulfillment_activation import migrated
from test_admind_r1 import Unit, run

from heterodyne.admind import ctl
from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_cli as cli
from heterodyne.admind import fulfillment_projection as p


def test_notification_failure_cannot_reclassify_committed_transaction(tmp_path):
    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)

        def failed():
            raise RuntimeError("notification failed after commit")

        store.fulfillment.verification_changed = failed
        with store.transaction():
            store.db.execute("UPDATE asks SET body='committed'")
        saved = store.ask("abcd")
        assert saved is not None
        assert saved.body == "committed"
        assert store._depth == 0
        assert not store.db.in_transaction
    finally:
        store.close()


@pytest.mark.parametrize("failed,delay", [(False, 1), (True, 30)])
def test_notifications_cannot_shorten_worker_minimum_delay(tmp_path, monkeypatch, failed, delay):
    store = migrated(tmp_path)
    sleeps = []
    try:

        def verify(_store):
            if failed:
                raise KeyError("malformed cursor")
            return 1

        async def sleep(seconds):
            sleeps.append(seconds)
            raise asyncio.CancelledError

        async def wait_for(awaitable, timeout):
            awaitable.close()
            raise asyncio.CancelledError

        monkeypatch.setattr(p, "verify_pass", verify)
        monkeypatch.setattr(p.asyncio, "sleep", sleep)
        monkeypatch.setattr(p.asyncio, "wait_for", wait_for)

        async def exercise():
            with pytest.raises(asyncio.CancelledError):
                await p.worker(store)

        asyncio.run(exercise())
        assert sleeps == [delay]
    finally:
        store.close()


def test_get_missing_remains_typed_refusal(tmp_path, monkeypatch):
    unit = Unit(tmp_path, fulfillment_enabled=True)
    try:
        p.migrate(unit.store, apply=True)
        reply = run(unit.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123, os.getuid(), os.getgid())))
        assert (reply.result, reply.message) == ("refused", "missing")
    finally:
        unit.store.close()


@pytest.mark.parametrize("matching", [True, False])
def test_readable_receipt_precedes_unavailable_binding(tmp_path, matching):
    store = migrated(tmp_path)
    try:
        seed(store)
        receipt = completion(store)
        apply(store, receipt)
        view = store.fulfillment.view("abcd")
        view["binding"] = None
        if not matching:
            receipt = msgspec.structs.replace(receipt, terminal_result_sha256="e" * 64)
        assert cli.plan(receipt, f.Reply("unavailable", "binding-unavailable", view)) == (
            "already-applied" if matching else "conflict"
        )
    finally:
        store.close()


@pytest.mark.parametrize("new_owner", [None, "b" * 64])
def test_replacement_requires_new_consumed_owner_before_retiring_old(tmp_path, new_owner):
    store = migrated(tmp_path)
    try:
        seed(store)
        first_mid = store.answers("abcd")[0].message_id
        store.claim_inbound(first_mid, sender_key="a" * 64)
        store.fulfillment.notice_authority = lambda: {"a" * 64}
        store.add_answer("abcd", "answer", "Other", "e" * 64, "B", "B")
        first_declaration = declaration(store)
        first_declaration = msgspec.structs.replace(
            first_declaration,
            binding=msgspec.structs.replace(first_declaration.binding, consumed_answers=[first_mid]),
        )
        first = store.fulfillment.propose(
            f.Propose("abcd", first_declaration), uid=os.getuid(), chunk_chars=1800
        )
        if new_owner:
            store.claim_inbound("e" * 64, sender_key=new_owner)
        next_declaration = declaration(store)
        next_declaration = msgspec.structs.replace(
            next_declaration,
            binding=msgspec.structs.replace(next_declaration.binding, consumed_answers=["e" * 64]),
        )
        assert first.view is not None
        with pytest.raises(f.Invalid, match="notice-owner-unavailable"):
            store.fulfillment.propose(
                f.Propose("abcd", next_declaration, str(first.view["digest"])),
                uid=os.getuid(),
                chunk_chars=1800,
            )
        assert store.db.execute("SELECT status FROM ask_fulfillment_intents").fetchall() == [("pending",)]
    finally:
        store.close()


def test_get_notice_observation_joins_view_read_snapshot(tmp_path, monkeypatch):
    unit = Unit(tmp_path, fulfillment_enabled=True)
    try:
        p.migrate(unit.store, apply=True)
        seed(unit.store)
        unit.store.fulfillment.propose(
            f.Propose("abcd", declaration(unit.store)), uid=os.getuid(), chunk_chars=1800
        )
        original = unit.store.fulfillment.notice_state
        observations = []

        def observed(intent, **kwargs):
            observations.append(unit.store.db.in_transaction)
            return original(intent, **kwargs)

        monkeypatch.setattr(unit.store.fulfillment, "notice_state", observed)
        reply = run(unit.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123, os.getuid(), os.getgid())))
        assert reply.result == "ok"
        assert observations == [True]
    finally:
        unit.store.close()


def test_projection_damage_is_unavailable_without_losing_view(tmp_path, monkeypatch):
    unit = Unit(tmp_path, fulfillment_enabled=True)
    try:
        p.migrate(unit.store, apply=True)
        seed(unit.store)
        unit.store.db.execute("DROP TRIGGER fq2_asks_update")
        reply = run(unit.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123, os.getuid(), os.getgid())))
        assert (reply.result, reply.message) == ("unavailable", "fulfillment-projection-unavailable")
        assert reply.view is not None
        assert reply.view["eligible"] is False
    finally:
        unit.store.close()


def test_non_get_programming_failure_is_not_binding_refusal(tmp_path, monkeypatch):
    unit = Unit(tmp_path, fulfillment_enabled=True)
    try:
        p.migrate(unit.store, apply=True)
        seed(unit.store)

        def failed(*args, **kwargs):
            raise TypeError("programming failure")

        monkeypatch.setattr(unit.store.fulfillment, "apply", failed)
        with pytest.raises(TypeError, match="programming failure"):
            run(
                unit.daemon.on_fulfillment(
                    f.Apply(completion(unit.store)), ctl.Peer(123, os.getuid(), os.getgid())
                )
            )
    finally:
        unit.store.close()


@pytest.mark.parametrize("sql", ["UPDATE asks SET body=body", "UPDATE ask_answers SET text=text"])
def test_distinct_trigger_target_bumped_once(tmp_path, sql):
    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        before = store.db.execute("SELECT generation FROM fulfillment_generations").fetchone()[0]
        store.db.execute(sql)
        after = store.db.execute("SELECT generation FROM fulfillment_generations").fetchone()[0]
        assert after == before + 1
    finally:
        store.close()


@pytest.mark.parametrize("rollback", [False, True])
def test_only_committed_dirty_changes_notify_without_postcommit_queries(tmp_path, rollback):
    store = migrated(tmp_path)
    try:
        seed(store)
        apply(store)
        notifications = []
        store.fulfillment.verification_changed = lambda: notifications.append("wake")
        statements = []
        store.db.set_trace_callback(statements.append)
        with store.transaction():
            store.set("unrelated", "committed with old dirty backlog")
        assert notifications == []
        assert statements[-1] == "COMMIT"
        if rollback:
            with pytest.raises(ValueError, match="abort"):
                with store.transaction():
                    store.db.execute("UPDATE asks SET body=body")
                    raise ValueError("abort")
            assert notifications == []
            assert statements[-1] == "ROLLBACK"
        else:
            with store.transaction():
                store.db.execute("UPDATE asks SET body=body")
            assert notifications == ["wake"]
            assert statements[-1] == "COMMIT"
    finally:
        store.close()


def test_reopened_migrated_connection_notifies_new_dirty_write(tmp_path):
    from heterodyne.admind.store import Store

    store = migrated(tmp_path)
    seed(store)
    apply(store)
    path = store.path
    store.close()
    store = Store(path)
    try:
        notifications = []
        store.fulfillment.verification_changed = lambda: notifications.append("wake")
        with store.transaction():
            store.db.execute("UPDATE asks SET body=body")
        assert notifications == ["wake"]
    finally:
        store.close()


def test_unmigrated_get_holds_new_binding_without_migrating(tmp_path, monkeypatch):
    unit = Unit(tmp_path, fulfillment_enabled=True)
    try:
        seed(unit.store)
        reply = run(unit.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123, os.getuid(), os.getgid())))
        assert (reply.result, reply.message) == ("unavailable", "fulfillment-migration-required")
        assert (
            unit.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='fulfillment_projection_meta'"
            ).fetchone()
            is None
        )
    finally:
        unit.store.close()
