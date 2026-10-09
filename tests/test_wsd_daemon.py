import asyncio
import concurrent.futures as cf
import contextlib
import gc
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, Clock, accounts_at, git_repo, login_home

from heterodyne.wsd import cli, ctl, daemon, ids, journal
from heterodyne.wsd.daemon import NEEDS_A_HUMAN, Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.headroom import Mark
from heterodyne.wsd.journal import Journal, JournalBusy
from heterodyne.wsd.runtime import Session
from heterodyne.wsd.scheduler import Outcome, Trigger, TriggerKind
from heterodyne.wsd.settings import CTL_SOCKET, WsdSettings
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
    """Drain every daemon a test made (a cancelled timer leaves its job running on its lane), then close
    every journal the test opened, so their descriptors don't close later inside another test's count of
    open descriptors (test_wsd_gate checks that a refusal leaks none)."""
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


def open_fds() -> list[int]:
    """This process's open descriptors. /proc/self/fd is Linux's; macOS (CI runs both) has /dev/fd. Either
    listing includes the descriptor that reads it."""
    where = Path("/proc/self/fd")
    return [int(fd.name) for fd in (where if where.is_dir() else Path("/dev/fd")).iterdir()]


def let_go(path: Path) -> None:
    """Close every descriptor this process holds on `path`, found by device and inode: /dev/fd entries
    on macOS are not links to read."""
    want, closed = path.stat(), 0
    for fd in open_fds():
        with contextlib.suppress(OSError):         # the listing's own descriptor is closed by now
            got = os.fstat(fd)
            if (got.st_dev, got.st_ino) == (want.st_dev, want.st_ino):
                os.close(fd)
                closed += 1
    assert closed, f"nothing held {path}"


def counted_submits(lane: daemon.Lane, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """The keys of every job asked of `lane` from now on, joined or queued, recorded as submit returns."""
    keys: list[object] = []
    real = lane.submit

    def submit(key: Any, fn: Callable[[], object]) -> cf.Future[object]:
        fut = real(key, fn)
        keys.append(key)
        return fut

    monkeypatch.setattr(lane, "submit", submit)
    return keys


def settings(tmp_path: Path) -> WsdSettings:
    repo = git_repo(tmp_path / "repos" / "proj")
    accounts = accounts_at(login_home(tmp_path / "home"))
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, accounts=accounts)
    return WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,), accounts)


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
        timer = asyncio.create_task(wsd._every(WS, 0.01, "pickup", backstop))  # pyright: ignore[reportPrivateUsage]
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
    with pytest.raises(daemon.JobFailed):
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
    failed = asyncio.run(wsd.handle(ctl.CtlRequest("tick", job="pickup")))
    assert failed.result == "failed" and failed.data == {WS: {"outcome": "failed", "error": "JournalBusy"}}
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
        assert wsd.drain_now(10)
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


