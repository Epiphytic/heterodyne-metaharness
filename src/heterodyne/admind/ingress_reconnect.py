"""Finite local subscription cutover; no approval/attempt or process-restart authority.

All state is owned by the daemon event loop. Reader cancellation, transport custody,
queued-event retention and critical-section admission share one owner here.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import msgspec

from heterodyne.marmot.control import InboundMessage, ReactionAdded, Subscription

if TYPE_CHECKING:
    from heterodyne.admind.daemon import Admind

BYTE_LIMIT = 8 * 1024 * 1024
EVENT_LIMIT = 256
REQUEST_LIMIT = 64
RECOVERY_SECONDS = 125.0
TEARDOWN_SECONDS = 2.0


@dataclass
class Capacity:
    """One shared normalized retained-payload budget, including dequeued events."""

    limit: int = BYTE_LIMIT
    used: int = 0
    episode: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class RetainedQueue[T: InboundMessage | ReactionAdded](asyncio.Queue[T]):
    """Queue-compatible admission: dequeue does not release bytes or event slots."""

    def __init__(self, capacity: Capacity) -> None:
        super().__init__(maxsize=EVENT_LIMIT)
        self.capacity = capacity
        self._sizes: deque[int] = deque()
        self._inflight: deque[int] = deque()

    def full(self) -> bool:
        return len(self._sizes) + len(self._inflight) >= self.maxsize

    def put_nowait(self, item: T) -> None:
        # Exact closed msgspec structures retain all text/metadata. Do not retain this
        # temporary serialization alongside the original event (no hidden payload copy).
        if type(item) not in (InboundMessage, ReactionAdded):
            raise TypeError("unsupported retained event")
        size = len(msgspec.json.encode(item))
        if self.full() or self.capacity.used + size > self.capacity.limit:
            raise asyncio.QueueFull
        super().put_nowait(item)
        self._sizes.append(size)
        self.capacity.used += size

    async def put(self, item: T) -> None:
        while True:
            try:
                self.put_nowait(item)
                return
            except asyncio.QueueFull:
                await self.capacity.changed.wait()

    def get_nowait(self) -> T:
        item = super().get_nowait()
        self._inflight.append(self._sizes.popleft())
        return item

    def task_done(self) -> None:
        if not self._inflight:
            raise ValueError("retained event was not dequeued")
        super().task_done()
        self.capacity.used -= self._inflight.popleft()
        self.capacity.episode = False
        # A waiter on put() needs waking only when original completion frees a slot.
        self.capacity.changed.set()
        self.capacity.changed = asyncio.Event()


class Operation(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    instance: str
    generation: int
    request_id: str
    state: Literal["pending", "observing", "failed"]
    reason: Literal["accepted", "observing", "failed-teardown", "failed-observation", "shutdown"]


class Result(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: Literal["accepted", "pending", "observing", "failed", "busy", "refused"]
    reason: Literal[
        "accepted",
        "observing",
        "failed-teardown",
        "failed-observation",
        "shutdown",
        "busy",
        "stale",
        "request-conflict",
    ]
    operation: Operation | None = None


class Reconnect:
    """One outer supervisor, one exact reader and at most one retained cutover task."""

    def __init__(self, daemon: Admind) -> None:
        self.d = daemon
        self.intent = False
        self.paused = False
        self.released = asyncio.Event()
        self.released.set()
        self.ready = asyncio.Event()
        self.ready.set()  # existing natural-loss behavior changes only after explicit cutover
        self.changed = asyncio.Event()
        self.reader: asyncio.Task[None] | None = None
        self.transport: Subscription | None = None
        self.operation: asyncio.Task[None] | None = None
        self.records: OrderedDict[str, Operation] = OrderedDict()
        self.teardown_unknown = False
        self.running = False
        self.recovery_seconds = RECOVERY_SECONDS
        self.teardown_seconds = TEARDOWN_SECONDS

    def invalidate(self) -> None:
        d = self.d
        d.observing = d.acked = d.reading = d.group_ok = False
        d.sub_gen += 1
        self.ready.clear()

    def observed(self, generation: int) -> None:
        if generation == self.d.sub_gen and self.d.acked and self.d.group_ok and self.d.reading:
            self.paused = False
            self.ready.set()

    async def worker_ready(self, component: Literal["normal", "custody"]) -> None:
        while self.paused or self.intent:
            self.d.ingress.phase(component, "waiting_observation")
            if self.intent:
                await self.released.wait()
            else:
                await self.ready.wait()

    async def admit(self, instance: str, generation: int, request_id: str) -> Result:
        old = self.records.get(request_id)
        if old is not None:
            if (old.instance, old.generation) != (instance, generation):
                return Result("refused", "request-conflict")
            return Result(old.state, old.reason, old)
        d = self.d
        if instance != d.ingress.instance or generation != d.sub_gen:
            return Result("refused", "stale")
        locks = (d.work_lock, d.transition_lock, d.custody_lock)
        recovering = d.ingress.active("recovery")
        if (
            not self.running
            or self.intent
            or d.shutting_down
            or d.changing
            or any(lock.locked() for lock in locks)
            or recovering
        ):
            return Result("busy", "busy")
        # No suspension is possible acquiring an asyncio.Lock known free in this
        # event loop. The intent fence prevents any new participating admission.
        self.intent = True
        self.released.clear()
        held: list[asyncio.Lock] = []
        try:
            for name, lock in zip(("work", "transition", "custody"), locks, strict=True):
                if lock.locked():
                    return Result("busy", "busy")
                await lock.acquire()
                held.append(lock)
                d.ingress.owner(name, "reconnect")
            while len(self.records) >= REQUEST_LIMIT:
                key = next((key for key, value in self.records.items() if value.state != "pending"), None)
                if key is None:
                    return Result("busy", "busy")
                del self.records[key]
            record = Operation(instance, generation, request_id, "pending", "accepted")
            self.records[request_id] = record
            # Strong lifecycle custody exists BEFORE the accepted response. The
            # socket task never awaits or owns this operation's cancellation.
            owned = held
            self.operation = asyncio.create_task(self._cutover(record, owned))
            self.operation.add_done_callback(lambda _: self._cancelled_before_start(record, owned))
            held = []  # ownership transferred to lifecycle task
            return Result("accepted", "accepted", record)
        finally:
            if held:
                for lock in reversed(held):
                    lock.release()
                for name in ("work", "transition", "custody"):
                    d.ingress.owner(name, None)
            if self.operation is None or self.operation.done():
                self.intent = False
                self.released.set()

    def _release(self, held: list[asyncio.Lock]) -> None:
        for lock in reversed(held):
            lock.release()
        held.clear()
        for name in ("work", "transition", "custody"):
            self.d.ingress.owner(name, None)
        self.intent = False
        self.released.set()
        self.changed.set()

    def _cancelled_before_start(self, record: Operation, held: list[asyncio.Lock]) -> None:
        # A Task cancelled before its first instruction never enters try/finally.
        # The lifecycle completion callback retains exact lock custody in that case.
        if held:
            self.paused = True
            self.invalidate()
            self.records[record.request_id] = msgspec.structs.replace(
                record, state="failed", reason="shutdown"
            )
            self._release(held)

    async def _stop_reader(self, deadline: float) -> bool:
        task, transport = self.reader, self.transport
        if task is not None and not task.done():
            task.cancel()
        if transport is not None:
            if not await transport.close(deadline):
                return False
        if task is not None:
            done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
            if not done:
                return False
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.result()
        return True

    def _start_reader(self, deadline: float | None = None) -> None:
        if self.reader is not None and not self.reader.done():
            raise RuntimeError("original subscription still owned")
        self.invalidate()
        self.transport = Subscription()
        generation = self.d.sub_gen
        self.reader = asyncio.create_task(self.d.read_subscription(generation, deadline, self.transport))

    async def _cutover(self, record: Operation, held: list[asyncio.Lock]) -> None:
        d = self.d
        deadline = time.monotonic() + self.recovery_seconds
        reason: Literal["observing", "failed-teardown", "failed-observation", "shutdown"] = (
            "failed-observation"
        )
        self.paused = True
        self.invalidate()
        self.changed.set()
        try:
            d.audit.write("ingress", action="reconnect-accepted")
            d.ingress.phase("reader", "closing")
            stopped = await self._stop_reader(min(deadline, time.monotonic() + self.teardown_seconds))
            self.teardown_unknown = not stopped
            if not stopped:
                reason = "failed-teardown"
            elif not d.shutting_down:
                self._start_reader(deadline)
                async with asyncio.timeout_at(deadline):
                    await self.ready.wait()
                reason = "observing"
            else:
                reason = "shutdown"
        except TimeoutError:
            reason = "failed-observation"
        except asyncio.CancelledError:
            reason = "shutdown"
            raise
        except Exception:
            # Fixed public reason, retained original handles; never infer cessation.
            self.teardown_unknown = True
            reason = "failed-teardown"
        finally:
            self.records[record.request_id] = msgspec.structs.replace(
                record, state="observing" if reason == "observing" else "failed", reason=reason
            )
            self._release(held)
            d.audit_quietly("ingress", action="reconnect-observing" if reason == "observing" else reason)

    async def _backoff(self, seconds: float) -> bool:
        """Wake explicit control without abandoning the existing backoff sleeper."""
        sleeper = asyncio.ensure_future(self.d._sleep(seconds))  # pyright: ignore[reportPrivateUsage]
        changed = asyncio.create_task(self.changed.wait())
        try:
            await asyncio.wait({sleeper, changed}, return_when=asyncio.FIRST_COMPLETED)
            return changed.done()
        finally:
            for task in (sleeper, changed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleeper, changed, return_exceptions=True)

    async def run(self) -> None:
        self.running = True
        delay = 1.0
        try:
            while not self.d.shutting_down:
                if self.intent or self.teardown_unknown:
                    self.changed.clear()
                    if self.intent or self.teardown_unknown:
                        await self.changed.wait()
                    continue
                if self.reader is None or self.reader.done():
                    if self.reader is not None:
                        self.invalidate()
                        # Positive close must accompany reader cessation. This also
                        # fences natural teardown failures, not just explicit ones.
                        original = self.reader
                        stopped = await self._stop_reader(time.monotonic() + self.teardown_seconds)
                        # Admission may take custody during close(). The automatic
                        # supervisor must not invalidate the accepted cutover's generation.
                        if self.intent or self.reader is not original:
                            continue
                        if not stopped:
                            self.teardown_unknown = True
                            self.paused = True
                            self.invalidate()
                            continue
                        self.d.ingress.count("reconnects")
                        self.d.ingress.phase("reader", "backoff")
                        self.invalidate()
                        self.changed.clear()
                        if await self._backoff(delay):
                            continue
                        delay = min(delay * 2, 30.0)
                    self._start_reader()
                reader = self.reader
                if reader is None:
                    raise RuntimeError("subscription custody missing")
                try:
                    await asyncio.shield(reader)
                    delay = 1.0
                except asyncio.CancelledError:
                    owner = asyncio.current_task()
                    if owner is not None and owner.cancelling():
                        raise
                    # The exact child was deliberately cancelled; never cancel siblings.
                except Exception:
                    if self.d.reading:
                        delay = 1.0
                    self.d.ingress.phase("reader", "failed")
        finally:
            self.running = False
            self.d.shutting_down = True
            if self.operation is not None and not self.operation.done():
                self.operation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.operation
            await self._stop_reader(time.monotonic() + self.teardown_seconds)
