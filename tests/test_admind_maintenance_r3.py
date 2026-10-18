"""Restart boundaries after a real decision invocation, over disposable offline ports."""
import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from test_admind_approvals import APPROVE, MID, card_row, readout
from test_admind_maintenance import setup

from heterodyne.admind.approvals import Attempt
from heterodyne.admind.store import Store


@pytest.mark.parametrize("native", ["untouched", "blocked", "recorded"])
def test_ordinary_cancelled_child_keeps_r26_restart_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: str,
) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            async def cancelled(
                a: Attempt, bead: str, on_launch: Callable[[], None] | None = None,
            ) -> tuple[int, str]:
                assert on_launch is not None
                on_launch()
                raise asyncio.CancelledError
            monkeypatch.setattr(port, "decide", cancelled)
            with pytest.raises(asyncio.CancelledError):
                await u.daemon.run_attempt(APPROVE, card_row())
            if native != "untouched":
                port.current = readout(
                    status="closed", decided=True, decision="approve", approved_by=APPROVE.operator,
                    approved_digest=APPROVE.digest,
                    via_ref=APPROVE.ref if native == "recorded" else "marmot:foreign",
                    gate_valid=True, gate_preview=None,
                )
            u.store.close()
            u.store = Store(u.settings.state_dir / "admind.db")
            u.build()
            u.daemon.approve_bead = port
            await u.daemon.reconcile_asks(u.store.recovery_snapshot())
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None
            assert row.status == {"untouched": "open", "blocked": "blocked", "recorded": "approved"}[native]
            assert u.store.current_attempt(APPROVE.ask_id) is None
            assert u.store.inbound_status(MID) == "done"
            assert not u.store.maintenance_pending()
        finally:
            u.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("observe_during_latch", [False, True])
def test_latch_suspends_old_consent_after_rearm(tmp_path: Path, observe_during_latch: bool) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            await u.daemon.run_attempt(APPROVE, card_row())
            u.daemon.latch("offline anomaly")
            u.daemon.wallclock = lambda: 130.0
            if observe_during_latch:
                await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            u.store.delete("latched")
            port.hold = False
            u.daemon.wallclock = lambda: 160.0
            await u.daemon.retry_maintenance(APPROVE.ask_id, MID)
            assert port.calls == 1
            assert not u.store.maintenance_retryable()
            row = u.store.ask(APPROVE.ask_id)
            assert row is not None and row.outcome == "maintenance-held:latched"
        finally:
            u.store.close()
    asyncio.run(scenario())


def test_failed_issue_cas_finishes_original_inbound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        u, port = setup(tmp_path)
        try:
            monkeypatch.setattr(u.store, "issue_decision", lambda *args, **kwargs: False)
            await u.daemon.run_attempt(APPROVE, card_row())
            assert port.calls == 0
            assert u.store.inbound_status(MID) == "done"
            assert any("changed" in text.lower() for text in u.texts())
        finally:
            u.store.close()
    asyncio.run(scenario())