def test_undrained_jobs_end_the_process_holding_the_journal_and_lock(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A job still running after the drain must not see its journal closed, nor a second wsd start: the
    process ends at once (os._exit) with both still held; the kernel lets go of the lock."""
    s = settings(tmp_path)
    closed: list[Journal] = []
    real_close = Journal.close

    def close(self: Journal) -> None:
        closed.append(self)
        real_close(self)

    async def undrained(d: Wsd) -> None:
        raise daemon.Undrained("1 job(s) still running after 120 s")

    exits: list[int] = []

    def exit_now(code: int) -> None:
        exits.append(code)
        assert closed == []
        with pytest.raises(AlreadyRunning):
            instance_lock(s.instance_lock)

    monkeypatch.setattr(Journal, "close", close)
    monkeypatch.setattr(cli, "_serve", undrained)
    try:
        assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime(), exit_now=exit_now) == 1
        assert exits == [1] and "exiting now" in capsys.readouterr().err
    finally:
        let_go(s.instance_lock)                          # the test, not the process, ends here


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


def two_workstreams(tmp_path: Path) -> WsdSettings:
    s = settings(tmp_path)
    beta = WorkstreamSettings("beta", {"default": git_repo(tmp_path / "repos" / "beta")}, "coder", "p-one",
                              PROFILES, accounts=s.accounts)
    return replace(s, workstreams=(*s.workstreams, beta))


def test_ticks_on_a_busy_workstream_queue_one_job_and_starve_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 review: many ticks for one workstream whose pickup is stuck hold one running and one queued
    job between them; status and the other workstream still answer; every tick gets an outcome."""
    s = two_workstreams(tmp_path)
    world = World(tmp_path / "btq-state")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    wsd.startup()
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup
    inside, release = threading.Event(), threading.Event()
    pickups: list[str] = []

    def slow(trigger: Trigger) -> Outcome:
        pickups.append(trigger.kind.value)
        if len(pickups) == 1:
            inside.set()
            assert release.wait(10)
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", slow)

    submitted = counted_submits(wsd.lanes[WS], monkeypatch)

    async def scenario() -> list[ctl.CtlReply]:
        def tick() -> asyncio.Task[ctl.CtlReply]:
            return asyncio.create_task(wsd.handle(ctl.CtlRequest("tick", job="pickup", ws=WS)))

        # The first is running before the rest arrive: until its job starts, every tick would join it.
        ticks = [tick()]
        try:
            await until(inside.is_set)
            ticks += [tick() for _ in range(49)]
            await until(lambda: len(submitted) == 50)   # every tick has reached the lane
            assert len(wsd.lanes[WS].jobs) == 2       # one running, one queued: no more
            status = await asyncio.wait_for(wsd.handle(ctl.CtlRequest("status")), 5)
            other = await asyncio.wait_for(wsd.handle(ctl.CtlRequest("tick", job="pickup", ws="beta")), 5)
            assert status.result == "ok" and other.data == {"beta": {"outcome": "nothing"}}
        finally:
            release.set()
        return list(await asyncio.wait_for(asyncio.gather(*ticks), 10))

    replies = asyncio.run(scenario())
    assert all(r.result == "ok" for r in replies)
    assert pickups == ["operator", "operator"]      # the running one, and one for all 49 that waited


def test_queued_jobs_never_run_once_stopping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 review: shutdown cancels what has not started; only the running job is drained."""
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    wsd.startup()
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup
    inside, release = threading.Event(), threading.Event()
    reconciles: list[str] = []
    real_reconcile = wsd.reconcile_one

    def slow(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.OPERATOR:
            inside.set()
            assert release.wait(10)
        return real(trigger)

    def reconcile(name: str) -> Outcome:
        reconciles.append(name)
        return real_reconcile(name)

    monkeypatch.setattr(sched, "pickup", slow)
    monkeypatch.setattr(wsd, "reconcile_one", reconcile)

    async def scenario() -> tuple[ctl.CtlReply, ctl.CtlReply]:
        running = asyncio.create_task(wsd.handle(ctl.CtlRequest("tick", job="pickup")))
        await until(inside.is_set)
        queued = asyncio.create_task(wsd.handle(ctl.CtlRequest("tick", job="reconcile")))
        await until(lambda: len(wsd.lanes[WS].jobs) == 2)
        drain = asyncio.create_task(wsd._drain())  # pyright: ignore[reportPrivateUsage]
        await until(lambda: wsd.stopping)
        release.set()
        await asyncio.wait_for(drain, 10)
        return await asyncio.wait_for(running, 10), await asyncio.wait_for(queued, 10)

    try:
        ran, cancelled = asyncio.run(scenario())
    finally:
        release.set()
    assert ran.result == "ok"
    assert cancelled.result == "refused" and "stopping" in cancelled.message
    assert reconciles == []


class BlockingRuntime(FakeRuntime):
    """sessions() waits for `release`: a recovery held where the runtime is slow to answer."""

    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def sessions(self, ws: str) -> list[Session]:
        self.entered.set()
        assert self.release.wait(10)
        return super().sessions(ws)


def test_a_stop_during_startup_drains_within_its_bound_and_opens_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 review: a stop while startup's recovery is held enters the drain at once, keeps to its bound,
    and the control socket never opens, not even once the recovery ends."""
    s = settings(tmp_path)
    runtime = BlockingRuntime()
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), runtime))
    monkeypatch.setattr(daemon, "DRAIN_SECONDS", 0.2)

    async def scenario() -> float:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        assert await asyncio.to_thread(runtime.entered.wait, 10)
        stop.set()
        began = asyncio.get_running_loop().time()
        with pytest.raises(daemon.Undrained):
            await asyncio.wait_for(task, 10)
        return asyncio.get_running_loop().time() - began

    try:
        assert asyncio.run(scenario()) < 3
        assert not s.socket.exists()
    finally:
        runtime.release.set()
    assert wsd.drain_now(10)
    assert not s.socket.exists() and wsd.parts.journal.snapshot(WS).state is not WsState.RUNNING


