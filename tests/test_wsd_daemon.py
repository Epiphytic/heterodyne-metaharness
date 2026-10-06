import asyncio
import contextlib
import gc
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, git_repo

from heterodyne.wsd import cli, ctl, daemon, ids, journal
from heterodyne.wsd.daemon import NEEDS_A_HUMAN, Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.journal import Journal, JournalBusy
from heterodyne.wsd.runtime import Session
from heterodyne.wsd.scheduler import Outcome, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.states import BeadState, Reason, WsState
from heterodyne.wsd.workstream import WorkstreamSettings


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _close_journals(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every journal a test opened, so their descriptors don't close later inside another test's
    count of open descriptors (test_wsd_gate checks that a refusal leaks none)."""
    opened: list[Journal] = []
    real = Journal.__init__

    def tracked(self: Journal, *args: Any, **kwargs: Any) -> None:
        opened.append(self)
        real(self, *args, **kwargs)

    monkeypatch.setattr(Journal, "__init__", tracked)
    yield
    for each in opened:
        if hasattr(each, "db"):                 # a journal whose open failed has nothing to close
            each.close()
    gc.collect()


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    """Wait for `check` to hold, failing the test (never hanging) if it doesn't within `seconds`."""
    for _ in range(int(seconds / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting")


def settings(tmp_path: Path) -> WsdSettings:
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES)
    return WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,))


def test_startup_recovers_before_accepting_events(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    world.add("btq-1")
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(world), runtime))
    seen_socket_during_recovery: list[bool] = []
    original = daemon.recover_one

    def spy(name: str):  # noqa: ANN202
        seen_socket_during_recovery.append(s.socket.exists())
        return original(name)

    daemon.recover_one = spy  # type: ignore[method-assign]

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        await until(s.socket.exists)
        reply = await ctl.request(s.socket, ctl.CtlRequest("status"))
        stop.set()
        await task
        return reply

    reply = asyncio.run(scenario())
    assert seen_socket_during_recovery == [False]
    assert reply.data[WS]["state"] == "running" and reply.data[WS]["beads"] == "btq-1=running"
    assert not s.socket.exists()
    assert world.claims == ["btq-1"]


def test_tick_and_backstop(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), runtime))

    async def scenario() -> tuple[ctl.CtlReply, ctl.CtlReply]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        await until(s.socket.exists)
        idle = await ctl.request(s.socket, ctl.CtlRequest("tick", job="pickup"))
        world.add("btq-1")
        await until(lambda: bool(world.claims))       # the 0.05 s backstop picks it up
        bad = await ctl.request(s.socket, ctl.CtlRequest("tick", job="reconcile", ws="nope"))
        stop.set()
        await task
        return idle, bad

    idle, bad = asyncio.run(scenario())
    assert idle.data == {WS: {"outcome": "nothing"}}
    assert world.claims == ["btq-1"]
    assert bad.result == "refused"


def test_failed_recovery_is_retried_before_pickup(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    world.down = True
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    assert daemon.startup() == {WS: daemon.pickup_one(WS, _trigger())}
    assert world.claims == [] and WS not in daemon.recovered
    world.down = False
    daemon.pickup_one(WS, _trigger())
    assert WS in daemon.recovered and world.claims == ["btq-1"]


def _trigger():  # noqa: ANN202
    from heterodyne.wsd.scheduler import Trigger, TriggerKind
    return Trigger(TriggerKind.BACKSTOP)


def test_corrupt_journal_refuses_to_start(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    s.state_dir.mkdir(parents=True, mode=0o700)
    s.journal.write_bytes(b"garbage" * 200)
    world = World(tmp_path / "btq-state")
    assert cli.run(s, factory(world), FakeRuntime()) == cli.EX_CONFIG
    assert s.journal.read_bytes() == b"garbage" * 200
    assert "left in place" in capsys.readouterr().err


def test_second_instance_refuses(tmp_path: Path) -> None:
    s = settings(tmp_path)
    fd = instance_lock(s.instance_lock)
    try:
        assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    finally:
        os.close(fd)


def test_wsctl_pause_goes_through_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    s = replace(settings(tmp_path), backstop_seconds=3600)    # only explicit ticks pick up
    world = World(tmp_path / "btq-state")
    monkeypatch.setattr(cli, "_settings", lambda: s)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))

    async def scenario() -> list[int]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        await until(s.socket.exists)
        codes = [await asyncio.to_thread(cli.wsctl_main, ["pause", WS])]
        world.add("btq-1")
        codes.append(await asyncio.to_thread(cli.wsd_main, ["tick", "pickup"]))
        codes.append(len(world.claims))
        codes.append(await asyncio.to_thread(cli.wsctl_main, ["resume", WS]))  # resume runs a pickup
        stop.set()
        await task
        return codes

    assert asyncio.run(scenario()) == [0, 0, 0, 0]
    assert world.claims == ["btq-1"]
    out = capsys.readouterr().out
    assert f"{WS}: paused." in out and f"{WS}: resumed." in out and "outcome=started" in out


