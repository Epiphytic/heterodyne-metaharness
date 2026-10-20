"""Offline regressions for the actual first HIGH fulfillment review."""

import os
import socket
import struct
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import cast

import msgspec
import pytest
from test_admind_fulfillment import apply, completion, declaration, seed

from heterodyne import platform
from heterodyne.admind import fulfillment as f

pytest_plugins = ("test_admind_fulfillment",)


def propose(store):
    return store.fulfillment.propose(f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=350)


def test_reaction_native_answer_reference_can_be_consumed(store):
    seed(store, projection=True)
    with store.transaction():
        store.db.execute("DELETE FROM ask_answers WHERE ask_id='abcd'")
        store.add_answer("abcd", "answer", "Liam", "r:" + "f" * 64, "👍", "A")
    assert apply(store).result == "applied"


@pytest.mark.parametrize("route", ["controller", "cancel", "supersede", "new-answer"])
def test_obsolete_pending_intent_releases_slot_and_retains_notice(store, route):
    seed(store, projection=True)
    intent = propose(store).view["intent_id"]
    with store.transaction():
        if route == "controller":
            apply(store)
        elif route == "new-answer":
            store.add_answer("abcd", "answer", "Liam", "f" * 64, "B", "B")
        else:
            store.set_ask("abcd", "cancelled" if route == "cancel" else "superseded")
    assert store.fulfillment.view("abcd")["pending"] is None
    assert (
        store.db.execute(
            "SELECT status FROM ask_fulfillment_intents WHERE intent_id=?", (intent,)
        ).fetchone()[0]
        == "retired"
    )
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM outbox WHERE key LIKE ?", (f"fulfill-intent:{intent}:%",)
        ).fetchone()[0]
        > 0
    )


def test_merge_completion_requires_terminal_evidence(store):
    seed(store, kind="merge", projection=True)
    with pytest.raises(f.Invalid, match="merge-evidence"):
        apply(store)
    receipt = msgspec.structs.replace(
        completion(store), evidence=[f.Evidence("integration:result", "e" * 64)]
    )
    assert apply(store, receipt).result == "applied"


def test_expired_identical_proposal_requires_explicit_replacement(store):
    seed(store, projection=True, owned=True)
    first = propose(store)
    store.db.execute("UPDATE ask_fulfillment_intents SET created_at='2020-01-01T00:00:00+00:00'")
    with pytest.raises(f.Invalid, match="stale-intent"):
        propose(store)
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone()[0] == 1
    result = store.fulfillment.propose(
        f.Propose("abcd", declaration(store), first.view["digest"]), uid=os.getuid(), chunk_chars=350
    )
    assert result.view["intent_id"] != first.view["intent_id"]


@pytest.mark.parametrize("valid", [0, 1])
def test_fulfillment_or_conflict_is_not_eligible(store, valid):
    seed(store, projection=True)
    apply(store)
    store.db.execute("UPDATE ask_fulfillments SET valid=?", (valid,))
    assert store.fulfillment.view("abcd")["eligible"] is False


def test_missing_socket_preserves_legacy_audit_only_peer(monkeypatch):
    # New checked credentials must fail with the existing OSError fallback contract.
    with pytest.raises(OSError):
        platform.peer_credentials_checked(None)


def test_unsigned_uid_gid_kernel_credentials():
    class Sock:
        def getsockopt(self, *_args):
            return struct.pack("iII", 123, 2**31 + 9, 2**32 - 2)

    assert platform.peer_credentials_checked(cast(socket.socket, Sock())) == (123, 2**31 + 9, 2**32 - 2)


@pytest.mark.parametrize("method", ["binding", "stored", "protected"])
def test_lifecycle_reads_wait_for_other_thread_transaction(store, method):
    seed(store, projection=True)
    entered = threading.Event()
    release = threading.Event()

    def writer():
        try:
            with store.transaction():
                apply(store)
                entered.set()
                assert release.wait(5)
                raise RuntimeError("rollback")
        except RuntimeError:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(writer)
        assert entered.wait(5)
        read = pool.submit(getattr(store.fulfillment, method), "abcd")
        try:
            with pytest.raises(TimeoutError):
                read.result(timeout=0.05)
        finally:
            release.set()
        write.result(timeout=5)
        result = read.result(timeout=5)
    if method == "protected":
        assert result is False
    elif method == "stored":
        assert result is None


