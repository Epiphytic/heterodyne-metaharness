"""Authenticated pre-effect HOLD retention over offline ports and disposable SQLite only."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX
from test_admind_approvals import APPROVE, BEAD, MID, D, card_row, readout
from test_admind_r1 import Unit

from heterodyne.admind.approvals import ApproveBead, Attempt, MaintenanceHold, Readout, maintenance_hold

HOLD = b'{"schema":"maintenance-hold.v1","effect":"not-issued","reason":"maintenance"}'


@pytest.mark.parametrize("code,out,expected", [
    (75, HOLD, True), (0, HOLD, False), (1, HOLD, False), (75, b"", False),
    (75, b"maintenance", False), (75, HOLD + b"x", False),
    (75, HOLD.replace(b"not-issued", b"issued"), False),
    (75, HOLD.replace(b".v1", b".v2"), False),
    (75, HOLD[:-1] + b',"reason":"maintenance"}', False),
    (75, HOLD[:-1] + b',"extra":true}', False), (75, b" " * 257 + HOLD, False),
    (75, b'"maintenance-hold.v1"', False), (75, b"\xff", False),
])
def test_closed_hold_protocol(code: int, out: bytes, expected: bool) -> None:
    assert maintenance_hold(code, out) is expected


class Port(ApproveBead):
    def __init__(self) -> None:
        super().__init__(Path("/unused-offline"))
        self.calls = 0
        self.hold = True
        self.current = readout()

    async def read(self, bead: str, on_launch: Callable[[], None] | None = None) -> Readout:
        assert bead == BEAD
        return self.current

    async def decide(self, a: Attempt, bead: str,
                     on_launch: Callable[[], None] | None = None) -> tuple[int, str] | MaintenanceHold:
        self.calls += 1
        if on_launch:
            on_launch()
        if self.hold:
            return MaintenanceHold()
        self.current = readout(status="closed", decided=True, decision=a.action, approved_by=a.operator,
                               approved_digest=a.digest, via_ref=a.ref, gate_valid=True, gate_preview=None)
        return 0, ""


def setup(tmp_path: Path) -> tuple[Unit, Port]:
    u, port = Unit(tmp_path), Port()
    u.daemon.approve_bead = port
    u.daemon.wallclock = lambda: 100.0
    u.store.insert_ask(card_row(), None)
    assert u.store.claim_inbound(MID, sender_key=OPERATOR_HEX)
    assert u.store.begin_attempt(APPROVE)
    return u, port


def test_hold_restart_same_attempt_and_exactly_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            assert port.calls == 1
            assert u.store.maintenance_pending() == [(APPROVE.ask_id, MID, 130.0)]
            assert u.store.inbound_status(MID) == "executing"
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            # New daemon has no in-memory sender map; authority comes from persisted provenance.
            u.build()
            u.daemon.approve_bead = port
            u.daemon.wallclock = lambda: 129.0
            u.daemon.recover()
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1 and u.store.inbound_status(MID) == "executing"
            u.daemon.wallclock = lambda: 130.0
            port.hold = False
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 2
            assert u.store.ask(APPROVE.ask_id).status == "approved"  # type: ignore[union-attr]
            assert u.store.inbound_status(MID) == "done"
            assert u.store.maintenance_pending() == []
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 2
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["digest", "gate", "approver", "latch", "sender", "name"])
def test_retry_revalidates_and_preserves_authenticated_attempt(tmp_path: Path, change: str) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.wallclock = lambda: 130.0
            if change == "digest":
                port.current = readout(digest="f" * 64)
            elif change == "gate":
                port.current = readout(gate_preview={"valid": False, "reasons": ["changed"]})
            elif change == "approver":
                port.current = readout(approvers=[])
            elif change == "latch":
                u.store.set("latched", "operator recovery required")
            elif change == "sender":
                u.store.db.execute("UPDATE inbound SET sender_key = NULL WHERE message_id = ?", (MID,))
            else:
                original = u.daemon.operator_name
                u.daemon.operator_name = lambda _: "renamed"  # type: ignore[method-assign]
                assert original(MID) == APPROVE.operator
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert u.store.maintenance_pending() == [(APPROVE.ask_id, MID, 160.0)]
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("own", [True, False])
def test_native_first_valid_result_wins_without_reissue(tmp_path: Path, own: bool) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.wallclock = lambda: 130.0
            port.current = readout(status="closed", decided=True, decision="approve", approved_by="op",
                                   approved_digest=D, via_ref=APPROVE.ref if own else "marmot:other",
                                   gate_valid=True, gate_preview=None)
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
            assert u.store.ask(APPROVE.ask_id).status == ("approved" if own else "blocked")  # type: ignore
            assert u.store.current_attempt(APPROVE.ask_id) is None
            assert u.store.maintenance_pending() == []
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_prelaunch_crash_consumes_only_retry_entitlement(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            assert not u.store.issue_maintenance_retry(replace(APPROVE, message_id="f" * 64))
            assert u.store.issue_maintenance_retry(APPROVE)
            assert not u.store.issue_maintenance_retry(APPROVE)
            u.build()
            u.daemon.approve_bead = port
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            assert port.calls == 1  # ambiguous restart uses readback, never calls decide again
            assert u.store.current_attempt(APPROVE.ask_id) is None
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_retry_rechecks_operator_name_after_native_await(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.wallclock = lambda: 130.0
            original = port.read
            reads = 0

            async def changed(bead: str, on_launch: Callable[[], None] | None = None) -> Readout:
                nonlocal reads
                reads += 1
                if reads == 2:
                    u.daemon.operator_name = lambda _: "renamed"  # type: ignore[method-assign]
                return await original(bead, on_launch)

            port.read = changed  # type: ignore[method-assign]
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert reads == 2 and port.calls == 1
            assert u.store.maintenance_pending() == [(APPROVE.ask_id, MID, 160.0)]
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("output,proven", [(HOLD, True), (b"", False), (HOLD + b"x", False)])
def test_decide_classifies_only_closed_pre_effect_child_result(tmp_path: Path, output: bytes,
                                                             proven: bool) -> None:
    class ChildResult(ApproveBead):
        async def _run(self, argv: list[str], timeout: float, cap: int,
                       on_launch: Callable[[], None] | None = None) -> tuple[int, bytes, bytes]:
            if on_launch:
                on_launch()
            return 75, output, b"diagnostic"

    result = asyncio.run(ChildResult(tmp_path / "offline").decide(APPROVE, BEAD))
    assert isinstance(result, MaintenanceHold) is proven
    if not proven:
        assert result == (75, "diagnostic")


def test_ambiguous_child_failure_never_enters_maintenance_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            async def failure(a: Attempt, bead: str,
                              on_launch: Callable[[], None] | None = None) -> tuple[int, str]:
                port.calls += 1
                if on_launch:
                    on_launch()
                raise TimeoutError("possible issuance")

            port.decide = failure  # type: ignore[method-assign]
            await u.daemon.run_attempt(APPROVE, card_row())
            assert u.store.maintenance_pending() == []
            assert u.store.current_attempt(APPROVE.ask_id) is None
            assert u.store.ask(APPROVE.ask_id).status == "open"  # type: ignore[union-attr]
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
        finally:
            u.store.close()
    asyncio.run(scenario())
