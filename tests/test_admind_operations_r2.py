"""Second-review races use actual handlers over disposable stores only."""

import asyncio

import pytest
from test_admind_approvals import APPROVE, D2, card_row, readout
from test_admind_maintenance import Port
from test_admind_operations import call, peer, request
from test_admind_r1 import Unit

pytest_plugins = ("test_admind_operations",)


@pytest.mark.parametrize("restart", [False, True])
def test_withdrawn_untouched_settlement_never_reopens(unit: Unit, monkeypatch, restart: bool) -> None:
    monkeypatch.setattr(unit.daemon, "authorised", lambda _: True)
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)

    class Racing(Port):
        async def decide(self, a, bead, on_launch=None):
            if on_launch:
                on_launch()
            assert (await unit.daemon.on_operation(request("withdraw"), peer())).withdrawn
            return 1, ""

    unit.daemon.approve_bead = Racing()
    if restart:
        assert call(unit, request("withdraw")).withdrawn
        asyncio.run(unit.daemon.reconcile_one(APPROVE.ask_id, APPROVE.message_id, "deciding", restarted=True))
    else:
        asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    result = call(unit, request("status"))
    assert result.lineage[0].status == "cancelled"
    assert result.lineage[0].attempt is None
    assert not unit.store.begin_attempt(APPROVE)


def test_repeat_parts_are_in_complete_outbox_projection(unit: Unit) -> None:
    posted = call(unit, request())
    ask_id = posted.lineage[0].ask_id
    unit.daemon.post(f"askr:{ask_id}:original:0", "repeat", None, owner=None)
    assert f"askr:{ask_id}:original:0" in call(unit, request("status")).lineage[0].outbox


@pytest.mark.parametrize("status,digest", [("stale", D2), ("cancelled", None), ("denied", None)])
def test_initial_post_adopts_terminal_history_and_creates_current_card(
    unit: Unit, status: str, digest
) -> None:
    unit.store.insert_ask(card_row(status=status), None)
    if digest is not None:
        port = Port()
        port.current = readout(digest=digest)
        unit.daemon.approve_bead = port
    result = call(unit, request(**({"context_digest": digest} if digest else {})))
    assert result.result == "posted"
    assert len(result.lineage) == 2
    assert result.lineage[0].status == status
    assert result.lineage[-1].status == "open"


def test_withdrawal_records_cancelled_audit_after_commit(unit: Unit, monkeypatch) -> None:
    call(unit, request())
    events = []
    original = unit.audit.write

    def record(kind, **fields):
        if fields.get("action") == "cancelled":
            assert not unit.store.db.in_transaction
            events.append(fields)
        original(kind, **fields)

    monkeypatch.setattr(unit.audit, "write", record)
    assert call(unit, request("withdraw")).withdrawn
    assert len(events) == 1


def test_withdraw_preflight_audit_failure_cannot_rollback_cancellation(unit: Unit, monkeypatch) -> None:
    monkeypatch.setattr(unit.daemon, "authorised", lambda _: True)
    unit.store.insert_ask(card_row(), None)
    call(unit, request())
    assert unit.store.begin_attempt(APPROVE)

    class Racing(Port):
        async def read(self, bead, on_launch=None):
            assert (await unit.daemon.on_operation(request("withdraw"), peer())).withdrawn
            return self.current

    unit.daemon.approve_bead = Racing()
    original = unit.audit.write

    def fail(kind, **fields):
        if fields.get("action") == "refused":
            raise OSError("disposable audit failure")
        original(kind, **fields)

    monkeypatch.setattr(unit.audit, "write", fail)
    asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    result = call(unit, request("status"))
    assert result.lineage[0].status == "cancelled"
    assert result.lineage[0].attempt is None


def test_status_outbox_projection_uses_indexed_key_ranges(unit: Unit) -> None:
    call(unit, request())
    for i in range(100):
        unit.daemon.post(f"unrelated:{i}", "other", None, owner=None)
    statements = []
    unit.store.db.set_trace_callback(statements.append)
    try:
        assert call(unit, request("status")).result == "ok"
    finally:
        unit.store.db.set_trace_callback(None)
    query = next(s for s in statements if s.startswith("WITH prefixes"))
    plan = unit.store.db.execute("EXPLAIN QUERY PLAN " + query).fetchall()
    details = [str(row[3]) for row in plan]
    assert any("SEARCH o USING" in item and "key>? AND key<?" in item for item in details), details
    assert not any("SCAN o" in item for item in details)


def test_withdrawn_refresh_writes_settlement_audit_after_commit(unit: Unit, monkeypatch) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    call(unit, request("withdraw"))
    events = []
    original = unit.audit.write

    def record(kind, **fields):
        if fields.get("action") == "refused":
            assert not unit.store.db.in_transaction
            events.append(fields)
        original(kind, **fields)

    monkeypatch.setattr(unit.audit, "write", record)
    asyncio.run(unit.daemon.refresh(APPROVE, card_row(), readout(digest=D2), "refused", None, ""))
    assert len(events) == 1
    assert events[0]["status"] == "cancelled"
    assert events[0]["fresh"] is None
