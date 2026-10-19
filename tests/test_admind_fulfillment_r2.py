"""R2 regression cases: private SQLite and fake transport only."""

import argparse
import hashlib
import os
import sqlite3
from typing import cast

import msgspec
import pytest
from fakes.settings import make_settings
from test_admind_fulfillment import apply, completion, declaration, deliver, seed

from heterodyne.admind import asks, fulfillment_cli
from heterodyne.admind import fulfillment as f
from heterodyne.admind.store import Store

pytest_plugins = ("test_admind_fulfillment",)


def propose(store):
    return store.fulfillment.propose(f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=3500)


def test_confirmation_notice_inherits_durable_consumed_answer_owner(store):
    seed(store, projection=True)
    owner = "b" * 64
    store.claim_inbound(hashlib.sha256(b"abcd").hexdigest(), sender_key=owner)
    propose(store)
    rows = store.pending()
    assert rows and all(row.ownership_kind == "request" for row in rows)
    assert all(store.row_owners(row.seq) == (owner,) for row in rows)


def test_unproven_consumed_answer_is_quarantined_not_system(store):
    seed(store, projection=True)
    propose(store)
    assert all(row.ownership_kind == "unknown" for row in store.pending())


def test_consumption_has_no_false_retirement_tombstone(store):
    seed(store, projection=True, owned=True)
    pending = propose(store)
    intent = pending.view["intent_id"]
    target = deliver(store, intent)
    assert store.fulfillment.confirm(target, principal="Liam", message_id="e" * 64).result == "applied"
    assert store.db.execute("SELECT status,reason FROM ask_fulfillment_intents").fetchall() == [
        ("consumed", None)
    ]
    assert store.db.execute("SELECT disposition FROM ask_fulfillment_audit").fetchall() == [("applied",)]


def test_read_only_view_succeeds_during_foreign_writer(store):
    seed(store, projection=True)
    store.db.execute("PRAGMA busy_timeout=10")
    with sqlite3.connect(store.path, isolation_level=None) as peer:
        peer.execute("BEGIN IMMEDIATE")
        try:
            assert store.fulfillment.view("abcd")["eligible"] is True
        finally:
            peer.execute("ROLLBACK")


def test_current_schema_open_does_not_rehash_and_startup_rebuild_detects_old_writer(tmp_path, monkeypatch):
    path = tmp_path / "state" / "db"
    source = Store(path)
    seed(source, projection=True)
    apply(source)
    source.close()
    with sqlite3.connect(path) as old:
        old.execute("UPDATE asks SET body='old writer changed immutable context'")
    calls = []
    original = f.Lifecycle.refresh
    monkeypatch.setattr(f.Lifecycle, "refresh", lambda self, ask_id: calls.append(ask_id))
    reopened = Store(path)
    try:
        assert calls == []
        monkeypatch.setattr(f.Lifecycle, "refresh", original)
        reopened.fulfillment.rebuild()
        from heterodyne.admind.fulfillment_projection import verify_pass
        verify_pass(reopened)  # Explicit bounded worker step after startup validation.
        assert reopened.fulfillment.view("abcd")["lifecycle"] == "conflict"
        assert reopened.capacity_count() == 1
    finally:
        reopened.close()


def test_cli_schema_failure_is_fixed_state_error(tmp_path, monkeypatch):
    from heterodyne.admind import cli

    settings = make_settings(tmp_path)
    monkeypatch.setattr(cli, "_resolve", lambda: settings)

    def incompatible(_path):
        raise f.Invalid("private-schema-sentinel")

    monkeypatch.setattr(cli, "Store", incompatible)
    with pytest.raises(cli.StateDirError) as caught:
        cli._load()
    assert "private-schema-sentinel" not in str(caught.value)


def test_reconcile_reports_new_commit_then_existing_receipt(store, tmp_path, monkeypatch):
    seed(store, projection=True)
    path = tmp_path / "receipts.json"
    path.write_bytes(f.canonical([f.builtins(completion(store))]))
    args = argparse.Namespace(receipts=path, limit=20, apply=True, continuation=None)

    def send(_settings, req):
        return (
            f.Reply("ok", "view", store.fulfillment.view(req.ask_id))
            if isinstance(req, f.Get)
            else apply(store, req.receipt)
        )

    monkeypatch.setattr(fulfillment_cli, "send", send)
    for expected in ("applied", "already-applied"):
        result = fulfillment_cli.reconcile(make_settings(tmp_path), args)
        outcomes = cast(list[dict[str, object]], result["results"])
        assert outcomes[0]["outcome"] == expected



