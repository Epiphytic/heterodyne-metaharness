"""Plan 5i part 1 T7: a session event wakes its workstream's lane, and serve subscribes each runtime with the
ops its workstreams handle (D6)."""
import asyncio
import gc
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, accounts_at, git_repo, login_home

from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import SessionEvent
from heterodyne.wsd.scheduler import Outcome, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import SessionHandler, WorkstreamSettings


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _close_journals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drain every daemon a test made, then close every journal it opened (see test_wsd_daemon)."""
    opened: list[Journal] = []
    daemons: list[Wsd] = []
    real, real_wsd = Journal.__init__, Wsd.__init__

    def tracked(self: Journal, *args: Any, **kwargs: Any) -> None:
        opened.append(self)
        real(self, *args, **kwargs)

    def tracked_wsd(self: Wsd, *args: Any, **kwargs: Any) -> None:
        daemons.append(self)
        real_wsd(self, *args, **kwargs)

    monkeypatch.setattr(Journal, "__init__", tracked)
    monkeypatch.setattr(Wsd, "__init__", tracked_wsd)
    yield
    for d in daemons:
        assert d.drain_now(10), "a test's daemon still had a job running"
    for each in opened:
        if hasattr(each, "db"):
            each.close()
    gc.collect()


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    """Wait for `check` to hold, failing the test (never hanging) if it doesn't within `seconds`."""
    for _ in range(int(seconds / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting")


def settings(tmp_path: Path, names: tuple[str, ...] = (WS,), inbox: int = 3) -> WsdSettings:
    """No backstop or reconcile tick within a test: every pickup here comes from startup or a wake."""
    accounts = accounts_at(login_home(tmp_path / "home"))
    ws = tuple(WorkstreamSettings(n, {"default": git_repo(tmp_path / "repos" / n)}, "coder", "p-one",
                                  PROFILES, accounts=accounts) for n in names)
    return WsdSettings(tmp_path / "state" / "wsd", 3600, 3600, inbox, tmp_path / "btq", {}, ws, accounts)


def handled(wsd: Wsd, name: str, handlers: dict[str, SessionHandler]) -> None:
    sched = wsd.parts.schedulers[name]
    sched.d = replace(sched.d, session_handlers=handlers)


def spy_pickups(wsd: Wsd, order: list[str] | None = None) -> list[Trigger]:
    """Every trigger `pickup_one` is called with, in order; `order` also gets "pickup" for each."""
    seen: list[Trigger] = []
    real = wsd.pickup_one

    def spy(name: str, trigger: Trigger) -> Outcome:
        seen.append(trigger)
        if order is not None:
            order.append("pickup")
        return real(name, trigger)

    wsd.pickup_one = spy  # type: ignore[method-assign]
    return seen


def turn_ended(seen: list[Trigger]) -> list[Trigger]:
    return [t for t in seen if t.kind is TriggerKind.TURN_ENDED]


def event(n: int = 1) -> SessionEvent:
    return SessionEvent(f"sk-a:g1:e{n}", "close", WS, "btq-1", "sk-a", 1, n, "evidence")


def build(tmp_path: Path, s: WsdSettings, runtime: FakeRuntime) -> Wsd:
    return Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), runtime))


def h(parker: Parker, event: SessionEvent) -> None:
    del parker, event


def test_serve_subscribes_the_runtime_with_the_handled_ops(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path), runtime)
    handled(wsd, WS, {"stop": h, "close": h})
    order: list[str] = []
    real_subscribe = runtime.subscribe

    def subscribe(wake: Callable[[str], None], ops: Any) -> None:
        order.append("subscribe")
        real_subscribe(wake, ops)

    runtime.subscribe = subscribe  # type: ignore[method-assign]
    seen = spy_pickups(wsd, order)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: bool(seen))
        stop.set()
        await task

    asyncio.run(scenario())
    assert runtime.subscribed == {WS: frozenset({"close"})}
    assert order[0] == "subscribe" and order.count("subscribe") == 1
    assert seen[0] == Trigger(TriggerKind.STARTUP)


def test_ops_are_gated_per_workstream(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path, ("alpha", "beta", "gamma")), runtime)
    handled(wsd, "alpha", {"close": h})
    handled(wsd, "beta", {"ask": h})
    calls: list[object] = []
    real_subscribe = runtime.subscribe

    def subscribe(wake: Callable[[str], None], ops: Any) -> None:
        calls.append(dict(ops))
        real_subscribe(wake, ops)

    runtime.subscribe = subscribe  # type: ignore[method-assign]
    seen = spy_pickups(wsd)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: len(seen) == 3)
        stop.set()
        await task

    asyncio.run(scenario())
    assert calls == [{"alpha": frozenset({"close"}), "beta": frozenset({"ask"}), "gamma": frozenset()}]