def test_a_stop_during_startup_that_drains_returns_without_serving(tmp_path: Path) -> None:
    s = settings(tmp_path)
    runtime = BlockingRuntime()
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), runtime))

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        assert await asyncio.to_thread(runtime.entered.wait, 10)
        stop.set()
        await until(lambda: wsd.stopping)
        runtime.release.set()
        await asyncio.wait_for(task, 10)

    try:
        asyncio.run(scenario())
    finally:
        runtime.release.set()
    assert not s.socket.exists() and world.claims == []       # the startup pickup was cancelled unstarted


def test_close_aborts_a_client_that_stops_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 review: a peer that never reads a large reply can't hold close() open."""
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.1)
    answered, stuck = asyncio.Event(), asyncio.Event()
    real_drain = asyncio.StreamWriter.drain

    async def drain(writer: asyncio.StreamWriter) -> None:
        if writer.transport.get_write_buffer_size():   # more written than the socket took
            stuck.set()
        await real_drain(writer)

    monkeypatch.setattr(asyncio.StreamWriter, "drain", drain)

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        answered.set()
        return ctl.CtlReply("ok", "status", {f"ws{i}": {"beads": "x" * 1000} for i in range(900)})

    async def scenario() -> None:
        server = ctl.CtlServer(tmp_path / "ctl.sock", handler)
        await server.start()
        before = len(open_fds())                       # with the listening socket
        _, writer = await asyncio.open_unix_connection(str(tmp_path / "ctl.sock"))
        try:
            writer.write(msgspec.json.encode(ctl.CtlRequest("status")) + b"\n")
            await writer.drain()
            await asyncio.wait_for(answered.wait(), 10)
            await asyncio.wait_for(stuck.wait(), 10)    # the reply is written and stuck in drain()
            await asyncio.wait_for(server.close(), 5)   # we never read a byte of it
            assert not server.handlers
            # The listening socket went and our end is still open: wsd's end of it was dropped.
            assert len(open_fds()) == before
        finally:
            writer.transport.abort()

    asyncio.run(scenario())


HARD_EXIT = """
import sys, threading
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, Clock, accounts_at, git_repo, login_home
from heterodyne.wsd import cli, daemon
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import WorkstreamSettings

tmp = Path(sys.argv[1])
accounts = accounts_at(login_home(tmp / "home"))
ws = WorkstreamSettings(WS, {"default": git_repo(tmp / "repos" / "proj")}, "coder", "p-one", PROFILES,
                        accounts=accounts)
s = WsdSettings(tmp / "state" / "wsd", 0.05, 3600, 3, tmp / "btq", {}, (ws,), accounts)


class Stuck(FakeRuntime):
    def sessions(self, ws):
        (tmp / "entered").touch()
        threading.Event().wait()        # never answers


daemon.DRAIN_SECONDS = 0.2
sys.exit(cli.run(s, factory(World(tmp / "btq-state")), Stuck()))
"""


def test_an_undrained_wsd_process_exits_within_its_bound(tmp_path: Path) -> None:
    """r2 review: with a job that never ends, SIGTERM still ends the process (exit 1) soon after the
    drain bound, rather than waiting on the thread forever."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BTQ_", "BEADS_", "BD_"))}
    env["HOME"] = str(tmp_path)
    tests = Path(__file__).parent
    proc = subprocess.Popen([sys.executable, "-c", HARD_EXIT, str(tmp_path), str(tests)], env=env,
                            stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(1500):
            if (tmp_path / "entered").exists() or proc.poll() is not None:
                break
            time.sleep(0.02)
        assert (tmp_path / "entered").exists(), "the daemon never reached recovery"
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
    assert proc.returncode == 1 and "exiting now" in err


def test_a_stop_that_lands_as_startup_ends_still_serves_nothing(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup finishing in the same loop pass as the stop: the stop wins, nothing is served."""
    Recording.made = []
    monkeypatch.setattr(daemon, "CtlServer", Recording)
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    stop = asyncio.Event()

    async def startup() -> None:
        stop.set()                      # e.g. SIGTERM handled just as the last startup job came back

    wsd._startup = startup  # type: ignore[method-assign]

    async def scenario() -> None:
        await asyncio.wait_for(wsd.serve(stop), 10)

    asyncio.run(scenario())
    assert Recording.made == [] and not s.socket.exists() and wsd.stopping


