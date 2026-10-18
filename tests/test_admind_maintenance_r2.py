"""R2 relay failure regressions over fake ports and disposable SQLite only."""
import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from test_admind_approvals import APPROVE, MID, card_row, readout
from test_admind_maintenance import setup

from heterodyne.admind.approvals import BtqError, Busy, Readout
from heterodyne.admind.store import Store


@pytest.mark.parametrize("gate", ["group_ok", "observing", "changing"])
def test_transient_subscription_gate_never_claims_revocation(tmp_path: Path, gate: str) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            setattr(u.daemon, gate, gate == "changing")
            u.daemon.wallclock = lambda: 130.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
            assert not any("held (authority)" in text for text in u.texts())
            assert any("group-unverified" in text for text in u.texts())
            setattr(u.daemon, gate, gate != "changing")
            port.hold = False
            u.daemon.wallclock = lambda: 160.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 2
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["store", "notice"])
@pytest.mark.parametrize("retry", [False, True])
def test_hold_persistence_failure_restart_never_consumes_or_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, retry: bool
) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            if retry:
                await u.daemon.run_attempt(APPROVE, card_row())
                u.daemon.wallclock = lambda: 130.0
            with monkeypatch.context() as patch:
                def broken(*args: object, **kwargs: object) -> None:
                    raise sqlite3.OperationalError("offline injected failure")
                patch.setattr(u.store if failure == "store" else u.daemon,
                              "defer_maintenance" if failure == "store" else "ask_notice", broken)
                with pytest.raises(sqlite3.OperationalError):
                    await u.daemon.run_attempt(APPROVE, card_row(), maintenance_retry=retry)
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert u.store.inbound_status(MID) == "executing"
            calls = port.calls
            u.store.close()
            u.store = Store(u.settings.state_dir / "admind.db")
            u.build()
            u.daemon.approve_bead = port
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert u.store.inbound_status(MID) == "executing"
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == calls
            # A different decision/ref cannot dispose of stranded issuance either.
            port.current = readout(status="closed", decided=True, decision="approve",
                                   approved_by=APPROVE.operator, approved_digest=APPROVE.digest,
                                   via_ref="marmot:foreign", gate_valid=True, gate_preview=None)
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert u.store.inbound_status(MID) == "executing"
            # Matching real native readback can still settle without another effect.
            port.current = readout(status="closed", decided=True, decision="approve",
                                   approved_by=APPROVE.operator, approved_digest=APPROVE.digest,
                                   via_ref=APPROVE.ref, gate_valid=True, gate_preview=None)
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            assert u.store.current_attempt(APPROVE.ask_id) is None
            assert port.calls == calls
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_deny_hold_notice_describes_denial(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            deny = replace(APPROVE, action="deny", note="offline reason")
            u.store.db.execute("UPDATE ask_attempts SET action = 'deny', note = ?", (deny.note,))
            await u.daemon.run_attempt(deny, card_row())
            assert port.calls == 1
            assert any("denial received and held" in text for text in u.texts())
            assert not any("approval received and held" in text for text in u.texts())
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_unconfigured_relay_holds_with_accurate_notice(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.approve_bead = None
            u.daemon.wallclock = lambda: 130.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert any("approvals-unconfigured" in text for text in u.texts())
            assert u.store.inbound_status(MID) == "executing"
            assert port.calls == 1
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_native_conflict_suspends_periodic_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.wallclock = lambda: 130.0
            port.current = readout(status="closed", decided=True, decision="approve",
                                   approved_by=APPROVE.operator, approved_digest=APPROVE.digest,
                                   via_ref="marmot:other", gate_valid=True, gate_preview=None)
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            async def forbidden(*args: object, **kwargs: object) -> None:
                raise AssertionError("terminal hold must not keep polling")
            port.read = forbidden  # type: ignore[method-assign]
            u.daemon.wallclock = lambda: 160.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert u.store.current_attempt(APPROVE.ask_id) == APPROVE
            assert not u.store.maintenance_retryable()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_maintenance_reads_do_not_write_and_use_indexed_lookup(tmp_path: Path) -> None:
    u, _ = setup(tmp_path)
    try:
        assert u.store.defer_maintenance(APPROVE, 130.0)
        statements: list[str] = []
        u.store.db.set_trace_callback(statements.append)
        assert u.store.maintenance_pending()
        assert u.store.maintenance_retry_at(APPROVE.ask_id, MID) == 130.0
        assert not any(sql.startswith(("DELETE", "UPDATE", "INSERT")) for sql in statements)
        assert any("m.ask_id =" in sql and "m.message_id =" in sql for sql in statements)
    finally:
        u.store.close()


@pytest.mark.parametrize("failure", ["retry", "listing", "mixed"])
def test_retry_loop_failure_has_thirty_second_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    async def scenario() -> None:
        u, _ = setup(tmp_path)
        try:
            assert u.store.defer_maintenance(APPROVE, 0.0)
            async def broken(*args: object) -> None:
                raise sqlite3.OperationalError("offline locked")
            sleeps: list[float] = []
            async def stop(delay: float) -> None:
                sleeps.append(delay)
                raise asyncio.CancelledError
            if failure == "listing":
                def broken_listing() -> None:
                    raise sqlite3.OperationalError("offline list locked")
                monkeypatch.setattr(u.store, "maintenance_retryable", broken_listing)
            else:
                monkeypatch.setattr(u.daemon, "retry_maintenance", broken)
                if failure == "mixed":
                    monkeypatch.setattr(u.store, "maintenance_retryable",
                                        lambda: [(APPROVE.ask_id, MID, 100.5), (APPROVE.ask_id, MID, 0.0)])
            monkeypatch.setattr(asyncio, "sleep", stop)
            with pytest.raises(asyncio.CancelledError):
                await u.daemon.maintenance_retry_loop()
            assert sleeps == [30.0]
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,reason", [("busy", "native-busy"), ("error", "read-unavailable")])
def test_retry_preflight_failure_reason_is_not_changed_card(
    tmp_path: Path, failure: str, reason: str
) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            async def changed(bead: str, on_launch: object = None) -> Readout | Busy:
                if failure == "error":
                    raise BtqError("unavailable")
                return Busy(format=1, busy=True)
            port.read = changed  # type: ignore[method-assign]
            await u.daemon.run_attempt(APPROVE, card_row(), maintenance_retry=True)
            assert any(f"held ({reason})" in text for text in u.texts())
            assert not any("held (changed-card)" in text for text in u.texts())
            assert port.calls == 1
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_transient_policy_failure_at_prelaunch_never_suspends_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            original = u.daemon.load_operators
            calls = 0
            def intermittent() -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("offline policy unavailable at prelaunch")
                return original()
            monkeypatch.setattr(u.daemon, "load_operators", intermittent)
            u.daemon.wallclock = lambda: 130.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert any("held (policy-unavailable)" in text for text in u.texts())
            assert not any("held (authority)" in text for text in u.texts())
            assert u.store.maintenance_retryable()
            assert port.calls == 1
        finally:
            u.store.close()
    asyncio.run(scenario())
