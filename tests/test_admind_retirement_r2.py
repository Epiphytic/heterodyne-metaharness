"""R2 durability and delivery regressions; synthetic ports and private SQLite only."""

import asyncio
from pathlib import Path

import pytest
from test_admind_approvals import APPROVE
from test_admind_retirement import command, eligible

from heterodyne.admind.approvals import BtqError
from heterodyne.admind.retirement import publish_notice, retire


@pytest.mark.parametrize("status", ["delayed", "quarantined", "suppressed"])
def test_held_delivery_is_not_failed(tmp_path: Path, status: str) -> None:
    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path)
        try:
            key = f"retirement:{APPROVE.ask_id}:{proof.token}"
            u.store.db.execute("UPDATE outbox SET status=?,message_id=NULL WHERE key=?", (status, key))
            before = tuple(u.store.db.execute("SELECT * FROM outbox"))
            publish_notice(u.daemon, APPROVE, refresh=True)
            assert tuple(u.store.db.execute("SELECT * FROM outbox")) == before
            seq = u.store.db.execute("SELECT seq FROM outbox WHERE key=?", (key,)).fetchone()[0]
            u.store.mark_sent(seq, notice)
            assert u.store.retirement.select(proof.token, notice) == (APPROVE, proof)
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_notice_error_cannot_erase_observed_hold(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        u, port, _, _ = await eligible(tmp_path)
        try:
            def broken(*args, **kwargs):
                raise RuntimeError("synthetic enqueue failure")
            monkeypatch.setattr(u.daemon, "ask_notice", broken)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            proof = u.store.retirement.eligible(APPROVE)
            assert proof is not None and proof.invocations == 2
            assert u.store.maintenance_pending()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_generic_unavailable_never_proves_not_spawned(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, _ = await eligible(tmp_path)
        try:
            async def unavailable(*args, **kwargs):
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


def test_same_command_replay_precedes_revoked_authority(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            receipt = u.store.retirement.receipt(proof.token, notice)
            assert receipt is not None
            before = tuple(u.store.db.execute("SELECT * FROM outbox"))
            reads = port.reads
            monkeypatch.setattr(u.daemon, "authorised", lambda _: False)
            assert await retire(u.daemon, mid, proof.token, notice) == receipt
            assert before == tuple(u.store.db.execute("SELECT * FROM outbox"))
            assert port.reads == reads
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_terminal_close_removes_only_active_proof_mapping(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, _, _, _ = await eligible(tmp_path)
        try:
            assert u.store.close_attempt(APPROVE.ask_id, APPROVE.message_id,
                expect_status="deciding", settled="recorded", exit_status=0,
                new_status="approved", outcome="recorded", decided_by=APPROVE.operator)
            assert u.store.retirement._proof(APPROVE) is None
            assert not tuple(u.store.db.execute("SELECT * FROM maintenance_recovery_notices"))
            assert tuple(u.store.db.execute("SELECT * FROM ask_attempts"))
            assert tuple(u.store.db.execute("SELECT * FROM outbox"))
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_retry_notice_does_not_append_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, _, _ = await eligible(tmp_path)
        try:
            before = tuple(u.store.db.execute("SELECT * FROM maintenance_notice_history"))
            for _ in range(3):
                row = u.store.ask(APPROVE.ask_id)
                assert row is not None
                await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
                publish_notice(u.daemon, APPROVE, refresh=True)
            assert tuple(u.store.db.execute("SELECT * FROM maintenance_notice_history")) == before
            assert len(tuple(u.store.db.execute("SELECT * FROM maintenance_recovery_notices"))) == 1
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_not_spawned_proof_and_overlay_share_rollback(tmp_path: Path, monkeypatch) -> None:
    from heterodyne.admind import approvals

    async def scenario() -> None:
        u, port, proof, _ = await eligible(tmp_path)
        try:
            async def unavailable(*args, **kwargs):
                # The fallback lets this test expose the pre-repair transaction bug on R2's base.
                kind = getattr(approvals, "NotSpawned", None)
                raise kind() if kind is not None else BtqError("unavailable")

            def unavailable_store(*args, **kwargs):
                raise RuntimeError("synthetic overlay capacity failure")

            port.decide = unavailable
            monkeypatch.setattr(u.store, "defer_maintenance", unavailable_store)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            with pytest.raises(RuntimeError, match="synthetic overlay"):
                await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            assert u.store.retirement._proof(APPROVE) == proof
            assert u.store.retirement.generation(APPROVE) == (2, True)
            assert u.store.retirement.eligible(APPROVE) is None
            assert not u.store.maintenance_pending()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_same_command_replay_rejects_rebound_sender(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            assert u.store.retirement.replay(mid, proof.token, notice) is not None
            monkeypatch.setattr(u.store, "inbound_sender", lambda _: "ff" * 32)
            assert u.store.retirement.replay(mid, proof.token, notice) is None
            assert u.store.retirement.replay(mid, "ff" * 12, notice) is None
            assert u.store.retirement.replay(mid, proof.token, "ff" * 32) is None
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_inspection_publishes_at_most_one_and_scans_once(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import replace

    from fakes.settings import OPERATOR_HEX
    from test_admind_approvals import card_row
    from test_admind_maintenance import HOLD

    from heterodyne.admind.approvals import MaintenanceHold

    async def scenario() -> None:
        u, _, _, _ = await eligible(tmp_path)
        try:
            for i in range(4):
                row = replace(card_row(), ask_id=f"zz{i:02d}")
                a = replace(APPROVE, ask_id=row.ask_id, message_id="fa" + f"{i + 1:062x}")
                u.store.insert_ask(row, None)
                assert u.store.claim_inbound(a.message_id, sender_key=OPERATOR_HEX)
                assert u.store.begin_attempt(a) and u.store.issue_decision(a)
                assert u.store.retirement.record_hold(a, MaintenanceHold(HOLD, "a" * 64))
                assert u.store.defer_maintenance(a, 130)
            original_pending = u.store.maintenance_pending
            original_eligible = u.store.retirement.eligible
            scans = 0
            proofs: list[str] = []

            def pending():
                nonlocal scans
                scans += 1
                return original_pending()

            def eligibility(a):
                proofs.append(a.ask_id)
                return original_eligible(a)

            monkeypatch.setattr(u.store, "maintenance_pending", pending)
            monkeypatch.setattr(u.store.retirement, "eligible", eligibility)
            mid = command(u, "")
            before = len(tuple(u.store.db.execute("SELECT key FROM outbox WHERE key LIKE 'retirement:%'")))
            await u.daemon.list_asks(mid)
            after = len(tuple(u.store.db.execute("SELECT key FROM outbox WHERE key LIKE 'retirement:%'")))
            assert after - before == 1
            assert scans == 1 and len(proofs) == 5 and len(set(proofs)) == 5
            assert not tuple(u.store.db.execute("SELECT * FROM maintenance_notice_history"))
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_legacy_proof_and_history_remain_readable_without_backfill(tmp_path: Path) -> None:
    import json

    from heterodyne.admind.retirement import canonical, digest

    async def scenario() -> None:
        u, _, proof, _ = await eligible(tmp_path)
        try:
            value = json.loads(proof.body)
            assert "consecutive" not in value
            value["consecutive"] = value["invocations"] - value["not_spawned"]
            body = canonical(value)
            u.store.db.execute("UPDATE maintenance_proofs SET body=?,sha256=?", (body, digest(body)))
            legacy = u.store.retirement.eligible(APPROVE)
            assert legacy is not None and legacy.body == body
            u.store.db.execute("INSERT INTO maintenance_notice_history VALUES ('old','ask','mid','hash')")
            before = tuple(u.store.db.execute("SELECT * FROM maintenance_notice_history"))
            u.store.retirement.migrate()
            assert before == tuple(u.store.db.execute("SELECT * FROM maintenance_notice_history"))
            assert u.store.retirement.eligible(APPROVE) == legacy
            stored = u.store.retirement._proof(APPROVE)
            assert stored is not None and stored.sha256 == digest(body)
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_real_adapter_structural_not_spawned_sites(tmp_path: Path, monkeypatch) -> None:
    from heterodyne.admind import approvals
    from heterodyne.admind.approvals import ApproveBead, NotSpawned

    async def scenario() -> None:
        binary = tmp_path / "adapter"
        binary.write_bytes(b"offline source; never executed")
        port = ApproveBead(binary)
        launched: list[bool] = []
        assert port.identity()

        def unavailable(*args, **kwargs):
            raise OSError("synthetic Popen rejection")

        monkeypatch.setattr(approvals.reap, "spawn", unavailable)
        with pytest.raises(NotSpawned):
            await port.decide(APPROVE, "offline", lambda: launched.append(True))
        assert not launched

    asyncio.run(scenario())


def test_older_selector_never_reports_another_attempt_receipt(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, old, old_notice = await eligible(tmp_path)
        try:
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            proof = u.store.retirement.eligible(APPROVE)
            assert proof is not None
            publish_notice(u.daemon, APPROVE, refresh=True)
            pending = next(r for r in u.store.pending() if r.key.startswith("retirement:"))
            notice = "96" * 32
            u.store.mark_sent(pending.seq, notice)
            await retire(u.daemon, command(u, notice), proof.token, notice)
            assert u.store.retirement.receipt(old.token, old_notice) is not None
            # An unrelated historical attempt makes old-token association unknowable without
            # retry-history rows. Exact selected-token receipt remains independently identifiable.
            u.store.db.execute(
                "INSERT INTO ask_attempts (ask_id,message_id,action,operator,ref,digest,note,"
                "started_at,exit_status,settled) SELECT ask_id,?,action,operator,ref,digest,note,"
                "started_at,exit_status,settled FROM ask_attempts WHERE message_id=?",
                ("fb" * 32, APPROVE.message_id),
            )
            assert u.store.retirement.receipt(old.token, old_notice) is None
            assert u.store.retirement.receipt(proof.token, notice) is not None
        finally:
            u.store.close()
    asyncio.run(scenario())