def test_daemon_notices_use_central_redaction_wake_and_durable_ownership(tmp_path, monkeypatch):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    u = Unit(tmp_path, fulfillment_enabled=True)
    try:
        seed(u.store, projection=True)
        observed = []
        post = u.daemon.post

        def spy(*args, **kwargs):
            observed.append(args[0])
            post(*args, **kwargs)

        monkeypatch.setattr(u.daemon, "post", spy)
        peer = ctl.Peer(123, os.getuid(), os.getgid())
        u.daemon.wake.clear()
        result = u.store.fulfillment.propose(
            f.Propose("abcd", declaration(u.store)), uid=os.getuid(), chunk_chars=350
        )
        assert result.result == "pending" and u.daemon.wake.is_set()
        assert len(observed) > 1  # real split continuation path
        u.daemon.wake.clear()
        assert run(u.daemon.on_fulfillment(f.Apply(completion(u.store)), peer)).result == "applied"
        assert u.daemon.wake.is_set() and observed[-1].startswith("fulfill:")
        assert u.store.db.execute(
            "SELECT DISTINCT ownership_kind FROM outbox WHERE key LIKE 'fulfill%'"
        ).fetchall() == [("unknown",), ("system",)]
    finally:
        u.store.close()


@pytest.mark.parametrize("failure", ["sqlite", "crash-after-receipt"])
def test_inbound_confirmation_atomic_and_fixed_unavailable(tmp_path, monkeypatch, failure):
    import sqlite3

    from fakes.settings import OPERATOR_HEX
    from test_admind_fulfillment import deliver
    from test_admind_r1 import Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    u = Unit(tmp_path, fulfillment_enabled=True)
    try:
        seed(u.store, projection=True, owned=True)
        proposed = propose(u.store)
        assert proposed.view is not None
        target = deliver(u.store, proposed.view["intent_id"])
        event = msgspec.structs.replace(inbound("!fulfill", "f" * 64, OPERATOR_HEX), reply_to=ReplyTo(target))
        if failure == "sqlite":

            def unavailable(*_args, **_kwargs):
                raise sqlite3.OperationalError("private detail must not escape")

            monkeypatch.setattr(u.store.fulfillment, "confirm", unavailable)
        else:
            finish = u.daemon.finish

            def fail(mid, status, text, tag):
                if text == "fulfilled; original answer retained":
                    raise RuntimeError("simulated crash after receipt before inbound settlement")
                finish(mid, status, text, tag)

            monkeypatch.setattr(u.daemon, "finish", fail)
        run(u.daemon.on_message(event))
        assert not u.store.fulfillment.protected("abcd")
        assert (
            u.store.db.execute(
                "SELECT COUNT(*) FROM ask_fulfillment_audit WHERE disposition='applied'"
            ).fetchone()[0]
            == 0
        )
        if failure == "sqlite":
            texts = [
                r[0]
                for r in u.store.db.execute("SELECT text FROM outbox WHERE key NOT LIKE 'fulfill-intent:%'")
            ]
            assert any("Fulfillment unavailable: store unavailable." in text for text in texts)
            assert not any("private detail" in text for text in texts)
        else:
            assert (
                u.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'fulfill:%'").fetchone()[0]
                == 0
            )
    finally:
        u.store.close()


def test_merge_operator_declaration_also_requires_evidence(store):
    seed(store, kind="merge", projection=True)
    with pytest.raises(f.Invalid, match="merge-evidence"):
        propose(store)
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone()[0] == 0


def test_guard_maintenance_receipt_survives_fulfillment_settlement_refusal(store, monkeypatch):
    # A fulfilled overlay must be checked before *any* mutation of the guard attempt overlay.
    seed(store, kind="approval", status="open", projection=True)
    store.db.execute("UPDATE asks SET status='deciding',attempt='attempt' WHERE ask_id='abcd'")
    store.db.execute("INSERT INTO ask_maintenance VALUES ('abcd','attempt',42)")
    monkeypatch.setattr(store.fulfillment, "protected", lambda _ask: True)
    before = store.db.total_changes
    with pytest.raises(ValueError, match="already fulfilled"):
        store.close_attempt(
            "abcd",
            "attempt",
            expect_status="deciding",
            settled="recorded",
            exit_status=0,
            new_status="approved",
            outcome="approved",
            decided_by="operator",
        )
    assert store.db.total_changes == before
    assert store.db.execute("SELECT * FROM ask_maintenance").fetchall() == [("abcd", "attempt", 42)]


def test_projection_joins_do_not_call_per_row_protected(store, monkeypatch):
    seed(store, projection=True)
    apply(store)
    seed(store, ask_id="abce", status="open", projection=True)

    def forbidden(_ask):
        raise AssertionError("per-row lifecycle lookup")

    monkeypatch.setattr(store.fulfillment, "protected", forbidden)
    assert [r.ask_id for r in store.fulfilled_asks()] == ["abcd"]
    assert [r.ask_id for r in store.remindable_asks()] == ["abce"]
    assert store.capacity_count() == len(store.capacity_asks()) == 1
