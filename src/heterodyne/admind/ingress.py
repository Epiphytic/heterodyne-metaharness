"""Memory-only ingress observations; a responsive snapshot is not delivery clearance.

PR122 status subset: byte accounting and explicit reconnect remain unavailable. This
module never reads the Store, policy, message payloads or the upstream transport.
"""

import asyncio
import math
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal, get_args

import msgspec

from heterodyne.admind import ctl
from heterodyne.admind.audit import Audit

SOCKET = "ingress.sock"
MAX_REQUEST = 4096
MAX_REPLY = 16384
CLIENT_SECONDS = 5.0
MAX_COUNTER = (1 << 64) - 1
TaskName = Literal["reader", "normal", "custody", "recovery"]
TaskState = Literal["unknown", "pending", "running", "done", "cancelled", "failed"]
LockName = Literal["work", "transition", "custody"]
Owner = Literal[
    "normal", "custody", "membership", "rearm", "recovery", "custody_control", "autobump", "unknown", "none"
]
ReaderPhase = Literal["awaiting_ack", "checking_group", "reading", "backoff", "failed", "stopped"]
WorkerPhase = Literal[
    "waiting_queue", "waiting_work_lock", "waiting_custody_lock", "handling", "failed", "stopped"
]
Phase = Literal["unknown", ReaderPhase, WorkerPhase]
CounterName = Literal[
    "decoded_any",
    "enqueued_normal",
    "enqueued_custody",
    "authenticated_normal",
    "authenticated_custody",
    "claimed",
    "completed_normal",
    "completed_custody",
    "reconnects",
    "dropped_self",
    "dropped_guard",
    "dropped_malformed",
    "dropped_replay",
    "dropped_capacity",
    "handler_failed",
    "correlated",
    "ask_answered",
]
COUNTERS: tuple[CounterName, ...] = get_args(CounterName)
TASKS: tuple[TaskName, ...] = get_args(TaskName)
LOCKS: tuple[LockName, ...] = get_args(LockName)
READER_PHASES = frozenset(get_args(ReaderPhase))
WORKER_PHASES = frozenset(get_args(WorkerPhase))
OWNERS = frozenset(get_args(Owner))


