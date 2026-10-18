"""Recovery repair regressions: synthetic ports, disposable SQLite, no native backend."""

import asyncio
import json
from pathlib import Path

import pytest
from test_admind_approvals import APPROVE, readout
from test_admind_maintenance import HOLD
from test_admind_retirement import ProvenPort, command, eligible, snapshot

from heterodyne.admind.approvals import BtqError, MaintenanceHold
from heterodyne.admind.daemon import canonical_ref
from heterodyne.admind.retirement import publish_notice, retire


@pytest.mark.parametrize("status", ["failed", "sent"])
def test_explicit_inspection_replaces_unusable_notice(tmp_path: Path, status: str) -> None:
    async def scenario() -> None:
        u, _, proof, _ = await eligible(tmp_path)
        try:
            u.store.db.execute(
                "UPDATE outbox SET status=?,message_id=NULL WHERE key LIKE 'retirement:%'", (status,)
            )
            with u.store.transaction():
                publish_notice(u.daemon, APPROVE, refresh=True)
            pending = [r for r in u.store.pending() if r.key.startswith("retirement:")]
            assert len(pending) == 1
            new_notice = "98" * 32
            u.store.mark_sent(pending[0].seq, new_notice)
            assert u.store.retirement.select(proof.token, new_notice) == (APPROVE, proof)
            # A second inspection after delivery must not duplicate it.
            with u.store.transaction():
                publish_notice(u.daemon, APPROVE, refresh=True)
            assert len([r for r in u.store.pending() if r.key.startswith("retirement:")]) == 0
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_adapter_change_does_not_overwrite_captured_chain(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, _, proof, _ = await eligible(tmp_path)
        try:
            assert u.store.issue_decision(APPROVE, maintenance_retry=True)
            assert not u.store.retirement.record_hold(APPROVE, MaintenanceHold(HOLD, "b" * 64))
            assert u.store.retirement._proof(APPROVE) == proof
            assert u.store.defer_maintenance(APPROVE, 190)
            assert u.store.retirement.eligible(APPROVE) is None
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_proven_spawn_failure_retains_monotonic_covered_generation(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, _, _ = await eligible(tmp_path)
        try:

            async def unavailable(*args, **kwargs):
                raise BtqError("unavailable")

            port.decide = unavailable
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            assert u.store.retirement.generation(APPROVE) == (2, True)
            proof = u.store.retirement.eligible(APPROVE)
            assert proof is not None and proof.invocations == 2
            body = json.loads(proof.body)
            assert body["not_spawned"] == 1 and body["consecutive"] == 1
            port.decide = ProvenPort.decide.__get__(port)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            proof = u.store.retirement.eligible(APPROVE)
            assert proof is not None and proof.invocations == 3
            assert json.loads(proof.body)["consecutive"] == 2
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_unknown_postlaunch_failure_never_becomes_not_spawned(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, _ = await eligible(tmp_path)
        try:

            async def unavailable(a, bead, on_launch=None):
                assert on_launch is not None
                on_launch()
                raise BtqError("unavailable")

            port.decide = unavailable
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            assert u.store.retirement._proof(APPROVE) == proof
            assert u.store.retirement.eligible(APPROVE) is None
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_old_delivered_notice_reports_existing_retirement(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, old, old_notice = await eligible(tmp_path)
        try:
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            proof = u.store.retirement.eligible(APPROVE)
            assert proof is not None
            with u.store.transaction():
                publish_notice(u.daemon, APPROVE, refresh=True)
            pending = next(r for r in u.store.pending() if r.key.startswith("retirement:"))
            notice = "97" * 32
            u.store.mark_sent(pending.seq, notice)
            await retire(u.daemon, command(u, notice), proof.token, notice)
            before = snapshot(u)
            reads = port.reads
            mid = command(u, old_notice)
            await retire(u.daemon, mid, old.token, old_notice)
            assert snapshot(u) == before and reads == port.reads
            reply = u.store.outbox_text(f"retirement-existing:{mid}:0")
            assert reply is not None and "already retired" in reply
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "native,reason", [({"kind_approval": False}, "not-approval"), ({"status": "in_progress"}, "not-open")]
)
def test_receipt_preserves_reason_and_real_command_reference(
    tmp_path: Path, native: dict, reason: str
) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            port.current = readout(**native)
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            receipt = u.store.retirement.receipt(proof.token, notice)
            assert receipt is not None
            value = json.loads(receipt)
            assert value["reason"] == reason
            assert value["ref"] == "marmot:" + canonical_ref(mid)
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_attempt_identity_survives_notice_redaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, _, proof, _ = await eligible(tmp_path)
        try:
            text = u.store.outbox_text(f"retirement:{APPROVE.ask_id}:{proof.token}")
            assert text is not None
            grouped = "-".join(APPROVE.message_id[i : i + 8] for i in range(0, len(APPROVE.message_id), 8))
            assert grouped in text and "<redacted hex key>" not in text
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_same_command_returns_existing_receipt_without_new_effects(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            receipt = u.store.retirement.receipt(proof.token, notice)
            before = tuple(u.store.db.execute("SELECT * FROM outbox"))
            reads = port.reads
            assert await retire(u.daemon, mid, proof.token, notice) == receipt
            assert before == tuple(u.store.db.execute("SELECT * FROM outbox"))
            assert reads == port.reads
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_real_adapter_identity_hashes_run_off_event_loop(tmp_path: Path, monkeypatch) -> None:
    import threading

    from heterodyne.admind.approvals import ApproveBead

    async def scenario() -> None:
        main_thread = threading.get_ident()
        calls: list[int] = []
        port = ApproveBead(tmp_path / "unused")

        def identity() -> str:
            calls.append(threading.get_ident())
            return "a" * 64

        async def run(*args, **kwargs):
            return 75, HOLD, b""

        monkeypatch.setattr(port, "identity", identity)
        monkeypatch.setattr(port, "_run", run)
        await asyncio.to_thread(port.identity)
        calls.clear()
        result = await port.decide(APPROVE, "btq-ab12c")
        assert isinstance(result, MaintenanceHold)
        assert len(calls) == 2 and all(t != main_thread for t in calls)

    asyncio.run(scenario())


def test_changed_installed_adapter_remains_hold_without_native_read(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            monkeypatch.setattr(port, "identity", lambda: "b" * 64)
            before = snapshot(u)
            reads = port.reads
            await retire(u.daemon, command(u, notice), proof.token, notice)
            assert snapshot(u) == before and reads == port.reads
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", [False, True])
def test_final_launch_refuses_cold_or_changed_cache_without_hashing(
    tmp_path: Path, monkeypatch, drift: bool
) -> None:
    from heterodyne.admind.approvals import ApproveBead

    async def scenario() -> None:
        binary = tmp_path / "adapter"
        binary.write_bytes(b"original adapter")
        port = ApproveBead(binary)
        if drift:
            assert await asyncio.to_thread(port.identity)
            binary.write_bytes(b"changed adapter")

        def forbidden(*args, **kwargs):
            pytest.fail("final authority fence must neither hash nor launch on cache miss")

        monkeypatch.setattr(port, "identity", forbidden)
        monkeypatch.setattr(port, "_run", forbidden)
        with pytest.raises(BtqError, match="unavailable"):
            await port.decide(APPROVE, "btq-ab12c", on_launch=lambda: None)

    asyncio.run(scenario())
