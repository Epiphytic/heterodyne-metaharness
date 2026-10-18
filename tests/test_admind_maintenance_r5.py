"""Latched consent stays suspended across in-flight readback and rearm."""
import asyncio

import pytest
from test_admind_approvals import APPROVE, MID, card_row
from test_admind_maintenance import setup


@pytest.mark.parametrize("operation", ["issue", "defer"])
def test_suspended_consent_cannot_be_reactivated(tmp_path, operation):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.latch("offline anomaly")
            u.store.delete("latched")
            if operation == "issue":
                assert not u.store.issue_decision(APPROVE, maintenance_retry=True)
            else:
                assert not u.store.defer_maintenance(APPROVE, 160.0)
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.outcome == "maintenance-held:latched"
            assert not u.store.maintenance_retryable()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_latch_persists_if_overlay_suspension_fails(tmp_path, monkeypatch):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            def unavailable(*args, **kwargs):
                raise OSError("offline injected storage failure")
            monkeypatch.setattr(u.store, "defer_maintenance", unavailable)
            with pytest.raises(OSError):
                u.daemon.latch("offline anomaly")
            assert u.daemon.latched()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_failed_hold_cas_finishes_inbound(tmp_path, monkeypatch):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            monkeypatch.setattr(u.store, "defer_maintenance", lambda *args, **kwargs: False)
            u.daemon.hold_maintenance(APPROVE, "latched")
            assert u.store.inbound_status(MID) == "done"
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_latch_during_native_readback_survives_immediate_rearm(tmp_path, monkeypatch):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            original = u.daemon.read_and_settle
            async def racing(a, bead):
                result = await original(a, bead)
                u.daemon.latch("offline anomaly")
                u.store.delete("latched")
                return result
            monkeypatch.setattr(u.daemon, "read_and_settle", racing)
            port.hold = False
            u.daemon.wallclock = lambda: 160.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
            assert not u.store.maintenance_retryable()
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_latch_during_child_survives_immediate_rearm(tmp_path, monkeypatch):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            original = port.decide
            async def racing(a, bead, on_launch=None):
                result = await original(a, bead, on_launch)
                u.daemon.latch("offline anomaly")
                u.store.delete("latched")
                return result
            monkeypatch.setattr(port, "decide", racing)
            await u.daemon.run_attempt(APPROVE, card_row())
            assert port.calls == 1
            assert not u.store.maintenance_retryable()
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.outcome == "maintenance-held:latched"
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_rearm_finishes_suspension_after_partial_latch_failure(tmp_path, monkeypatch):
    async def scenario():
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            original = u.store.defer_maintenance
            def unavailable(*args, **kwargs):
                raise OSError("offline injected storage failure")
            monkeypatch.setattr(u.store, "defer_maintenance", unavailable)
            with pytest.raises(OSError):
                u.daemon.latch("offline anomaly")
            monkeypatch.setattr(u.store, "defer_maintenance", original)
            assert (await u.daemon.rearm())[0] == "rearmed"
            assert not u.daemon.latched()
            assert not u.store.maintenance_retryable()
        finally:
            u.store.close()
    asyncio.run(scenario())