def held_lane(wsd: Wsd, name: str) -> tuple[threading.Event, threading.Event, Callable[[], object]]:
    """A job that holds `name`'s lane until released: (inside, release, the job)."""
    inside, release = threading.Event(), threading.Event()

    def hold() -> object:
        inside.set()
        return release.wait(10)

    return inside, release, hold


@pytest.mark.parametrize("asked", [["pause", "resume", "pause"], ["resume", "pause", "resume"],
                                   ["pause", "resume"]])
def test_pause_and_resume_queued_together_apply_the_last_asked(tmp_path: Path, asked: list[str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: requests queued behind a busy lane end in the state asked last; a caller is told its
    request took effect only if that is the state published, and the state is published once."""
    s = settings(tmp_path)
    j = Journal(s.journal)
    wsd = Wsd(s, assemble(s, j, factory(World(tmp_path / "btq-state")), FakeRuntime()))
    wsd.startup()
    if asked[0] == "resume":
        assert wsd.set_pause(WS, True).result == "ok"
    inside, release, hold = held_lane(wsd, WS)
    before = j.events_since(0)[-1].seq
    submitted = counted_submits(wsd.lanes[WS], monkeypatch)

    async def scenario() -> list[ctl.CtlReply]:
        holder = asyncio.create_task(wsd._run(WS, "hold", hold))  # pyright: ignore[reportPrivateUsage]
        replies: list[asyncio.Task[ctl.CtlReply]] = []
        try:
            await until(inside.is_set)
            for n, op in enumerate(asked, 1):
                req = ctl.CtlRequest("pause" if op == "pause" else "resume", ws=WS)
                replies.append(asyncio.create_task(wsd.handle(req)))
                # In the order asked: each has reached the lane before the next is made.
                await until(lambda n=n: submitted.count(daemon.PAUSE_STATE) == n)
            assert len(wsd.lanes[WS].jobs) == 2      # the hold, and one pause-state job for them all
        finally:
            release.set()
        await asyncio.wait_for(holder, 10)
        return list(await asyncio.wait_for(asyncio.gather(*replies), 10))

    replies = asyncio.run(scenario())
    last = asked[-1]
    paused = last == "pause"
    assert (j.snapshot(WS).state is WsState.PAUSED) is paused
    assert [e.kind for e in j.events_since(before) if e.kind in ("paused", "resumed")] == [
        "paused" if paused else "resumed"]
    for op, reply in zip(asked, replies, strict=True):
        if op == last:
            assert reply.result == "ok" and ("paused" if paused else "resumed") in reply.message
        else:
            assert reply.result == "refused" and "overtaken" in reply.message
            assert f"is {'paused' if paused else 'resumed'}" in reply.message


def test_a_timer_reconcile_never_joins_a_queued_pickup(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: the reconcile timer queues a reconcile, not a pickup, behind an operator pickup."""
    s = replace(settings(tmp_path), backstop_seconds=3600, reconcile_seconds=0.05)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    inside, release, hold = held_lane(wsd, WS)
    reconciles: list[str] = []
    real = wsd.reconcile_one

    def reconcile(name: str) -> Outcome:
        reconciles.append(name)
        return real(name)

    monkeypatch.setattr(wsd, "reconcile_one", reconcile)

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        try:
            await until(s.socket.exists)
            holder = asyncio.create_task(wsd._run(WS, "hold", hold))  # pyright: ignore[reportPrivateUsage]
            await until(inside.is_set)
            ticked = asyncio.create_task(wsd.handle(ctl.CtlRequest("tick", job="pickup", ws=WS)))
            await until(lambda: len(wsd.lanes[WS].jobs) == 3)       # the hold, the pickup, the reconcile
            release.set()
            await asyncio.wait_for(holder, 10)
            await until(lambda: bool(reconciles))
            return await asyncio.wait_for(ticked, 10)
        finally:
            release.set()
            stop.set()
            await asyncio.wait_for(task, 10)

    assert asyncio.run(scenario()).result == "ok"


class StopProbe(ctl.CtlServer):
    """Releases the job holding the lane as the socket stops listening, then waits for the lane's
    worker to end: anything queued that could still start does so before this returns."""
    release: threading.Event
    lane: "daemon.Lane"

    def stop_accepting(self) -> None:
        StopProbe.release.set()
        worker = StopProbe.lane.worker
        assert worker is not None
        worker.join(5)
        super().stop_accepting()


def test_no_queued_job_starts_once_stopping_even_if_a_worker_frees_up(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: the lane's worker frees up between `stopping` and the timers' cancellation; the job
    queued behind it was already cancelled, and never runs."""
    s = replace(settings(tmp_path), backstop_seconds=3600)     # nothing else queues on the lane
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    inside, release, hold = held_lane(wsd, WS)
    StopProbe.release, StopProbe.lane = release, wsd.lanes[WS]
    monkeypatch.setattr(daemon, "CtlServer", StopProbe)
    reconciles: list[str] = []
    monkeypatch.setattr(wsd, "reconcile_one", lambda name: reconciles.append(name) or Outcome.NOTHING)

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        try:
            await until(s.socket.exists)
            holder = asyncio.create_task(wsd._run(WS, "hold", hold))  # pyright: ignore[reportPrivateUsage]
            await until(inside.is_set)
            queued = asyncio.create_task(wsd.handle(ctl.CtlRequest("tick", job="reconcile", ws=WS)))
            await until(lambda: ("reconcile", None) in wsd.lanes[WS].queued)
            stop.set()
            await asyncio.wait_for(task, 10)
            await asyncio.wait_for(holder, 10)
            return await asyncio.wait_for(queued, 10)
        finally:
            release.set()

    reply = asyncio.run(scenario())
    worker = wsd.lanes[WS].worker
    assert worker is not None and not worker.is_alive()
    assert reconciles == [] and reply.result == "refused" and "stopping" in reply.message


def test_a_blocked_workstream_never_holds_up_another_ones_timers(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: alpha's backstop pickup is stuck; beta's backstop ticks carry on."""
    s = two_workstreams(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    inside, release = threading.Event(), threading.Event()
    beta: list[str] = []
    alpha_sched, beta_sched = wsd.parts.schedulers[WS], wsd.parts.schedulers["beta"]
    real_alpha, real_beta = alpha_sched.pickup, beta_sched.pickup

    def alpha(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.BACKSTOP:
            inside.set()
            assert release.wait(10)
        return real_alpha(trigger)

    def counted(trigger: Trigger) -> Outcome:
        if trigger.kind is TriggerKind.BACKSTOP:
            beta.append(trigger.kind.value)
        return real_beta(trigger)

    monkeypatch.setattr(alpha_sched, "pickup", alpha)
    monkeypatch.setattr(beta_sched, "pickup", counted)

    async def scenario() -> int:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        try:
            await until(inside.is_set)
            seen = len(beta)
            await until(lambda: len(beta) >= seen + 3)      # alpha still stuck
            assert not release.is_set()
            return len(beta)
        finally:
            release.set()
            stop.set()
            await asyncio.wait_for(task, 10)

    assert asyncio.run(scenario()) >= 3


def test_startup_recovers_every_workstream_before_any_pickup(tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: the startup barrier. beta's recovery is held; alpha recovers, and its lane then sits
    empty, its startup pickup not even queued, until beta's recovery ends."""
    s = two_workstreams(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    order: list[str] = []
    beta_in, beta_go = threading.Event(), threading.Event()
    real_recover, real_pickup = wsd.recover_one, wsd.pickup_one

    def recover(name: str) -> object:
        if name == "beta":
            beta_in.set()
            assert beta_go.wait(10)
        result = real_recover(name)
        order.append(f"recovered {name}")
        return result

    def pickup(name: str, trigger: Trigger) -> Outcome:
        order.append(f"pickup {name}")
        return real_pickup(name, trigger)

    monkeypatch.setattr(wsd, "recover_one", recover)
    monkeypatch.setattr(wsd, "pickup_one", pickup)

    async def scenario() -> None:
        task = asyncio.create_task(wsd._startup())  # pyright: ignore[reportPrivateUsage]
        try:
            await until(lambda: beta_in.is_set() and f"recovered {WS}" in order and not wsd.lanes[WS].jobs)
            assert order == [f"recovered {WS}"]         # alpha's pickup was never queued
        finally:
            beta_go.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    assert order[:2] == [f"recovered {WS}", "recovered beta"]
    assert sorted(order[2:]) == [f"pickup {WS}", "pickup beta"]


@pytest.mark.parametrize(("refs", "pickups"), [(["a", "a"], 1), (["a", "b"], 2), ([None, None], 1)])
def test_only_triggers_with_the_same_ref_share_a_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     refs: list[str | None], pickups: int) -> None:
    """r3 review: one event's ref is never recorded as another's."""
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    wsd.startup()
    seen: list[str | None] = []
    sched = wsd.parts.schedulers[WS]
    real = sched.pickup

    def counted(trigger: Trigger) -> Outcome:
        seen.append(trigger.ref)
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", counted)
    inside, release, hold = held_lane(wsd, WS)

    async def scenario() -> None:
        holder = asyncio.create_task(wsd._run(WS, "hold", hold))  # pyright: ignore[reportPrivateUsage]
        try:
            await until(inside.is_set)
            asked = [asyncio.create_task(wsd.pickup(WS, Trigger(TriggerKind.OPERATOR, ref))) for ref in refs]
            await until(lambda: len(wsd.lanes[WS].jobs) == 1 + pickups)
        finally:
            release.set()
        await asyncio.wait_for(holder, 10)
        await asyncio.wait_for(asyncio.gather(*asked), 10)

    asyncio.run(scenario())
    assert seen == list(dict.fromkeys(refs))


def test_the_hard_exit_happens_even_if_stderr_is_gone(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """r3 review: a closed stderr (BrokenPipeError) can't keep an undrained wsd alive."""
    s = settings(tmp_path)

    class Gone:
        def write(self, _text: str) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    async def undrained(d: Wsd) -> None:
        raise daemon.Undrained("1 job(s) still running after 120 s")

    exits: list[int] = []
    monkeypatch.setattr(cli, "_serve", undrained)
    monkeypatch.setattr(sys, "stderr", Gone())
    try:
        with pytest.raises(BrokenPipeError):        # only because this exit_now returns
            cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime(), exit_now=exits.append)
    finally:
        let_go(s.instance_lock)
    assert exits == [1]


def test_a_closed_lane_admits_nothing() -> None:
    lane = daemon.Lane("t", threading.Lock())
    assert lane.close() == set()
    with pytest.raises(daemon.Stopping):
        lane.submit("k", lambda: None)


def test_a_lane_never_runs_a_job_its_caller_cancelled() -> None:
    lane = daemon.Lane("t", threading.Lock())
    inside, release = threading.Event(), threading.Event()
    ran: list[str] = []

    def hold() -> object:
        inside.set()
        return release.wait(10)

    held = lane.submit("hold", hold)
    try:
        assert inside.wait(10)
        queued = lane.submit("k", lambda: ran.append("k"))
        assert queued.cancel()
        after = lane.submit("after", lambda: ran.append("after"))
    finally:
        release.set()
    assert after.result(10) is None and held.result(10) is True
    assert ran == ["after"]
    assert lane.close() == set()


SOCKET_TAKEN = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, Clock, accounts_at, git_repo, login_home
from heterodyne.wsd import cli
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import WorkstreamSettings

tmp = Path(sys.argv[1])
accounts = accounts_at(login_home(tmp / "home"))
ws = WorkstreamSettings(WS, {"default": git_repo(tmp / "repos" / "proj")}, "coder", "p-one", PROFILES,
                        accounts=accounts)
s = WsdSettings(tmp / "state" / "wsd", 0.05, 3600, 3, tmp / "btq", {}, (ws,), accounts)
s.socket.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
s.socket.write_text("not a socket")
sys.exit(cli.run(s, factory(World(tmp / "btq-state")), FakeRuntime()))
"""


def test_a_socket_that_fails_to_start_still_ends_the_process(tmp_path: Path) -> None:
    """r4 review: a regular file at the socket path fails the socket after startup ran jobs on every
    lane; wsd still shuts its lanes down and exits EX_CONFIG, leaving the file alone."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BTQ_", "BEADS_", "BD_"))}
    env["HOME"] = str(tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", SOCKET_TAKEN, str(tmp_path), str(Path(__file__).parent)],
                            env=env, stderr=subprocess.PIPE, text=True)
    try:
        _, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
    assert proc.returncode == cli.EX_CONFIG, err
    assert "is not a socket" in err
    assert (tmp_path / "state" / "wsd" / CTL_SOCKET).read_text() == "not a socket"


class StopWhileStarting(ctl.CtlServer):
    """Sets `stop` as the socket finishes starting: the stop arrives during `await server.start()`."""
    stop: asyncio.Event

    async def start(self) -> None:
        await super().start()
        StopWhileStarting.stop.set()


def test_a_stop_while_the_socket_starts_starts_no_timer(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    timers: list[str] = []

    async def every(name: str, seconds: float, kind: str, job: Callable[[str], Outcome]) -> None:
        timers.append(f"{name} {kind}")

    monkeypatch.setattr(wsd, "_every", every)
    monkeypatch.setattr(daemon, "CtlServer", StopWhileStarting)

    async def scenario() -> None:
        StopWhileStarting.stop = asyncio.Event()
        await asyncio.wait_for(wsd.serve(StopWhileStarting.stop), 10)

    asyncio.run(scenario())
    assert timers == [] and wsd.stopping and not s.socket.exists()


class ProbedLock:
    """Wraps a lane's shutdown lock: `tried` once a worker reaches its start checkpoint and asks for
    the lock, `left` once it lets go of it."""

    def __init__(self, real: threading.Lock) -> None:
        self.real = real
        self.tried, self.left = threading.Event(), threading.Event()

    def __enter__(self) -> bool:
        self.tried.set()
        return self.real.__enter__()

    def __exit__(self, *exc: object) -> None:
        self.real.__exit__(None, None, None)
        self.left.set()


def test_shutdown_closes_every_lane_before_any_starts_a_queued_job(tmp_path: Path) -> None:
    """r4 review: shutdown is under way but still waiting for the status lane's lock (the first lane it
    closes) when alpha's running job ends; alpha's worker reaches its start checkpoint in that gap, and
    its queued job must not start."""
    s = settings(tmp_path)
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "btq-state")), FakeRuntime()))
    alpha = wsd.lanes[WS]
    inside, release = threading.Event(), threading.Event()
    ran: list[str] = []

    def hold() -> object:
        inside.set()
        return release.wait(10)

    held = alpha.submit("hold", hold)
    assert inside.wait(10)
    probe = ProbedLock(wsd.shutdown)
    alpha.shutdown = probe  # type: ignore[assignment]   # the worker's next start goes through it
    queued = alpha.submit("queued", lambda: ran.append("queued"))
    shutdown_done: list[set[Any]] = []
    status = wsd.lanes[daemon.STATUS_LANE]
    with status.lock:
        closing = threading.Thread(target=lambda: shutdown_done.append(
            wsd._cancel_queued()))  # pyright: ignore[reportPrivateUsage]
        closing.start()
        try:
            for _ in range(500):            # shutdown has begun: it holds the daemon's shutdown lock
                if wsd.stopping:
                    break
                time.sleep(0.01)
            assert wsd.stopping and not alpha.closed
            release.set()
            assert held.result(10) is True
            assert probe.tried.wait(10), "alpha's worker never reached its start checkpoint"
            # It is past the checkpoint: blocked there (as it must be), or through it at once. Let a
            # worker that got through let go of the lock before shutdown can close alpha.
            probe.left.wait(0.5)
            assert not probe.left.is_set(), "alpha's worker got the shutdown lock mid-shutdown"
        finally:
            release.set()
    closing.join(10)
    assert not closing.is_alive() and shutdown_done
    cf.wait([queued], 10)
    assert ran == [] and queued.cancelled()


