"""Daemon-only serial state owner and a separate bounded backup-copy worker.

No live producer is registered by this early slice. Future producer registrations need their own
approved implementation/activation; generic effect-wake never constructs an effect from caller data.
"""

import concurrent.futures as cf
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from heterodyne.wsd.backup_cycles import (
    Identity,
    NativeStatus,
    copy_bundle,
    discard_incomplete,
    new_attempt,
    retain,
    verify_bundle,
)
from heterodyne.wsd.effects import EffectStore, Health

T = TypeVar("T")


class EffectOwner:
    def __init__(self, path: Path, installation: str, generation: str, owner_hash: str | None = None) -> None:
        self.path = path
        self.installation = installation
        self.generation = generation
        self.identity = Identity(installation, generation, owner_hash or "")
        self.writer = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="wsd-effect-state")
        self.copier = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="wsd-backup-copy")
        self.store: EffectStore | None = None
        self.cache = Health("unready", 0, 0)
        self.cancel = threading.Event()
        self.copy: cf.Future[dict[str, str]] | None = None
        self.closing = False
        self.sample: cf.Future[None] | None = None
        self.opened = self.writer.submit(self._open)
        self.opened.add_done_callback(self._consume)

    def _open(self) -> None:
        self.store = EffectStore(self.path, self.installation, self.generation)
        self.cache = self.store.cache
        if self.store.db is not None:
            pending = self.store.pending_backup()
            if pending is not None and pending[2]:
                cycle, native, attempt = pending
                def recover() -> dict[str, str]:
                    directory = self.path.parent / "paired-backups"
                    try:
                        return verify_bundle(directory, attempt, cycle, native, self.identity)
                    except (OSError, ValueError):
                        # Exclusive owner restart fences the old local worker, not the native effect.
                        discard_incomplete(directory, attempt)
                        raise
                self.copy = self.copier.submit(recover)
                self.copy.add_done_callback(lambda future: self._settle(cycle, future))
            elif pending is not None:
                # This process owns the recovered store; the former local worker is gone.
                self.store.finish_backup(pending[0], "failed")
                self.cache = self.store.cache

    def _settle(self, cycle: int, future: cf.Future[dict[str, str]]) -> None:
        try:
            result = future.result()
        except Exception:
            # Future completion positively fences this local copy worker. This is not proof
            # of native backup success/unsent; it only terminates the failed local companion.
            result = {"status": "failed"}

        def write() -> None:
            if self.store is not None:
                self.store.finish_backup(
                    cycle, result["status"], result.get("attempt", ""), result.get("manifest_sha256", "")
                )
                self.cache = self.store.cache

        self.writer.submit(write).add_done_callback(self._consume)

    def _consume(self, future: cf.Future[None]) -> None:
        if future.cancelled() or future.exception() is not None:
            self.cache = Health("error", time.time(), self.cache.revision + 1)

    def call(self, fn: Callable[[EffectStore], T]) -> cf.Future[T]:
        if self.closing:
            raise RuntimeError("effect owner is closing")

        def run() -> T:
            if self.store is None:
                raise RuntimeError("effect owner unavailable")
            result = fn(self.store)
            self.cache = self.store.cache
            return result

        return self.writer.submit(run)

    def refresh(self) -> None:
        """One outstanding cache sample; a blocked writer leaves the old timestamp visibly stale."""
        if not self.closing and (self.sample is None or self.sample.done()):
            self.sample = self.call(lambda store: store.refresh())
            self.sample.add_done_callback(self._consume)

    def backup(
        self, cycle: int, native: NativeStatus, directory: Path, sources: tuple[Path, Path]
    ) -> cf.Future[str]:
        def admit(store: EffectStore) -> str:
            status = store.begin_backup(cycle, native)
            if status != "queued":
                return status
            attempt = new_attempt()
            store.backup_attempt(cycle, attempt)

            def copy() -> dict[str, str]:
                result = copy_bundle(directory, sources, cycle, native, self.cancel, self.identity, attempt)
                try:
                    retain(directory, self.identity, time.time(), attempt)
                except (OSError, ValueError):
                    logging.getLogger(__name__).warning(
                    "paired backup retention held; sealed result retained")
                return result

            self.copy = self.copier.submit(copy)
            self.copy.add_done_callback(lambda future: self._settle(cycle, future))
            return status

        return self.call(admit)

    def close(self) -> None:
        """Blocking shutdown worker, never called on the event loop."""
        self.closing = True
        self.cancel.set()
        self.copier.shutdown(wait=True, cancel_futures=True)

        def close_store() -> None:
            if self.store is not None:
                self.store.close()

        self.writer.submit(close_store).result()
        self.writer.shutdown(wait=True, cancel_futures=True)