def test_wsctl_pause_needs_a_running_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda: s)
    assert cli.wsctl_main(["pause", WS]) == 1
    assert "not running" in capsys.readouterr().err
    assert not (tmp_path / "btq-state").exists()        # nothing was set behind wsd's back


def test_pause_flag_failure_is_not_acknowledged(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    world.down = True
    reply = asyncio.run(daemon.handle(ctl.CtlRequest("pause", ws=WS)))
    assert reply.result == "failed" and "nothing is acknowledged" in reply.message


def test_pause_without_a_workstream_is_refused(tmp_path: Path) -> None:
    s = settings(tmp_path)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "w")), FakeRuntime()))

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        await until(s.socket.exists)
        reply = await ctl.request(s.socket, ctl.CtlRequest("pause"))
        stop.set()
        await task
        return reply

    assert asyncio.run(scenario()).result == "refused"


def test_acknowledged_pause_shows_at_once(tmp_path: Path) -> None:
    s = settings(tmp_path)
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(World(tmp_path / "btq-state")), FakeRuntime()))
    daemon.startup()
    assert journal.snapshot(WS).state is WsState.IDLE
    reply = asyncio.run(daemon.handle(ctl.CtlRequest("pause", ws=WS)))
    assert reply.result == "ok"
    assert journal.snapshot(WS).state is WsState.PAUSED       # no pickup ran in between


def test_failed_recovery_publishes_its_hold(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.down = True
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(world), FakeRuntime()))
    daemon.recover_one(WS)
    snap = journal.snapshot(WS)
    assert snap.state is WsState.HELD and Reason.BEADS_UNREACHABLE in snap.holds


# --- deviations: tests the plan does not have (Tasks 3, 7 and 8 notes) ---


def journal_files(s: WsdSettings) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(s.state_dir.glob("wsd.db*"))}


def hold_exclusively(path: Path) -> sqlite3.Connection:
    """Another process writes to the journal and keeps it locked (SQLite's exclusive locking mode keeps
    the lock after the commit), so no other connection can read or write it."""
    other = sqlite3.connect(path, isolation_level=None)
    other.execute("PRAGMA locking_mode=EXCLUSIVE")
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) "
                  "VALUES ('t', '-', NULL, 'x', '', NULL)")
    other.execute("COMMIT")
    return other


def test_busy_journal_at_open_exits_1_and_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """Task 3: a journal another process holds is not a configuration error. wsd exits 1 (the unit
    restarts it), says retrying is safe, and touches neither the journal, the queue nor the runtime."""
    s = settings(tmp_path)
    Journal(s.journal).close()
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    world.add("btq-1")
    monkeypatch.setattr(journal, "BUSY_TIMEOUT", 0)       # read at connect: the open itself must not wait
    other = hold_exclusively(s.journal)
    try:
        before = journal_files(s)
        assert cli.run(s, factory(world), runtime) == 1
        assert journal_files(s) == before
    finally:
        other.close()
    assert "the journal is locked by another process; retrying is safe" in capsys.readouterr().err
    assert world.claims == [] and runtime.launches == [] and not s.socket.exists()


