"""First-review regressions over disposable operation stores and offline ports."""

import asyncio
from dataclasses import replace

import msgspec
import pytest
from test_admind_approvals import APPROVE, BEAD, D2, card_row, readout
from test_admind_maintenance import Port
from test_admind_operations import call, peer, request
from test_admind_r1 import Unit

from heterodyne.admind import asks, ctl, operations

pytest_plugins = ("test_admind_operations",)


def test_source_owned_bead_rejects_legacy_question_without_poisoning(unit: Unit) -> None:
    assert call(unit, request()).result == "posted"
    reply = asyncio.run(
        unit.daemon.serialised_post(asks.AskPost("question", title="Safe", body="What?", bead=BEAD), peer())
    )
    assert reply.result == "refused"
    assert call(unit, request("withdraw")).result == "withdrawn"


def test_historical_question_does_not_poison_approval_lineage(unit: Unit) -> None:
    question = asyncio.run(
        unit.daemon.serialised_post(asks.AskPost("question", title="Safe", body="What?", bead=BEAD), peer())
    )
    assert question.result == "posted"
    posted = call(unit, request())
    assert posted.result == "posted"
    assert len(posted.lineage) == 1
    assert call(unit, request("withdraw")).result == "withdrawn"


@pytest.mark.parametrize("bead", ["abc-1.2", "a-1"])
def test_operation_codec_uses_existing_bead_grammar(bead: str) -> None:
    assert operations.check(request(bead=bead)) is None


def test_operation_codec_refuses_bead_outside_existing_grammar() -> None:
    assert operations.check(request(bead="heterodyne-metaharness-ab12c")) == "binding"


def test_withdrawn_stale_attempt_settles_without_replacement(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    assert call(unit, request("withdraw")).result == "decision-in-flight"
    asyncio.run(unit.daemon.refresh(APPROVE, card_row(), readout(digest=D2), "refused", None, ""))
    result = call(unit, request("status"))
    assert result.result == "withdrawn"
    assert result.lineage[0].status == "cancelled"
    assert result.lineage[0].attempt is None


def test_withdraw_during_preflight_settles_proven_no_call(unit: Unit, monkeypatch) -> None:
    monkeypatch.setattr(unit.daemon, "authorised", lambda _mid: True)

    class Racing(Port):
        async def read(self, bead: str, on_launch=None):
            assert (await unit.daemon.on_operation(request("withdraw"), peer())).withdrawn
            return self.current

    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)
    port = Racing()
    unit.daemon.approve_bead = port
    asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    assert port.calls == 0
    result = call(unit, request("status"))
    assert result.result == "withdrawn"
    assert result.lineage[0].attempt is None
    assert result.lineage[0].status == "cancelled"
    assert not unit.store.maintenance_pending()


def test_refresh_at_lineage_capacity_settles_stale_without_throwing(unit: Unit, monkeypatch) -> None:
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)
    monkeypatch.setattr(operations, "MAX_LINEAGE", 1)
    asyncio.run(unit.daemon.refresh(APPROVE, card_row(), readout(digest=D2), "refused", None, ""))
    result = call(unit, request("status"))
    assert len(result.lineage) == 1
    assert result.lineage[0].status == "stale"
    assert result.lineage[0].attempt is None


def test_protected_answer_preserved_while_withdraw_cancels_open_sibling(unit: Unit, monkeypatch) -> None:
    old = card_row(status="answered")
    unit.store.insert_ask(old, None)
    unit.store.insert_ask(replace(card_row(), ask_id="b234", refreshed_from=old.ask_id), None)
    assert call(unit, request()).result == "ok"
    monkeypatch.setattr(unit.store.fulfillment, "protected", lambda ask_id: ask_id == old.ask_id)
    result = call(unit, request("withdraw"))
    assert result.withdrawn
    assert [row.status for row in result.lineage] == ["answered", "cancelled"]
    assert call(unit, request()).withdrawn
    events = unit.store.db.execute("SELECT COUNT(*) FROM approval_operation_events").fetchone()[0]
    assert call(unit, request("withdraw")).withdrawn
    assert unit.store.db.execute("SELECT COUNT(*) FROM approval_operation_events").fetchone()[0] == events