# --- AU-5: the quota wake (design §3.6; §5 daemon) ---

HOUR = 3600


def deferred_wsd(tmp_path: Path) -> tuple[Wsd, Clock, int]:
    """A daemon on an injected clock whose only bead is blocked for an hour: its pickups are DEFERRED."""
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    wsd = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    clock = Clock()
    sched = wsd.parts.schedulers[WS]
    sched.d = replace(sched.d, clock=clock)
    sched.parker.d = sched.d
    [candidate] = s.workstreams[0].accounts.view("p-one").accounts  # type: ignore[union-attr]
    until = clock() + HOUR
    seq = sched.d.journal.usage_seq_next()
    sched.d.journal.exhausted_put(candidate.key, Mark(until, clock(), seq), seq)
    return wsd, clock, until


def test_a_deferred_pickup_arms_one_wake_and_the_next_replaces_it(tmp_path: Path) -> None:
    wsd, clock, until = deferred_wsd(tmp_path)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        assert await wsd.pickup(WS, Trigger(TriggerKind.BACKSTOP)) is Outcome.DEFERRED
        first = wsd.wakes[WS]
        assert abs(first.when() - loop.time() - (until - clock())) < 5
        clock.advance(600)
        assert await wsd.pickup(WS, Trigger(TriggerKind.BACKSTOP)) is Outcome.DEFERRED
        second = wsd.wakes[WS]
        assert first.cancelled() and not second.cancelled() and len(wsd.wakes) == 1
        assert abs(second.when() - loop.time() - (until - clock())) < 5
        await wsd._cancel_wakes()  # pyright: ignore[reportPrivateUsage]

    asyncio.run(scenario())