class LockingRuntime(FakeRuntime):
    """Another process takes the journal's write lock the first time recovery lists sessions."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.other: sqlite3.Connection | None = None

    def sessions(self, ws: str) -> list[Session]:
        if self.other is None:
            self.other = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            self.other.execute("BEGIN IMMEDIATE")
        return super().sessions(ws)


def test_busy_journal_during_startup_recovery_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})   # a hold to write
    monkeypatch.setattr(journal, "BUSY_TIMEOUT", 0)
    runtime = LockingRuntime(s.journal)
    try:
        assert cli.run(s, factory(world), runtime) == 1
    finally:
        if runtime.other is not None:
            runtime.other.rollback()
            runtime.other.close()
    assert runtime.other is not None
    assert "retrying is safe" in capsys.readouterr().err
    assert world.claims == [] and not s.socket.exists()


def test_timer_outlives_a_busy_journal(tmp_path: Path) -> None:
    """Task 3: a tick fails on a journal another process holds, and so does recording the failure. The
    timer keeps ticking, the workstream is recovered again once the journal is free, and then picks up."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(world), FakeRuntime()))
    assert wsd.startup() == {WS: Outcome.NOTHING} and WS in wsd.recovered
    j.db.execute("PRAGMA busy_timeout = 0")
    world.add("btq-1")
    ticks: list[str] = []

    def backstop(name: str) -> Outcome:
        ticks.append(name)
        return wsd.pickup_one(name, Trigger(TriggerKind.BACKSTOP))

    other = sqlite3.connect(s.journal, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")

    async def scenario() -> None:
        timer = asyncio.create_task(wsd._every(0.01, backstop))  # pyright: ignore[reportPrivateUsage]
        try:
            await until(lambda: len(ticks) >= 3)
            assert not timer.done() and WS not in wsd.recovered and world.claims == []
            other.rollback()
            await until(lambda: bool(world.claims))
            assert not timer.done() and WS in wsd.recovered
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)

    try:
        asyncio.run(scenario())
    finally:
        other.close()
    assert world.claims == ["btq-1"]
    assert [e.detail for e in j.events_since(0) if e.kind == "tick_failed"] == []   # nowhere to record it


def test_a_timer_that_dies_ends_serve(tmp_path: Path) -> None:
    """A tick failure the timer can't record for any reason but a busy journal ends `serve` with that
    error (and the control socket goes): wsd never runs on without its backstop."""
    s = settings(tmp_path)
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(World(tmp_path / "btq-state")), FakeRuntime()))

    def tick(name: str, trigger: Trigger) -> Outcome:
        raise RuntimeError("tick")

    def emit(*_args: object, **_kwargs: object) -> int:
        raise sqlite3.OperationalError("disk I/O error")

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(s.socket.exists)
        wsd.pickup_one = tick  # type: ignore[method-assign]
        j.emit = emit  # type: ignore[method-assign]
        with pytest.raises(sqlite3.OperationalError):
            await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    assert not s.socket.exists()


def test_a_failed_tick_is_recorded_and_recovered_again(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(tmp_path)
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(World(tmp_path / "btq-state")), FakeRuntime()))
    wsd.startup()
    calls: list[str] = []

    def boom(trigger: Trigger) -> Outcome:
        calls.append(trigger.kind.value)
        raise RuntimeError("boom")

    monkeypatch.setattr(wsd.parts.schedulers[WS], "pickup", boom)
    wsd._guarded(lambda n: wsd.pickup_one(n, Trigger(TriggerKind.BACKSTOP)), WS)  # pyright: ignore[reportPrivateUsage]
    assert calls == ["backstop"] and WS not in wsd.recovered
    assert [e.detail for e in j.events_since(0) if e.kind == "tick_failed"] == ["RuntimeError"]


