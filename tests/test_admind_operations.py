"""Source operation protocol over disposable SQLite and offline approval ports only."""
import asyncio
import os
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from test_admind_approvals import APPROVE, BEAD, D2, D, card_row, readout
from test_admind_maintenance import Port
from test_admind_r1 import Unit

from heterodyne.admind import asks, ctl, operations
from heterodyne.admind.approvals import BtqError
from heterodyne.admind.store import Store


def request(mode: str = "post", **changes: object) -> operations.Request:
    data = dict(mode=mode, source="a" * 64, journal="b" * 64,
                operation="01234567-0123-4123-8123-0123456789ab", bead=BEAD,
                context_digest=D, intent_hash="c" * 64)
    return msgspec.convert(data | changes, operations.Request)


def test_post_retry_and_withdraw_all_lineage(tmp_path: Path) -> None:
    async def scenario() -> None:
        u = Unit(tmp_path)
        u.daemon.approve_bead = Port()
        peer = ctl.Peer(os.getpid(), os.getuid())
        try:
            first = await u.daemon.on_operation(request(), peer)
            assert first.result == "posted"
            count = len(u.store.pending())
            again = await u.daemon.on_operation(request(), peer)
            assert again.receipt == first.receipt
            assert len(u.store.pending()) == count
            withdrawn = await u.daemon.on_operation(request("withdraw"), peer)
            assert withdrawn.result == "withdrawn"
            assert len(withdrawn.lineage) == 1
            assert withdrawn.lineage[0].status == "cancelled"
            assert (await u.daemon.on_operation(request(), peer)).result == "withdrawn"
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_withdraw_before_post_prevents_native_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        u = Unit(tmp_path)
        peer = ctl.Peer(os.getpid(), os.getuid())
        try:
            result = await u.daemon.on_operation(request("withdraw"), peer)
            assert result.result == "withdrawn"
            assert (await u.daemon.on_operation(request(), peer)).result == "withdrawn"
            assert not u.store.pending()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_closed_codec_duplicate_and_unknown() -> None:
    wire = msgspec.json.encode(request())
    assert operations.decode(wire) == request()
    for bad in (wire[:-1] + b',"mode":"status"}', wire[:-1] + b',"answer":"approve"}', b" " * 16385):
        with pytest.raises(operations.Invalid):
            operations.decode(bad)


@pytest.fixture
def unit(tmp_path: Path):
    u = Unit(tmp_path)
    u.daemon.approve_bead = Port()
    yield u
    u.store.close()


def peer() -> ctl.Peer:
    return ctl.Peer(os.getpid(), os.getuid())


def call(u: Unit, req: operations.Request) -> operations.Reply:
    return asyncio.run(u.daemon.on_operation(req, peer()))


@pytest.mark.parametrize("change", [dict(source="A" * 64), dict(journal=""), dict(intent_hash="0"),
                                    dict(operation="not-a-uuid"), dict(bead="../x"),
                                    dict(context_digest=None), dict(mode="other"), dict(source=True)])
def test_invalid_local_objects_do_not_mutate(unit: Unit, change: dict[str, object]) -> None:
    # Deliberate malformed typed local object; the handler must validate beyond static annotations.
    good = request()
    fields = msgspec.to_builtins(good) | change
    fields.pop("type")
    broken = operations.Request(**fields)
    before = unit.store.db.total_changes
    assert call(unit, broken).result == "refused"
    assert unit.store.db.total_changes == before


def test_trusted_uid_required_even_pid_present(unit: Unit) -> None:
    for bad in (ctl.Peer(os.getpid()), ctl.Peer(os.getpid(), os.getuid() + 1)):
        reply = asyncio.run(unit.daemon.on_operation(request("withdraw"), bad))
        assert reply.code == "trusted-uid-required"
    assert unit.store.db.execute("SELECT COUNT(*) FROM approval_operations").fetchone()[0] == 0


def test_changed_payload_and_competing_bead_key_refuse(unit: Unit) -> None:
    first = call(unit, request())
    before = unit.store.db.total_changes
    for change in (dict(intent_hash="d" * 64), dict(context_digest="d" * 64),
                   dict(bead="btq-ef678")):
        assert call(unit, request(**change)).code == "payload-conflict"
    conflict = call(unit, request("withdraw", operation="01234567-0123-4123-8123-0123456789ac"))
    assert conflict.code == "bead-key-conflict"
    assert unit.store.db.total_changes == before
    assert call(unit, request("status")).receipt == first.receipt