def test_a_wake_from_another_thread_runs_a_turn_ended_pickup(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path), runtime)
    handled(wsd, WS, {"close": h})
    seen = spy_pickups(wsd)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: bool(seen))                 # the startup pickup: the runtime is subscribed
        sender = threading.Thread(target=runtime.event, args=(WS, event()))
        sender.start()
        sender.join()
        await until(lambda: bool(turn_ended(seen)))
        stop.set()
        await task

    asyncio.run(scenario())
    assert runtime.wakes == 1
    assert turn_ended(seen) == [Trigger(TriggerKind.TURN_ENDED)]
    assert turn_ended(seen)[0].ref is None
    assert runtime.logs[WS] == [event()]


def test_a_burst_of_wakes_runs_a_bounded_number_of_pickups(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path), runtime)
    seen = spy_pickups(wsd)
    release, blocked = threading.Event(), threading.Event()

    def slow() -> None:
        blocked.set()
        release.wait(10)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: bool(seen))
        await until(lambda: not wsd.lanes[WS].jobs)     # the startup pickup is done
        wsd.lanes[WS].submit("slow", slow)
        await until(blocked.is_set)
        burst = threading.Thread(target=lambda: [wsd.wake(WS) for _ in range(100)])
        burst.start()
        burst.join()
        await until(lambda: ("pickup", None) in wsd.lanes[WS].queued and not wsd.event_wakes)
        await asyncio.sleep(0.1)                        # every wake's callback has run by now
        release.set()
        await until(lambda: not wsd.waking and not wsd.lanes[WS].jobs)
        stop.set()
        await task

    asyncio.run(scenario())
    assert 1 <= len(turn_ended(seen)) <= 2


def test_a_wake_while_a_pickup_runs_after_reading_its_events_runs_one_more(tmp_path: Path) -> None:
    """A wake that lands after the running pickup has read the log must not join it: one more runs."""
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path), runtime)
    seen: list[Trigger] = []
    read, woken = threading.Barrier(2, timeout=10), threading.Event()
    real = wsd.pickup_one

    def spy(name: str, trigger: Trigger) -> Outcome:
        seen.append(trigger)
        outcome = real(name, trigger)               # the events are read by now
        if len(turn_ended(seen)) == 1:
            read.wait()                             # let the sender wake while this pickup still runs
            assert woken.wait(10)
        return outcome

    wsd.pickup_one = spy  # type: ignore[method-assign]

    def sender() -> None:
        runtime.event(WS, event(1))
        read.wait()
        runtime.event(WS, event(2))
        woken.set()

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: bool(seen))
        await until(lambda: not wsd.lanes[WS].jobs)     # the startup pickup is done
        thread = threading.Thread(target=sender)
        thread.start()
        await until(lambda: len(turn_ended(seen)) == 2)
        await until(lambda: not wsd.waking and not wsd.lanes[WS].jobs)
        await asyncio.to_thread(thread.join, 10)
        stop.set()
        await task

    asyncio.run(scenario())
    assert runtime.wakes == 2
    assert turn_ended(seen) == [Trigger(TriggerKind.TURN_ENDED)] * 2


def test_a_wake_before_serve_or_on_a_closed_loop_does_nothing(tmp_path: Path) -> None:
    wsd = build(tmp_path, settings(tmp_path), FakeRuntime())
    seen = spy_pickups(wsd)
    wsd.wake(WS)                                        # no loop yet
    closed = asyncio.new_event_loop()
    closed.close()
    wsd.loop = closed                                   # not stopping, but its loop has gone
    wsd.wake(WS)                                        # call_soon_threadsafe raises RuntimeError: suppressed
    assert seen == [] and not wsd.waking and not wsd.event_wakes and not wsd.lanes[WS].jobs


def test_a_wake_after_shutdown_does_nothing(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    wsd = build(tmp_path, settings(tmp_path), runtime)
    seen = spy_pickups(wsd)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: bool(seen))
        stop.set()
        await task

    asyncio.run(scenario())
    wsd.wake(WS)                                        # the loop has closed: no error, nothing started
    wsd.wake("nope")
    assert turn_ended(seen) == [] and not wsd.waking and not wsd.lanes[WS].jobs


def test_assemble_passes_the_inbox_limit(tmp_path: Path) -> None:
    s = settings(tmp_path, inbox=5)
    parts = assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime())
    assert parts.schedulers[WS].d.inbox_attempts == 5
