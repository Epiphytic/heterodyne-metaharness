"""Proven pre-effect retirement: disposable Store and synthetic child ports only."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, operator
from test_admind_approvals import APPROVE, readout
from test_admind_maintenance import HOLD, Port, setup

from heterodyne.admind import commands
from heterodyne.admind.approvals import Attempt, MaintenanceHold, Readout
from heterodyne.admind.retirement import retire


def test_retirement_command_is_closed_and_bounded() -> None:
    command = commands.parse("!asks retire " + "ab" * 12)
    assert command is not None and command.arg == "retire" and command.rest == "ab" * 12


def test_new_attempt_counter_precedes_child_and_never_resets(tmp_path: Path) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.retirement.generation(APPROVE) == (0, True)
        assert u.store.issue_decision(APPROVE)
        assert u.store.retirement.generation(APPROVE) == (1, True)
        assert u.store.retirement.eligible(APPROVE) is None
    finally:
        u.store.close()


def test_exact_hold_chain_and_generation_aba(tmp_path: Path) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.issue_decision(APPROVE)
        hold = MaintenanceHold(HOLD, "a" * 64)
        with u.store.transaction():
            u.store.retirement.record_hold(APPROVE, hold)
            assert u.store.defer_maintenance(APPROVE, 130)
        proof = u.store.retirement.eligible(APPROVE)
        assert proof is not None and proof.invocations == 1
        assert u.store.issue_decision(APPROVE, maintenance_retry=True)
        assert u.store.retirement.eligible(APPROVE) is None
        with u.store.transaction():
            u.store.retirement.record_hold(APPROVE, hold)
            assert u.store.defer_maintenance(APPROVE, 160)
        newer = u.store.retirement.eligible(APPROVE)
        assert newer is not None and newer.invocations == 2 and newer.token != proof.token
    finally:
        u.store.close()


def test_legacy_marker_cannot_construct_proof(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            await u.daemon.run_attempt(APPROVE, row)
            assert port.calls == 1
            assert u.store.retirement.eligible(APPROVE) is None
        finally:
            u.store.close()

    asyncio.run(scenario())


# Deliberate synthetic adapter identities exercise the trusted adapter result boundary. No live native
# process is created; the actual ApproveBead protocol is tested separately with disposable executables.


class ProvenPort(Port):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.after_read: Callable[[], None] | None = None

    def identity(self) -> str:
        return "a" * 64

    async def read(self, bead: str, on_launch: Callable[[], None] | None = None) -> Readout:
        self.reads += 1
        if self.after_read is not None:
            self.after_read()
        return await super().read(bead, on_launch)

    async def decide(
        self, a: Attempt, bead: str, on_launch: Callable[[], None] | None = None
    ) -> tuple[int, str] | MaintenanceHold:
        self.calls += 1
        if on_launch:
            on_launch()
        return MaintenanceHold(HOLD, self.identity())


async def eligible(tmp_path: Path):
    u, _ = setup(tmp_path)
    port = ProvenPort()
    u.daemon.approve_bead = port
    row = u.store.ask(APPROVE.ask_id)
    assert row is not None
    await u.daemon.run_attempt(APPROVE, row)
    proof = u.store.retirement.eligible(APPROVE)
    assert proof is not None
    notice = "ef" * 32
    key = f"retirement:{APPROVE.ask_id}:{proof.token}"
    found = [r for r in u.store.pending() if r.key == key]
    assert len(found) == 1
    u.store.mark_sent(found[0].seq, notice)
    return u, port, proof, notice


def command(u, target: str, sender: str = OPERATOR_HEX) -> str:
    mid = u.mid()
    assert u.store.claim_inbound(mid, reply_to=target, sender_key=sender)
    return mid


def snapshot(u):
    return tuple(
        tuple(u.store.db.execute(query).fetchall())
        for query in (
            "SELECT * FROM ask_attempts",
            "SELECT * FROM maintenance_proofs",
            "SELECT * FROM ask_maintenance",
            "SELECT * FROM asks",
        )
    )


def test_retire_is_atomic_idempotent_and_restart_safe(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            async with u.daemon.work_lock:
                await retire(u.daemon, mid, proof.token, notice)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.status == "cancelled" and row.outcome == "maintenance-retired"
            assert u.store.inbound_status(APPROVE.message_id) == "done"
            assert u.store.inbound_status(mid) == "done"
            assert u.store.current_attempt(APPROVE.ask_id) is None
            assert u.store.maintenance_pending() == []
            attempt = u.store.db.execute(
                "SELECT action,operator,ref,digest,settled,exit_status,invocations FROM ask_attempts"
            ).fetchone()
            assert attempt == (
                APPROVE.action,
                APPROVE.operator,
                APPROVE.ref,
                APPROVE.digest,
                "untouched",
                75,
                1,
            )
            receipts = u.store.db.execute("SELECT * FROM maintenance_retirements").fetchall()
            ack = [r for r in u.store.pending() if r.key.startswith("retired:")]
            assert len(receipts) == len(ack) == 1 and ack[0].reply_to == notice
            reads = port.reads
            u.build()
            u.daemon.approve_bead = port
            await retire(u.daemon, mid, proof.token, notice)
            assert u.store.db.execute("SELECT * FROM maintenance_retirements").fetchall() == receipts
            assert port.reads == reads and port.calls == 1
            assert len([r for r in u.store.pending() if r.key.startswith("retired:")]) == 1
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        "thread",
        "token",
        "digest",
        "issuing",
        "historical",
        "missing",
        "corrupt",
        "unverified",
        "no-sender",
        "native-unavailable",
    ],
)
def test_ineligible_preserves_original_attempt_and_proof(tmp_path: Path, change: str) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            target, token = notice, proof.token
            mid = command(u, notice)
            if change == "thread":
                target = "12" * 32
            elif change == "token":
                token = "0" * 24
            elif change == "digest":
                u.store.db.execute("UPDATE asks SET digest=?", ("0" * 64,))
            elif change == "issuing":
                assert u.store.issue_decision(APPROVE, maintenance_retry=True)
            elif change == "historical":
                u.store.db.execute("UPDATE ask_attempts SET proof_capable_since_begin=0")
            elif change == "missing":
                u.store.db.execute("DELETE FROM maintenance_proofs")
            elif change == "corrupt":
                u.store.db.execute("UPDATE maintenance_proofs SET body='{}'")
            elif change == "unverified":
                u.daemon.observing = False
            elif change == "no-sender":
                u.store.db.execute("UPDATE inbound SET sender_key=NULL WHERE message_id=?", (mid,))
            elif change == "native-unavailable":
                port.after_read = lambda: (_ for _ in ()).throw(OSError("offline"))
            before = snapshot(u)
            await retire(u.daemon, mid, token, target)
            assert snapshot(u) == before
            assert u.store.inbound_status(APPROVE.message_id) == "executing"
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone() == (0,)
            assert port.calls == 1
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_outbox_failure_rolls_back_retirement_and_original_inbound(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            before = snapshot(u)

            def fail(*args, **kwargs):
                raise OSError("offline simulated full disk")

            monkeypatch.setattr(u.daemon, "post", fail)
            with pytest.raises(OSError):
                await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before
            assert u.store.inbound_status(APPROVE.message_id) == "executing"
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone() == (0,)
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_second_current_operator_can_retire_removed_original(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path)
        try:
            u.settings = replace(u.settings, operators=(operator("second", SECOND_HEX),))
            u.store.set("group_operators", '["' + SECOND_HEX + '"]')
            u.build()
            port = ProvenPort()
            u.daemon.approve_bead = port
            mid = command(u, notice, SECOND_HEX)
            assert not u.daemon.authorised(APPROVE.message_id)
            assert u.daemon.authorised(mid)
            await retire(u.daemon, mid, proof.token, notice)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.status == "cancelled" and row.decided_by == "second"
            assert u.store.current_attempt(APPROVE.ask_id) is None
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_current_authority_and_generation_rechecked_after_native_await(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)

            def issue() -> None:
                assert u.store.issue_decision(APPROVE, maintenance_retry=True)

            port.after_read = issue
            await retire(u.daemon, mid, proof.token, notice)
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert u.store.retirement.generation(APPROVE) == (2, True)
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone() == (0,)
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "native,expected",
    [
        ({"status": "closed", "decided": True, "decision": "deny", "denied_by": "other"}, "blocked"),
        ({"kind_approval": False}, "blocked"),
        ({"status": "in_progress"}, "blocked"),
        (
            {
                "status": "closed",
                "decided": True,
                "decision": APPROVE.action,
                "approved_by": APPROVE.operator,
                "approved_digest": APPROVE.digest,
                "via_ref": APPROVE.ref,
            },
            "approved",
        ),
    ],
)
def test_native_conflict_and_exact_recorded_are_distinct(tmp_path: Path, native: dict, expected: str) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            port.current = readout(**native)
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.status == expected
            assert port.calls == 1
            count = u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone()[0]
            assert count == (0 if expected == "approved" else 1)
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad", ["", "A" * 24, "a" * 23, "a" * 25, "a" * 24 + " x", "a" * 24 + "\n", "x" * 1100]
)
def test_malformed_retire_grammar_never_reaches_agent(bad: str) -> None:
    if bad == "a" * 24 + "\n":  # ordinary command whitespace is accepted, not part of the token
        assert commands.parse("!asks retire " + bad) is not None
    else:
        with pytest.raises(commands.CommandError):
            commands.parse("!asks retire " + bad)


def test_missing_invocation_proof_can_never_be_healed_by_later_hold(tmp_path: Path) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.issue_decision(APPROVE)
        # Simulated process death after intent, before obtaining exact child proof.
        assert u.store.defer_maintenance(APPROVE, 130)
        assert u.store.issue_decision(APPROVE, maintenance_retry=True)
        with u.store.transaction():
            assert not u.store.retirement.record_hold(APPROVE, MaintenanceHold(HOLD, "a" * 64))
            assert u.store.defer_maintenance(APPROVE, 160)
        assert u.store.retirement.generation(APPROVE) == (2, True)
        assert u.store.retirement.eligible(APPROVE) is None
    finally:
        u.store.close()


def test_historical_migration_flag_never_promotes_even_after_exact_hold(tmp_path: Path) -> None:
    from heterodyne.admind.store import Store

    u, _ = setup(tmp_path)
    path = u.store.path
    # Reproduce an old real row/table without either additive column, preserving payload/checks.
    u.store.db.execute("ALTER TABLE ask_attempts DROP COLUMN invocations")
    u.store.db.execute("ALTER TABLE ask_attempts DROP COLUMN proof_capable_since_begin")
    original = u.store.db.execute("SELECT * FROM ask_attempts").fetchall()
    u.store.close()
    store = Store(path)
    try:
        assert (
            store.db.execute(
                "SELECT ask_id,message_id,action,operator,ref,digest,note,started_at,"
                "exit_status,settled FROM ask_attempts"
            ).fetchall()
            == original
        )
        assert store.retirement.generation(APPROVE) == (0, False)
        assert store.issue_decision(APPROVE)
        with store.transaction():
            assert not store.retirement.record_hold(APPROVE, MaintenanceHold(HOLD, "a" * 64))
            assert store.defer_maintenance(APPROVE, 130)
        assert store.retirement.generation(APPROVE) == (1, False)
        assert store.retirement.eligible(APPROVE) is None
    finally:
        store.close()


@pytest.mark.parametrize(
    "envelope",
    [b"", HOLD + b"x", HOLD[:-1] + b',"extra":true}', HOLD.replace(b"not-issued", b"issued"), b"\xff"],
)
def test_arbitrary_hold_marker_cannot_become_retirement_proof(tmp_path: Path, envelope: bytes) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.issue_decision(APPROVE)
        assert not u.store.retirement.record_hold(APPROVE, MaintenanceHold(envelope, "a" * 64))
        assert u.store.defer_maintenance(APPROVE, 130)
        assert u.store.retirement.eligible(APPROVE) is None
    finally:
        u.store.close()


def test_retirement_available_with_twenty_asks_and_suspended_overlay(tmp_path: Path) -> None:
    from test_admind_approvals import card_row

    async def scenario() -> None:
        u, _, proof, notice = await eligible(tmp_path)
        try:
            for i in range(19):
                u.store.insert_ask(replace(card_row(), ask_id=f"x{i:03d}"), None)
            assert len(u.store.capacity_asks()) == 20
            assert u.store.defer_maintenance(APPROVE, 160, reason="authority")
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.status == "cancelled"
            assert len(u.store.capacity_asks()) == 19
            assert u.store.db.execute("SELECT COUNT(*) FROM asks").fetchone() == (20,)
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_durable_command_thread_is_not_caller_supplied_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, "11" * 32)
            before = snapshot(u)
            reads = port.reads
            await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before and port.reads == reads
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_authority_revoked_during_native_read_never_commits(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            mid = command(u, notice)
            port.after_read = lambda: u.store.set("group_operators", "[]")
            before = snapshot(u)
            await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone() == (0,)
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_no_per_retry_proof_or_notice_growth_and_explicit_inspection_refresh(tmp_path: Path) -> None:
    from heterodyne.admind.retirement import publish_notice

    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            for _ in range(5):
                await u.daemon.run_attempt(APPROVE, row, maintenance_retry=True, prechecked=port.current)
            current = u.store.retirement.eligible(APPROVE)
            assert current is not None and current.invocations == 6
            assert u.store.retirement.select(proof.token, notice) is None
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_proofs").fetchone() == (1,)
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_recovery_notices").fetchone() == (1,)
            keys = u.store.db.execute("SELECT key FROM outbox WHERE key LIKE 'retirement:%'").fetchall()
            assert len(keys) == 1
            with u.store.transaction():
                publish_notice(u.daemon, APPROVE, refresh=True)
            keys = u.store.db.execute("SELECT key FROM outbox WHERE key LIKE 'retirement:%'").fetchall()
            assert len(keys) == 2
        finally:
            u.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [-1, (1 << 63) - 1, "corrupt"])
def test_invalid_counter_refuses_before_overlay_deletion(tmp_path: Path, count) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.issue_decision(APPROVE)
        assert u.store.defer_maintenance(APPROVE, 130)
        u.store.db.execute("UPDATE ask_attempts SET invocations=?", (count,))
        before = snapshot(u)
        assert not u.store.issue_decision(APPROVE, maintenance_retry=True)
        assert snapshot(u) == before
    finally:
        u.store.close()


def test_actual_adapter_captures_closed_stdout_and_real_file_identity(tmp_path: Path) -> None:
    import hashlib

    from heterodyne.admind.approvals import ApproveBead

    async def scenario() -> None:
        binary = tmp_path / "approve-bead"
        binary.write_text("#!/bin/sh\nprintf '%s' '" + HOLD.decode() + "'\nexit 75\n")
        binary.chmod(0o700)
        port = ApproveBead(binary)
        result = await port.decide(APPROVE, "btq-ab12c")
        assert isinstance(result, MaintenanceHold)
        assert result.stdout == HOLD
        assert result.adapter_identity == hashlib.sha256(binary.read_bytes()).hexdigest()
        binary.write_text(
            "#!/bin/sh\nprintf '%s' '" + (HOLD[:-1] + b',"extra":true}').decode() + "'\nexit 75\n"
        )
        assert not isinstance(await port.decide(APPROVE, "btq-ab12c"), MaintenanceHold)

    asyncio.run(scenario())


def test_identity_rejects_nonregular_without_waiting(tmp_path: Path) -> None:
    import os

    from heterodyne.admind.approvals import ApproveBead

    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert ApproveBead(fifo).identity() == ""


@pytest.mark.parametrize(
    "status",
    ["open", "answered", "approved", "denied", "uncertain", "cancelled", "blocked", "stale", "superseded"],
)
def test_every_non_deciding_state_is_ineligible_and_untouched(tmp_path: Path, status: str) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            u.store.db.execute("UPDATE asks SET status=?", (status,))
            mid = command(u, notice)
            before = snapshot(u)
            reads = port.reads
            await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before and port.reads == reads
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_unsent_notice_has_no_thread_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            u.store.db.execute("UPDATE outbox SET status='pending' WHERE key LIKE 'retirement:%'")
            mid = command(u, notice)
            before = snapshot(u)
            reads = port.reads
            await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before and port.reads == reads
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_different_command_on_retired_notice_never_reads_or_changes_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port, proof, notice = await eligible(tmp_path)
        try:
            await retire(u.daemon, command(u, notice), proof.token, notice)
            before = snapshot(u)
            reads = port.reads
            mid = command(u, notice)
            await retire(u.daemon, mid, proof.token, notice)
            assert snapshot(u) == before and port.reads == reads
            assert u.store.inbound_status(mid) == "done"
            assert u.store.db.execute("SELECT COUNT(*) FROM maintenance_retirements").fetchone() == (1,)
        finally:
            u.store.close()

    asyncio.run(scenario())


def test_overlay_and_exact_proof_publication_roll_back_together(tmp_path: Path) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.issue_decision(APPROVE)
        before = snapshot(u)
        with pytest.raises(OSError), u.store.transaction():
            assert u.store.retirement.record_hold(APPROVE, MaintenanceHold(HOLD, "a" * 64))
            assert u.store.defer_maintenance(APPROVE, 130)
            raise OSError("simulated crash before commit")
        assert snapshot(u) == before
        assert u.store.retirement.generation(APPROVE) == (1, True)
        assert u.store.retirement.eligible(APPROVE) is None
    finally:
        u.store.close()


def test_retiring_denial_preserves_original_denial_provenance(tmp_path: Path) -> None:
    from test_admind_approvals import card_row
    from test_admind_r1 import Unit

    async def scenario() -> None:
        u = Unit(tmp_path)
        a = replace(APPROVE, action="deny", note="original denial")
        port = ProvenPort()
        u.daemon.approve_bead = port
        try:
            u.store.insert_ask(card_row(), None)
            assert u.store.claim_inbound(a.message_id, sender_key=OPERATOR_HEX)
            assert u.store.begin_attempt(a)
            await u.daemon.run_attempt(a, card_row())
            proof = u.store.retirement.eligible(a)
            assert proof is not None
            key = f"retirement:{a.ask_id}:{proof.token}"
            item = next(r for r in u.store.pending() if r.key == key)
            notice = "de" * 32
            u.store.mark_sent(item.seq, notice)
            mid = command(u, notice)
            await u.daemon.handle(mid, "!asks retire " + proof.token, notice)
            original = u.store.db.execute(
                "SELECT action,note,settled,exit_status FROM ask_attempts"
            ).fetchone()
            assert original == ("deny", "original denial", "untouched", 75)
            assert port.calls == 1
            assert u.daemon.held == []
        finally:
            u.store.close()

    asyncio.run(scenario())