def test_beadless_tombstone_never_rebinds_late_orphan(unit: Unit) -> None:
    none = dict(bead=None, context_digest=None)
    initial = call(unit, request("withdraw", **none))
    assert initial.result == "withdrawn" and initial.lineage == []
    assert call(unit, request()).code == "withdrawn-payload-conflict"
    assert call(unit, request("status", **none)).receipt == initial.receipt
    assert not unit.store.capacity_asks()


def test_restart_and_exact_retry_do_not_read_native_or_write(unit: Unit) -> None:
    first = call(unit, request())
    unit.store.close()
    unit.store = Store(unit.settings.state_dir / "admind.db")
    unit.build()
    assert unit.daemon.approve_bead is None
    before = unit.store.db.total_changes
    assert call(unit, request()).receipt == first.receipt
    assert call(unit, request("status")).lineage == first.lineage
    assert unit.store.db.total_changes == before


def test_whole_historical_lineage_enrolled_and_cancelled(unit: Unit) -> None:
    old = card_row(ask_id="a234", status="superseded")
    second = card_row(ask_id="b234", status="answered", refreshed_from=old.ask_id)
    unit.store.insert_ask(old, None)
    unit.store.insert_ask(second, None)
    initial = call(unit, request())
    assert [r.ask_id for r in initial.lineage] == [old.ask_id, second.ask_id]
    assert not unit.store.pending()  # adoption never posts a new card
    result = call(unit, request("withdraw"))
    assert [r.status for r in result.lineage] == ["superseded", "cancelled"]
    assert result.revision is not None and result.revision > (initial.revision or 0)
    assert len(unit.store.pending()) >= 1


def test_incomplete_refresh_history_refuses_enrollment(unit: Unit) -> None:
    unit.store.insert_ask(card_row(refreshed_from="lost"), None)
    assert call(unit, request("withdraw")).code == "lineage-incomplete"
    row = unit.store.ask("k7m2")
    assert row is not None and row.status == "open"
    assert not unit.store.db.execute("SELECT * FROM approval_operations").fetchall()


def test_legacy_same_bead_denied_unrelated_legacy_compatible(unit: Unit) -> None:
    call(unit, request())
    result = asyncio.run(unit.daemon.serialised_post(asks.AskPost("approval", bead=BEAD), peer()))
    assert result.result == "refused"
    # Other kinds have no source-owned key restrictions.
    result = asyncio.run(unit.daemon.serialised_post(asks.AskPost("question", title="Safe", body="What?"),
                                                    peer()))
    assert result.result == "posted"