class Request(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["admind.ingress.request.v1"]
    op: Literal["status"]


class Counter(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: CounterName
    value: int
    overflowed: bool
    last_age_seconds: float | None


class Task(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: TaskName
    state: TaskState
    phase: Phase


class Lock(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: LockName
    owner: Owner
    held_age_seconds: float | None


class Snapshot(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    instance: str
    age_seconds: float
    generation: int
    acked: bool
    reading: bool
    observing: bool
    group_ok: bool
    membership_epoch: int
    tasks: tuple[Task, ...]
    locks: tuple[Lock, ...]
    counters: tuple[Counter, ...]
    normal_depth: int
    custody_depth: int
    release: None = None
    unavailable: tuple[
        Literal["queue_bytes", "durable_disposition_completion", "last_error_category"], ...
    ] = (
        "queue_bytes",
        "durable_disposition_completion",
        "last_error_category",
    )
    retained_bytes: None = None
    byte_accounting: Literal["unavailable"] = "unavailable"
    reconnect: Literal["not-implemented"] = "not-implemented"
    upstream_delivery_verified: Literal[False] = False
    history_replay_verified: Literal[False] = False


class Reply(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: Literal["ok", "refused", "unavailable"]
    reason: Literal["snapshot", "peer", "malformed", "unavailable"]
    snapshot: Snapshot | None = None
    schema: Literal["admind.ingress.reply.v1"] = "admind.ingress.reply.v1"


class Observation:
    """One event-loop-owned observer. Callers pass fixed labels, never event data."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.started = clock()
        self.instance = uuid.uuid4().hex
        self._tasks: dict[TaskName, asyncio.Task[object]] = {}
        self._phases: dict[str, Phase] = {}
        self._counts = dict.fromkeys(COUNTERS, 0)
        self._overflow: set[CounterName] = set()
        self._marked: dict[CounterName, float] = {}
        self._owners: dict[LockName, tuple[Owner, float]] = {}

    def count(self, name: CounterName) -> None:
        value = self._counts[name]
        if value == MAX_COUNTER:
            self._overflow.add(name)
        else:
            self._counts[name] = value + 1
        self.mark(name)

    def mark(self, name: CounterName) -> None:
        if name not in self._counts:
            raise ValueError("unknown ingress counter")
        self._marked[name] = self.clock()

    def phase(self, component: TaskName, value: Phase) -> None:
        allowed = READER_PHASES if component == "reader" else WORKER_PHASES
        if component not in TASKS or value not in allowed:
            raise ValueError("unknown ingress phase")
        self._phases[component] = value

    def task(self, name: TaskName, task: asyncio.Task[object]) -> None:
        if name not in TASKS:
            raise ValueError("unknown ingress task")
        self._tasks[name] = task

    def owner(self, lock: LockName, category: Owner | None) -> None:
        if lock not in LOCKS or (category is not None and category not in OWNERS):
            raise ValueError("unknown ingress owner")
        if category is None:
            self._owners.pop(lock, None)
        else:
            self._owners[lock] = (category, self.clock())

    @staticmethod
    def _age(current: float, previous: float) -> float:
        value = current - previous
        return max(0.0, value) if math.isfinite(value) else 0.0

    def _task(self, name: TaskName) -> Task:
        task = self._tasks.get(name)
        phase = self._phases.get(name, "unknown")
        if task is None:
            state: TaskState = "unknown"
        elif task.cancelled():
            state = "cancelled"
        elif task.done():
            state = "failed" if task.exception() is not None else "done"
        else:
            state = "running" if name in self._phases else "pending"
        return Task(name, state, phase)

    def snapshot(
        self,
        *,
        normal_depth: int,
        custody_depth: int,
        locks: dict[str, bool],
        generation: int,
        acked: bool,
        reading: bool,
        observing: bool,
        group_ok: bool = False,
        membership_epoch: int = 0,
    ) -> Reply:
        current = self.clock()
        held: list[Lock] = []
        for name in LOCKS:
            owner = self._owners.get(name) if locks.get(name, False) else None
            category: Owner = owner[0] if owner else ("unknown" if locks.get(name, False) else "none")
            held.append(Lock(name, category, None if owner is None else self._age(current, owner[1])))
        counters = tuple(
            Counter(
                name,
                self._counts[name],
                name in self._overflow,
                None if name not in self._marked else self._age(current, self._marked[name]),
            )
            for name in COUNTERS
        )
        return Reply(
            "ok",
            "snapshot",
            Snapshot(
                self.instance,
                self._age(current, self.started),
                generation,
                acked,
                reading,
                observing,
                group_ok,
                membership_epoch,
                tuple(self._task(name) for name in TASKS),
                tuple(held),
                counters,
                normal_depth,
                custody_depth,
            ),
        )


def server(path: Path, audit: Audit, snapshot: Callable[[], Reply]) -> ctl.JsonSocketServer[Request, Reply]:
    """The fixed same-UID read-only endpoint; polls never append audit records."""

    async def handle(_request: Request, peer: ctl.Peer) -> Reply:
        if peer.uid != os.getuid():
            return Reply("refused", "peer")
        return snapshot()

    return ctl.JsonSocketServer(
        path,
        handle,
        audit,
        kind="ingress",
        max_request=MAX_REQUEST,
        max_reply=MAX_REPLY,
        request_type=Request,
        check=lambda _request: None,
        refused=lambda _reason: Reply("refused", "malformed"),
        failed=lambda: Reply("unavailable", "unavailable"),
        describe=lambda _request: {},
        audit_requests=False,
    )


def _valid_snapshot(snapshot: Snapshot) -> bool:
    """Reject incompatible or unbounded server metadata before printing it."""
    if (
        len(snapshot.instance) != 32
        or any(c not in "0123456789abcdef" for c in snapshot.instance)
        or tuple(t.name for t in snapshot.tasks) != TASKS
        or tuple(lock.name for lock in snapshot.locks) != LOCKS
        or tuple(counter.name for counter in snapshot.counters) != COUNTERS
    ):
        return False
    numbers = (snapshot.generation, snapshot.membership_epoch, snapshot.normal_depth, snapshot.custody_depth)
    if any(not 0 <= number <= MAX_COUNTER for number in numbers):
        return False
    ages = [snapshot.age_seconds]
    for counter in snapshot.counters:
        if not 0 <= counter.value <= MAX_COUNTER:
            return False
        if counter.last_age_seconds is not None:
            ages.append(counter.last_age_seconds)
    ages.extend(lock.held_age_seconds for lock in snapshot.locks if lock.held_age_seconds is not None)
    return all(math.isfinite(age) and age >= 0 for age in ages)


async def request(path: Path) -> Reply:
    """Bound the whole client operation, including send, response and connection cleanup."""
    try:
        async with asyncio.timeout(CLIENT_SECONDS):
            reply = await ctl.request(
                path,
                Request("admind.ingress.request.v1", "status"),
                timeout=CLIENT_SECONDS,
                reply_type=Reply,
                max_reply=MAX_REPLY,
                require_uid=os.getuid(),
                abort_close=True,
            )
    except TimeoutError:
        raise ctl.CtlUnavailable("ingress-unavailable") from None
    if (reply.result, reply.reason) not in {
        ("ok", "snapshot"),
        ("refused", "peer"),
        ("refused", "malformed"),
        ("unavailable", "unavailable"),
    } or (reply.result == "ok") != (reply.snapshot is not None):
        raise ctl.CtlInvalidReply("ingress-schema")
    if reply.snapshot is not None and not _valid_snapshot(reply.snapshot):
        raise ctl.CtlInvalidReply("ingress-schema")
    return reply