def test_a_firing_wake_submits_a_quota_wake_pickup_through_the_lane(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    wsd, clock, until = deferred_wsd(tmp_path)
    sched = wsd.parts.schedulers[WS]
    seen: list[TriggerKind] = []
    real = sched.pickup

    def counted(trigger: Trigger) -> Outcome:
        seen.append(trigger.kind)
        return real(trigger)

    monkeypatch.setattr(sched, "pickup", counted)
    keys = counted_submits(wsd.lanes[WS], monkeypatch)

    async def scenario() -> None:
        assert await wsd.pickup(WS, Trigger(TriggerKind.BACKSTOP)) is Outcome.DEFERRED
        clock.now = until                                  # the deadline passes
        wsd._wake(WS)  # pyright: ignore[reportPrivateUsage] - what the armed handle calls
        [task] = wsd.waking
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    assert seen == [TriggerKind.BACKSTOP, TriggerKind.QUOTA_WAKE]
    assert keys == [("pickup", None), ("pickup", None)]
    assert wsd.parts.journal.state(WS, "btq-1") is not None and WS not in wsd.wakes


def test_stop_cancels_the_wake(tmp_path: Path) -> None:
    wsd, _, _ = deferred_wsd(tmp_path)

    async def scenario() -> asyncio.TimerHandle:
        stop = asyncio.Event()
        task = asyncio.create_task(wsd.serve(stop))
        await until(lambda: WS in wsd.wakes)               # startup's pickup armed it
        handle = wsd.wakes[WS]
        stop.set()
        await task
        return handle

    handle = asyncio.run(scenario())
    assert handle.cancelled() and wsd.wakes == {} and wsd.waking == set()


def test_no_wake_is_armed_once_stopping(tmp_path: Path) -> None:
    wsd, _, _ = deferred_wsd(tmp_path)

    async def scenario() -> None:
        assert await wsd.pickup(WS, Trigger(TriggerKind.BACKSTOP)) is Outcome.DEFERRED
        wsd._cancel_queued()  # pyright: ignore[reportPrivateUsage]
        wsd._arm(WS)  # pyright: ignore[reportPrivateUsage]
        assert wsd.wakes == {}
        wsd._wake(WS)  # pyright: ignore[reportPrivateUsage]
        assert wsd.waking == set()

    asyncio.run(scenario())