def test_a_recovery_that_raises_is_retried_before_pickup(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 8 note 1: JournalBusy propagates out of recover() with no hold recorded. The workstream it
    was recovering is no longer counted as recovered, so its next pickup recovers it first."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    wsd.startup()
    assert WS in wsd.recovered
    real = daemon.recover
    recoveries: list[str] = []

    def busy(sched: object) -> object:
        raise JournalBusy("SQLITE_BUSY")

    monkeypatch.setattr(daemon, "recover", busy)
    with pytest.raises(JournalBusy):
        wsd.recover_one(WS)             # recover_one itself, whoever calls it (plan 6 may)
    assert WS not in wsd.recovered

    def counted(sched: daemon.Scheduler) -> daemon.Recovered:
        recoveries.append(sched.ws.name)
        return real(sched)

    monkeypatch.setattr(daemon, "recover", counted)
    world.add("btq-1")
    assert wsd.pickup_one(WS, Trigger(TriggerKind.BACKSTOP)) is Outcome.STARTED
    assert recoveries == [WS] and WS in wsd.recovered


def test_a_pickup_that_raises_is_recovered_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick's pickup that raises (JournalBusy mid-pickup, Task 7 note 3) leaves the workstream to be
    recovered again before its next pickup, from a control request as from a timer."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    wsd.startup()
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup

    def busy(trigger: Trigger) -> Outcome:
        raise JournalBusy("SQLITE_BUSY")

    monkeypatch.setattr(sched, "pickup", busy)
    with pytest.raises(JournalBusy):
        asyncio.run(wsd.handle(ctl.CtlRequest("tick", job="pickup")))
    assert WS not in wsd.recovered
    monkeypatch.setattr(sched, "pickup", real)
    world.add("btq-1")
    assert asyncio.run(wsd.handle(ctl.CtlRequest("tick", job="pickup"))).data == {WS: {"outcome": "started"}}
    assert WS in wsd.recovered


def test_stuck_is_never_shown_as_idle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Task 7: a ready bead btq's claim can never take makes the workstream stuck, not idle. Tick and
    status replies, and the CLI's output, say a human must act."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    world.beads["btq-1"].labels.append(f"session:{ids.ws_session(WS)}")     # pinned: unclaimable
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(world), FakeRuntime()))
    assert wsd.startup() == {WS: Outcome.STUCK}
    assert j.snapshot(WS).state is WsState.STUCK
    tick = asyncio.run(wsd.handle(ctl.CtlRequest("tick", job="pickup")))
    assert tick.data == {WS: {"outcome": "stuck", "attention": NEEDS_A_HUMAN}}
    status = asyncio.run(wsd.handle(ctl.CtlRequest("status")))
    assert (status.data[WS]["state"], status.data[WS]["attention"]) == ("stuck", NEEDS_A_HUMAN)
    assert status.data[WS]["beads"] == "btq-1=stuck(unclaimable)"
    assert cli._print(status) == 0  # pyright: ignore[reportPrivateUsage]
    assert f"{WS}: state=stuck attention={NEEDS_A_HUMAN} " in capsys.readouterr().out
    assert world.claims == []


def test_idle_status_has_no_attention(tmp_path: Path) -> None:
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    assert wsd.startup() == {WS: Outcome.NOTHING}
    status = wsd.status(None)[WS]
    assert (status["state"], status["attention"]) == ("idle", "")


@pytest.mark.parametrize("req", [ctl.CtlRequest("pause"), ctl.CtlRequest("resume"), ctl.CtlRequest("tick"),
                                 ctl.CtlRequest("status", job="pickup"),
                                 ctl.CtlRequest("tick", job="pickup", all=True)])
