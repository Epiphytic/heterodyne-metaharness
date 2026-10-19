"""Cancellation must survive a failed, drained verifier pass; disposable Store only."""

import asyncio
import threading

import pytest

from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_projection as projection
from heterodyne.admind.store import Store


def test_cancelled_verifier_does_not_replace_shutdown_with_pass_failure(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def failing_pass(store):
        started.set()
        assert release.wait(5), "test did not release disposable verifier"
        raise f.Invalid("projection-unavailable")

    monkeypatch.setattr(projection, "verify_pass", failing_pass)
    store = Store(tmp_path / "admind.db")

    async def scenario():
        task = asyncio.create_task(projection.worker(store))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)  # Enter cancellation drainage before the pass fails.
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    store.close()