def test_enrollment_does_not_need_new_card_capacity(unit: Unit, monkeypatch) -> None:
    unit.store.insert_ask(card_row(), None)
    monkeypatch.setattr(unit.daemon, "approval_post_refusal", lambda *_args: "capacity full")
    result = call(unit, request())
    assert result.result == "ok"
    assert len(result.lineage) == 1
    assert not unit.store.pending()
    assert call(unit, request("withdraw")).withdrawn


def test_operation_card_creation_preserves_posted_audit(unit: Unit, monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(unit.daemon, "audit_posted", lambda *args: recorded.append(args))
    posted = call(unit, request())
    assert posted.result == "posted"
    assert len(recorded) == 1
    assert recorded[0][0].row.ask_id == posted.lineage[0].ask_id
    assert recorded[0][2] == []


def test_actual_socket_operation_validation_keeps_closed_reply_shape(unit: Unit) -> None:
    async def scenario() -> None:
        server = ctl.JsonSocketServer(
            unit.settings.state_dir / "operations-r1.sock",
            unit.daemon.on_ask_wire,
            unit.audit,
            kind="ask",
            max_request=asks.MAX_REQUEST,
            max_reply=asks.MAX_REPLY,
            request_type=asks.AskRequest | operations.Request,
            check=unit.daemon.check_ask_wire,
            refused=asks.refused,
            failed=asks.failed,
            describe=lambda r: {"op": type(r).__name__},
        )
        await server.start()
        valid = msgspec.json.encode(request())
        cases = [
            (msgspec.json.encode(request(operation="bad")), "identity"),
            (msgspec.json.encode(request(bead=None, context_digest=None)), "bead-required"),
            (valid[:-1] + b',"extra":true}', "malformed"),
            (valid[:-1] + b',"mode":"post"}', "malformed"),
        ]
        try:
            for wire, code in cases:
                reader, writer = await asyncio.open_unix_connection(server.path)
                writer.write(wire + b"\n")
                await writer.drain()
                data = await reader.readline()
                writer.close()
                await writer.wait_closed()
                reply = msgspec.json.decode(data, type=operations.Reply)
                assert reply.result == "refused"
                assert reply.code == code
            assert not unit.store.capacity_asks()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_status_lineage_uses_bounded_batch_queries(unit: Unit) -> None:
    for letter in "abcdefghjkmnpqrstuvwx":
        unit.store.insert_ask(card_row(ask_id="a" + letter + "23", status="stale"), None)
    assert call(unit, request()).result == "ok"
    statements = []
    unit.store.db.set_trace_callback(statements.append)
    try:
        result = call(unit, request("status"))
    finally:
        unit.store.db.set_trace_callback(None)
    assert len(result.lineage) == len("abcdefghjkmnpqrstuvwx")
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) < 16


def test_withdrawn_maintenance_retry_settles_only_after_untouched_readback(unit: Unit, monkeypatch) -> None:
    monkeypatch.setattr(unit.daemon, "authorised", lambda _mid: True)
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)
    port = Port()
    unit.daemon.approve_bead = port
    unit.daemon.wallclock = lambda: 100.0
    asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    assert port.calls == 1
    assert unit.store.maintenance_pending()
    assert call(unit, request("withdraw")).result == "decision-in-flight"
    unit.daemon.wallclock = lambda: 130.0
    asyncio.run(unit.daemon.retry_maintenance(APPROVE.ask_id, APPROVE.message_id))
    result = call(unit, request("status"))
    assert result.result == "withdrawn"
    assert result.lineage[0].status == "cancelled"
    assert result.lineage[0].attempt is None
    assert port.calls == 1
    assert not unit.store.maintenance_pending()


def test_withdrawn_retry_does_not_convert_unreadable_native_into_cancellation(
    unit: Unit, monkeypatch
) -> None:
    monkeypatch.setattr(unit.daemon, "authorised", lambda _mid: True)
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)
    port = Port()
    unit.daemon.approve_bead = port
    unit.daemon.wallclock = lambda: 100.0
    asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    assert call(unit, request("withdraw")).withdrawn

    async def unreadable(*_args, **_kwargs):
        raise OSError("offline read unavailable")

    monkeypatch.setattr(port, "read", unreadable)
    unit.daemon.wallclock = lambda: 130.0
    asyncio.run(unit.daemon.retry_maintenance(APPROVE.ask_id, APPROVE.message_id))
    result = call(unit, request("status"))
    assert result.result == "decision-in-flight"
    assert result.lineage[0].status == "deciding"
    assert result.lineage[0].attempt == APPROVE.message_id
    assert port.calls == 1