def test_id_validation_uses_shared_grammar(monkeypatch):
    monkeypatch.setattr(asks, "ID_LENGTH", 5)
    monkeypatch.setattr(asks, "ID_ALPHABET", "x")
    f.check_id("xxxxx")
    with pytest.raises(f.Invalid):
        f.check_id("abcd")


@pytest.mark.parametrize("fault", ["refusal-store", "programmer-error"])
def test_fulfill_refusal_store_fault_is_recoverable_and_bug_not_called_store_outage(
    tmp_path, monkeypatch, fault
):
    from fakes.settings import OPERATOR_HEX
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    u = Unit(tmp_path)
    try:
        monkeypatch.setattr(f, "ENABLED", True)

        def confirm(*_args, **_kwargs):
            if fault == "programmer-error":
                raise KeyError("private-sentinel")
            raise f.Invalid("stale-intent")

        monkeypatch.setattr(u.store.fulfillment, "confirm", confirm)
        finish = u.daemon.finish

        def finish_or_busy(mid, status, text, tag):
            if text == "stale-intent":
                raise sqlite3.OperationalError("private-sentinel")
            finish(mid, status, text, tag)

        monkeypatch.setattr(u.daemon, "finish", finish_or_busy)
        event = msgspec.structs.replace(
            inbound("!fulfill", "f" * 64, OPERATOR_HEX), reply_to=ReplyTo("notice")
        )
        run(u.daemon.on_message(event))
        text = "\n".join(row.text for row in u.store.pending())
        assert "private-sentinel" not in text
        assert (
            "Fulfillment unavailable: store unavailable."
            if fault == "refusal-store"
            else "Fulfillment unavailable: internal error."
        ) in text
    finally:
        u.store.close()


def test_legacy_socket_duplicate_keys_unchanged_but_new_union_strict(tmp_path):
    import asyncio

    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    u = Unit(tmp_path)
    seed(u.store, projection=True)

    async def scenario():
        server = ctl.JsonSocketServer(
            tmp_path / "ask.sock",
            u.daemon.on_ask_wire,
            u.audit,
            kind="ask",
            max_request=asks.MAX_REQUEST,
            max_reply=asks.MAX_REPLY,
            request_type=asks.AskRequest | f.Request,
            check=u.daemon.check_ask_wire,
            refused=asks.refused,
            failed=asks.failed,
            describe=lambda req: {"op": type(req).__name__},
        )
        await server.start()
        try:
            for tag in ("get", "fulfillment.get"):
                reader, writer = await asyncio.open_unix_connection(server.path)
                writer.write(f'{{"type":"{tag}","ask_id":"efgh","ask_id":"abcd"}}\n'.encode())
                await writer.drain()
                reply = msgspec.json.decode(await reader.readline())
                writer.close()
                await writer.wait_closed()
                if tag == "get":
                    assert reply["result"] == "ok"
                else:
                    assert reply["result"] == "refused"
        finally:
            await server.close()

    try:
        run(scenario())
    finally:
        u.store.close()


def test_quoted_answer_revocation_uses_actual_daemon_guard(tmp_path):
    from fakes.settings import OPERATOR_HEX
    from test_admind_r1 import Unit

    u = Unit(tmp_path)
    try:
        seed(u.store, projection=True)
        u.store.claim_inbound(hashlib.sha256(b"abcd").hexdigest(), sender_key=OPERATOR_HEX)
        propose(u.store)
        row = u.store.pending()[0]
        assert u.daemon.prepare_delivery(row)
        u.daemon.operators.clear()
        assert not u.daemon.prepare_delivery(row)
        assert (
            u.store.db.execute("SELECT status FROM outbox WHERE seq=?", (row.seq,)).fetchone()[0]
            == "suppressed"
        )
    finally:
        u.store.close()