def test_handler_refuses_what_the_socket_refuses(tmp_path: Path, req: ctl.CtlRequest) -> None:
    """Called directly (plan 6 may), a pause or resume without a workstream, or a tick without a job, is
    refused rather than read as a pickup of every workstream."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    world.add("btq-1")
    assert asyncio.run(wsd.handle(req)).result == "refused"
    assert world.claims == [] and not list((tmp_path / "btq-state").glob("*/paused"))


class Threads:
    """Worker threads for a test: each is started, joined with a bound and checked, and an exception in
    one is kept for the test to assert on."""

    def __init__(self) -> None:
        self.started: list[threading.Thread] = []
        self.errors: dict[str, BaseException] = {}
        self.results: dict[str, object] = {}

    def start(self, name: str, fn: Callable[[], object]) -> None:
        def body() -> None:
            try:
                self.results[name] = fn()
            except BaseException as exc:  # noqa: BLE001 - kept for the test
                self.errors[name] = exc
        thread = threading.Thread(target=body, name=name)
        thread.start()
        self.started.append(thread)

    def join(self) -> None:
        for thread in self.started:
            thread.join(10)
            assert not thread.is_alive(), f"{thread.name} did not finish"


def test_a_pickup_waiting_behind_a_failing_one_recovers_first(tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """r1 review: pickup B arrives while pickup A holds the workstream; A then fails (JournalBusy) and so
    leaves it unrecovered. B must decide whether to recover only once it has the lock, so it recovers
    before picking up rather than act on the state A could not confirm."""
    a_inside, b_waiting, release_a = threading.Event(), threading.Event(), threading.Event()

    def cp(point: str) -> None:
        if point == "lock.waiting" and threading.current_thread().name == "B":
            b_waiting.set()

    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime(), cp=cp))
    wsd.startup()
    assert WS in wsd.recovered
    sched = wsd.parts.schedulers[WS]
    real_pickup, real_recover = sched.pickup, daemon.recover
    recoveries: list[str] = []

    def pickup(trigger: Trigger) -> Outcome:
        if threading.current_thread().name == "A":
            a_inside.set()
            assert release_a.wait(10)
            raise JournalBusy("SQLITE_BUSY")
        return real_pickup(trigger)

    def counted(sch: daemon.Scheduler) -> daemon.Recovered:
        recoveries.append(threading.current_thread().name)
        return real_recover(sch)

    monkeypatch.setattr(sched, "pickup", pickup)
    monkeypatch.setattr(daemon, "recover", counted)
    world.add("btq-1")
    threads = Threads()
    try:
        threads.start("A", lambda: wsd.pickup_one(WS, Trigger(TriggerKind.BACKSTOP)))
        assert a_inside.wait(10)
        threads.start("B", lambda: wsd.pickup_one(WS, Trigger(TriggerKind.BACKSTOP)))
        assert b_waiting.wait(10)
    finally:
        release_a.set()
        threads.join()
    assert isinstance(threads.errors.pop("A"), JournalBusy) and threads.errors == {}
    assert recoveries == ["B"] and WS in wsd.recovered
    assert threads.results["B"] is Outcome.STARTED and world.claims == ["btq-1"]


def test_serve_returns_only_after_a_running_job_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r1 review: a timer's pickup in its worker thread when stop comes is waited for, not abandoned."""
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup
    inside, release = threading.Event(), threading.Event()
    finished: list[str] = []

    def slow(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.BACKSTOP and not inside.is_set():
            inside.set()
            assert release.wait(10)
            finished.append("pickup")
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", slow)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        try:
            await until(inside.is_set)
            stop.set()
            done, _ = await asyncio.wait([task], timeout=0.3)
            assert not done                 # still waiting for the job in its thread
        finally:
            release.set()
        await asyncio.wait_for(task, 10)
        assert finished == ["pickup"]

    asyncio.run(scenario())
    assert not s.socket.exists()


def test_jobs_outliving_the_drain_keep_wsd_from_finishing(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup
    inside, release = threading.Event(), threading.Event()

    def stuck(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.BACKSTOP:
            inside.set()
            assert release.wait(10)
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", stuck)
    monkeypatch.setattr(daemon, "DRAIN_SECONDS", 0.1)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(inside.is_set)
        stop.set()
        with pytest.raises(daemon.Undrained):
            await asyncio.wait_for(task, 10)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        wsd.pool.shutdown(wait=True)
    assert not s.socket.exists()


class Recording(ctl.CtlServer):
    made: list["Recording"] = []

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        Recording.made.append(self)


def test_an_accepted_client_cannot_start_a_job_once_stopping(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """r1 review: a client accepted before stop whose request arrives during shutdown is refused; no
    pickup starts after the socket stops."""
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup
    operator: list[Trigger] = []

    def counted(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.OPERATOR:
            operator.append(trigger)
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", counted)
    Recording.made = []
    monkeypatch.setattr(daemon, "CtlServer", Recording)

    async def scenario() -> bytes:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(s.socket.exists)
        reader, writer = await asyncio.open_unix_connection(str(s.socket))
        try:
            await until(lambda: len(Recording.made[0].handlers) == 1)       # accepted, request unread
            stop.set()
            await until(lambda: wsd.stopping)
            writer.write(msgspec.json.encode(ctl.CtlRequest("tick", job="pickup")) + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 10)
        finally:
            writer.close()
        await asyncio.wait_for(task, 10)
        assert not Recording.made[0].handlers
        return line

    reply = msgspec.json.decode(asyncio.run(scenario()), type=ctl.CtlReply)
    assert reply.result == "refused" and "stopping" in reply.message
    assert operator == []


def test_a_stopping_wsd_refuses_jobs_from_the_handler_too(tmp_path: Path) -> None:
    """Plan 6 may call handle without the socket: once shutdown began it starts nothing either."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    wsd.startup()
    world.add("btq-1")
    wsd.stopping = True
    for req in (ctl.CtlRequest("tick", job="pickup"), ctl.CtlRequest("resume", ws=WS),
                ctl.CtlRequest("status")):
        reply = asyncio.run(wsd.handle(req))
        assert reply.result == "refused" and "stopping" in reply.message
    assert world.claims == []


def test_a_closing_socket_refuses_requests_it_has_not_read(tmp_path: Path) -> None:
    calls: list[ctl.CtlRequest] = []

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        calls.append(req)
        return ctl.CtlReply("ok", "ran")

    async def scenario() -> ctl.CtlReply:
        server = ctl.CtlServer(tmp_path / "ctl.sock", handler)
        await server.start()
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "ctl.sock"))
        try:
            await until(lambda: len(server.handlers) == 1)
            server.stop_accepting()
            writer.write(msgspec.json.encode(ctl.CtlRequest("status")) + b"\n")
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 10)
        finally:
            writer.close()
            await server.close()
        return msgspec.json.decode(line, type=ctl.CtlReply)

    assert asyncio.run(scenario()).result == "refused" and calls == []


def test_close_does_not_leave_a_handler_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection whose handler is still busy when close() gives up waiting is cancelled, not left
    to act after close returns."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.1)
    entered, never = asyncio.Event(), asyncio.Event()
    ended: list[str] = []

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        entered.set()
        try:
            await asyncio.wait_for(never.wait(), 10)
        finally:
            ended.append("handler")
        return ctl.CtlReply("ok", "late")

    async def scenario() -> None:
        server = ctl.CtlServer(tmp_path / "ctl.sock", handler)
        await server.start()
        reader, writer = await asyncio.open_unix_connection(str(tmp_path / "ctl.sock"))
        try:
            writer.write(msgspec.json.encode(ctl.CtlRequest("status")) + b"\n")
            await writer.drain()
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.wait_for(server.close(), 5)
            assert ended == ["handler"] and not server.handlers
        finally:
            writer.close()

    asyncio.run(scenario())


def test_run_closes_the_journal_then_releases_the_lock(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """r1 review: run closes the journal itself, on a clean stop as on a failed startup."""
    s = settings(tmp_path)
    closed: list[Journal] = []
    real_close = Journal.close

    def close(self: Journal) -> None:
        closed.append(self)
        real_close(self)

    async def served(d: Wsd) -> None:
        return None

    monkeypatch.setattr(Journal, "close", close)
    monkeypatch.setattr(cli, "_serve", served)
    assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 0
    assert len(closed) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        closed[0].db.execute("SELECT 1")
    os.close(instance_lock(s.instance_lock))        # released


def test_a_failed_startup_closes_the_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(tmp_path)
    closed: list[Journal] = []
    real_close = Journal.close

    def close(self: Journal) -> None:
        closed.append(self)
        real_close(self)

    async def busy(d: Wsd) -> None:
        raise JournalBusy("SQLITE_BUSY")

    monkeypatch.setattr(Journal, "close", close)
    monkeypatch.setattr(cli, "_serve", busy)
    assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    assert len(closed) == 1
    os.close(instance_lock(s.instance_lock))


def test_undrained_jobs_keep_the_journal_and_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """A job still running after the drain must not see its journal closed, nor a second wsd start."""
    s = settings(tmp_path)
    closed: list[Journal] = []
    real_close = Journal.close

    def close(self: Journal) -> None:
        closed.append(self)
        real_close(self)

    async def undrained(d: Wsd) -> None:
        raise daemon.Undrained("1 job(s) still running after 120 s")

    monkeypatch.setattr(Journal, "close", close)
    monkeypatch.setattr(cli, "_serve", undrained)
    assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    assert "stay held" in capsys.readouterr().err
    try:
        assert closed == []
        with pytest.raises(AlreadyRunning):
            instance_lock(s.instance_lock)
    finally:
        for fd in Path("/proc/self/fd").iterdir():     # the test, not the process, ends here: let go
            with contextlib.suppress(OSError):
                if fd.readlink() == s.instance_lock:
                    os.close(int(fd.name))


def test_a_reply_over_64_kib_arrives_whole(tmp_path: Path) -> None:
    """r1 review: asyncio's default 64 KiB line limit made a long status read as "wsd is not running"."""
    big = {f"ws{i}": {"beads": "x" * 1000} for i in range(100)}        # about 100 KiB

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        return ctl.CtlReply("ok", "status", big)

    async def scenario() -> ctl.CtlReply:
        server = ctl.CtlServer(tmp_path / "ctl.sock", handler)
        await server.start()
        try:
            return await ctl.request(tmp_path / "ctl.sock", ctl.CtlRequest("status"))
        finally:
            await server.close()

    reply = asyncio.run(scenario())
    assert reply.result == "ok" and reply.data == big


def test_a_reply_over_the_maximum_is_refused_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "MAX_REPLY", 70_000)

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        return ctl.CtlReply("ok", "status", {f"ws{i}": {"beads": "x" * 1000} for i in range(100)})

    async def scenario() -> ctl.CtlReply:
        server = ctl.CtlServer(tmp_path / "ctl.sock", handler)
        await server.start()
        try:
            return await ctl.request(tmp_path / "ctl.sock", ctl.CtlRequest("status"))
        finally:
            await server.close()

    reply = asyncio.run(scenario())
    assert reply.result == "failed" and reply.message.startswith(ctl.TOO_LARGE) and reply.data == {}


def test_status_counts_finished_beads_unless_asked_for_all(tmp_path: Path) -> None:
    s = settings(tmp_path)
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(World(tmp_path / "btq-state")), FakeRuntime()))
    wsd.startup()
    for bead in ("btq-7", "btq-8"):
        j.set_state(WS, bead, BeadState.CLAIMING)
    j.set_state(WS, "btq-8", BeadState.DROPPED, Reason.CLAIM_LOST)
    default = wsd.status(WS)[WS]
    assert (default["beads"], default["finished"]) == ("btq-7=claiming", "1")
    everything = asyncio.run(wsd.handle(ctl.CtlRequest("status", ws=WS, all=True))).data[WS]
    assert set(everything["beads"].split(",")) == {"btq-7=claiming", "btq-8=dropped(claim_lost)"}