def test_stale_decision_snapshot_cannot_claim_after_withdraw(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert call(unit, request("withdraw")).result == "withdrawn"
    assert not unit.store.begin_attempt(APPROVE)
    assert unit.store.current_attempt(APPROVE.ask_id) is None
    row = unit.store.ask(APPROVE.ask_id)
    assert row is not None and row.status == "cancelled"


def test_withdraw_inflight_preserves_attempt_and_late_real_decision(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    result = call(unit, request("withdraw"))
    assert result.result == "decision-in-flight"
    assert result.lineage[0].attempt == APPROVE.message_id
    assert not unit.store.issue_decision(APPROVE)  # withdrawal during preflight must not launch
    assert call(unit, request("status")).result == "decision-in-flight"
    # A genuine previously issued decision may settle after withdrawal; never rewrite native consent.
    assert unit.store.close_attempt(APPROVE.ask_id, APPROVE.message_id, expect_status="deciding",
                                    settled="recorded", exit_status=0, new_status="approved",
                                    outcome="recorded", decided_by="op")
    after = call(unit, request("withdraw"))
    assert after.lineage[0].status == "approved" and after.withdrawn
    assert after.lineage[0].decision_refs == [APPROVE.ref]


def test_refresh_carries_key_then_withdraw_guards_stale_commit(unit: Unit) -> None:
    async def scenario() -> None:
        first = await unit.daemon.on_operation(request(), peer())
        old_id = first.lineage[0].ask_id
        a = replace(APPROVE, ask_id=old_id)
        assert unit.store.begin_attempt(a)
        old = unit.store.ask(old_id)
        assert old is not None
        await unit.daemon.refresh(a, old, readout(digest=D2), "refused", None, "")
        current = await unit.daemon.on_operation(request("status"), peer())
        assert len(current.lineage) == 2
        assert current.lineage[-1].refreshed_from == old_id
        stale = unit.daemon.prepare_approval("wsd", BEAD, readout())
        await unit.daemon.on_operation(request("withdraw"), peer())
        from heterodyne.admind.daemon import FreshCard
        assert isinstance(stale, FreshCard)
        with pytest.raises(operations.Invalid, match="withdrawn"):
            unit.daemon.commit_approval(stale, None, operation_key=operations.key(request()))
        assert [r.status for r in (await unit.daemon.on_operation(request("status"), peer())).lineage] == [
            "stale", "cancelled"]
    asyncio.run(scenario())


def test_notice_failure_rolls_back_tombstone_and_all_cancellations(unit: Unit, monkeypatch) -> None:
    call(unit, request())
    def fail(*args, **kwargs):
        raise OSError("disposable fault")
    monkeypatch.setattr(unit.daemon, "ask_notice", fail)
    with pytest.raises(OSError):
        call(unit, request("withdraw"))
    status = call(unit, request("status"))
    assert not status.withdrawn and status.lineage[0].status == "open"
    monkeypatch.undo()
    assert call(unit, request("withdraw")).lineage[0].status == "cancelled"


def test_capacity_full_allows_tombstone_and_exact_replay(unit: Unit, monkeypatch) -> None:
    initial = call(unit, request())
    monkeypatch.setattr(operations, "MAX_OPERATIONS", 1)
    assert call(unit, request()).receipt == initial.receipt
    # Existing key can still withdraw; a new distinct key holds visibly with no replacement.
    assert call(unit, request("withdraw")).withdrawn
    other = request("withdraw", operation="01234567-0123-4123-8123-0123456789ad",
                    bead="btq-ef678")
    assert call(unit, other).code == "operation-capacity"


def test_readout_gates_before_mapping_and_no_raw_error_output(unit: Unit) -> None:
    class Failing(Port):
        async def read(self, bead: str, on_launch=None):
            raise BtqError("unavailable")
    unit.daemon.approve_bead = Failing()
    assert call(unit, request()).code == "readout-unavailable"
    assert not unit.store.db.execute("SELECT * FROM approval_operations").fetchall()
    p = Port()
    p.current = readout(digest=D2)
    unit.daemon.approve_bead = p
    assert call(unit, request()).code == "context-conflict"
    unit.store.set("latched", "private diagnostic")
    assert call(unit, request()).code == "post-gate"


def test_transactional_audit_and_repeat_no_extra_events(unit: Unit) -> None:
    call(unit, request())
    call(unit, request("withdraw"))
    rows = unit.store.db.execute("SELECT action,uid,before_hash,after_hash FROM approval_operation_events "
                                 "ORDER BY seq").fetchall()
    assert [r[0] for r in rows] == ["post", "withdraw"]
    assert all(r[1] == os.getuid() and r[2] != r[3] for r in rows)
    for mode in ("status", "post", "withdraw"):
        call(unit, request(mode))
    assert unit.store.db.execute("SELECT COUNT(*) FROM approval_operation_events").fetchone()[0] == 2


def test_reply_capacity_refusal_rolls_back_mapping_card_and_audit(unit: Unit, monkeypatch) -> None:
    monkeypatch.setattr(operations, "MAX_REPLY", 1)
    result = call(unit, request())
    assert result.code == "reply-capacity"
    assert not unit.store.pending() and not unit.store.capacity_asks()
    assert not unit.store.db.execute("SELECT * FROM approval_operations").fetchall()
    assert not unit.store.db.execute("SELECT * FROM approval_operation_events").fetchall()


def test_lineage_capacity_refuses_without_partial_cancellation(unit: Unit, monkeypatch) -> None:
    unit.store.insert_ask(card_row(ask_id="a234"), None)
    unit.store.insert_ask(card_row(ask_id="b234"), None)
    monkeypatch.setattr(operations, "MAX_LINEAGE", 1)
    assert call(unit, request("withdraw")).code == "lineage-capacity"
    assert len(unit.store.asks_with_status("open")) == 2
    assert not unit.store.db.execute("SELECT * FROM approval_operations").fetchall()


def test_withdraw_during_native_preflight_never_issues(unit: Unit) -> None:
    class Racing(Port):
        async def read(self, bead: str, on_launch=None):
            reply = await unit.daemon.on_operation(request("withdraw"), peer())
            assert reply.result == "decision-in-flight"
            return self.current
    unit.store.insert_ask(card_row(), None)
    assert call(unit, request()).result == "ok"
    assert unit.store.begin_attempt(APPROVE)
    port = Racing()
    unit.daemon.approve_bead = port
    asyncio.run(unit.daemon.run_attempt(APPROVE, card_row()))
    assert port.calls == 0
    assert call(unit, request("status")).withdrawn


def test_refresh_after_withdraw_does_not_emit_descendant(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    assert call(unit, request("withdraw")).result == "decision-in-flight"
    before = len(unit.store.pending())
    asyncio.run(unit.daemon.refresh(APPROVE, card_row(), readout(digest=D2), "refused", None, ""))
    after = call(unit, request("status"))
    assert len(after.lineage) == 1 and after.lineage[0].status == "deciding"
    assert len(unit.store.pending()) == before


def test_concurrent_exact_posts_create_one_card(unit: Unit) -> None:
    async def scenario() -> None:
        a, b = await asyncio.gather(unit.daemon.on_operation(request(), peer()),
                                    unit.daemon.on_operation(request(), peer()))
        assert a.receipt == b.receipt and a.lineage == b.lineage
        assert {a.result, b.result} == {"posted", "ok"}
    asyncio.run(scenario())
    assert len(unit.store.capacity_asks()) == 1
    assert unit.store.db.execute("SELECT COUNT(*) FROM approval_operation_events").fetchone()[0] == 1


def test_actual_socket_strict_operation_and_legacy_coexist(unit: Unit) -> None:
    async def scenario() -> None:
        # Real Unix framing/peer credentials and handler, disposable private socket only.
        server = ctl.JsonSocketServer(
            unit.settings.state_dir / "operations-test.sock", unit.daemon.on_ask_wire, unit.audit, kind="ask",
            max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY,
            request_type=asks.AskRequest | operations.Request,
            check=unit.daemon.check_ask_wire,
            refused=asks.refused, failed=asks.failed, describe=lambda r: {"op": type(r).__name__})
        await server.start()
        async def send(wire: bytes) -> dict:
            reader, writer = await asyncio.open_unix_connection(server.path, limit=asks.MAX_REPLY + 1)
            writer.write(wire + b"\n")
            await writer.drain()
            data = await reader.readline()
            writer.close()
            await writer.wait_closed()
            return msgspec.json.decode(data)
        try:
            wire = msgspec.json.encode(request())
            assert (await send(wire))["result"] == "posted"
            for bad in (wire[:-1] + b',"mode":"post"}', wire[:-1] + b',"answer":"A"}',
                        b" " * operations.MAX_REQUEST + wire):
                assert (await send(bad))["result"] == "refused"
            assert (await send(msgspec.json.encode(asks.AskList())))["result"] == "ok"
            assert (await send(msgspec.json.encode(request("withdraw"))))["withdrawn"] is True
        finally:
            await server.close()
    asyncio.run(scenario())


def test_migrated_existing_context_must_match_retained_history(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    result = call(unit, request("withdraw", context_digest=D2))
    assert result.code == "lineage-context-conflict"
    assert not unit.store.db.execute("SELECT * FROM approval_operations").fetchall()


def test_positive_untouched_reconciliation_then_repeat_withdraw_cancels(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    assert call(unit, request("withdraw")).result == "decision-in-flight"
    assert unit.store.close_attempt(APPROVE.ask_id, APPROVE.message_id, expect_status="deciding",
                                    settled="untouched", exit_status=1, new_status="open",
                                    outcome="actual untouched", decided_by=None)
    # The existing native reconciliation remains authoritative; no guessing absence on a timeout.
    result = call(unit, request("withdraw"))
    assert result.lineage[0].status == "cancelled"
    assert not unit.store.begin_attempt(APPROVE)


def test_withdraw_cancels_open_sibling_while_retaining_inflight(unit: Unit) -> None:
    unit.store.insert_ask(card_row(), None)
    assert unit.store.begin_attempt(APPROVE)
    sibling = replace(card_row(), ask_id="b234", refreshed_from=APPROVE.ask_id)
    unit.store.insert_ask(sibling, None)
    result = call(unit, request("withdraw"))
    assert result.result == "decision-in-flight"
    assert [row.status for row in result.lineage] == ["deciding", "cancelled"]
    assert result.lineage[0].attempt == APPROVE.message_id
    assert not unit.store.issue_decision(APPROVE)


def test_shared_validator_preserves_each_protocol_refusal(unit: Unit) -> None:
    assert unit.daemon.check_ask_wire(request(source="not-a-hash")) == "identity"
    invalid = asks.AskPost("question", title="question?", body="body", poster="bad poster")
    assert unit.daemon.check_ask_wire(invalid) is not None
