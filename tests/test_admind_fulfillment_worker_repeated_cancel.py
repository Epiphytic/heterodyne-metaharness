"""A bounded verifier callback must drain despite repeated worker cancellation."""

import asyncio
import logging
import threading

import pytest

from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_projection as projection
from heterodyne.admind.store import Store


@pytest.mark.parametrize("verifier_fails", [False, True])
def test_repeated_cancellation_keeps_loop_available_until_verifier_drains(
    tmp_path, monkeypatch, caplog, verifier_fails
):
    started, release = threading.Event(), threading.Event()
    callback_completed, callback_timed_out = threading.Event(), threading.Event()
    store = Store(tmp_path / "admind.db")

    async def scenario():
        loop = asyncio.get_running_loop()

        async def authority_callback():
            return "disposable-authority-readback"

        def verifier(_store):
            started.set()
            assert release.wait(3), "disposable verifier was not released"
            callback = asyncio.run_coroutine_threadsafe(authority_callback(), loop)
            try:
                assert callback.result(timeout=2) == "disposable-authority-readback"
            except TimeoutError:
                callback_timed_out.set()
                raise
            callback_completed.set()
            if verifier_fails:
                raise f.Invalid("retained-secret-must-not-enter-diagnostic")
            return 0

        monkeypatch.setattr(projection, "verify_pass", verifier)
        task = asyncio.create_task(projection.worker(store))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel("original-cancellation")
            await asyncio.sleep(0)  # First cancellation is draining the actual pass.
            task.cancel("second-cancellation")
            release.set()
            with pytest.raises(asyncio.CancelledError) as cancelled:
                await asyncio.wait_for(task, 5)
            assert cancelled.value.args == ("original-cancellation",)
            assert callback_completed.is_set()
            assert not callback_timed_out.is_set()
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)

    try:
        with caplog.at_level(logging.WARNING, logger=projection.__name__):
            asyncio.run(scenario())
        messages = [r.getMessage() for r in caplog.records if r.name == projection.__name__]
        assert messages == (["fulfillment-verification-failed-during-shutdown"] if verifier_fails else [])
        assert "retained-secret" not in caplog.text
    finally:
        store.close()
