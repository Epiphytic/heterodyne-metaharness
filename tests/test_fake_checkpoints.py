"""The checkpoint fakes themselves: interleaving tests rely on them under concurrent callers."""

import threading
from collections.abc import Callable

import pytest
from fakes.checkpoints import CrashAt, Many, PauseAt, Recorder, Seen, SimulatedCrash

THREADS = 8


def together(count: int, body: Callable[[int], None]) -> list[threading.Thread]:
    """Start `count` named threads that run `body(i)` as soon as all of them are ready."""
    start = threading.Barrier(count)

    def run(i: int) -> None:
        start.wait(5)
        body(i)

    threads = [threading.Thread(target=run, args=(i,), name=f"t{i}") for i in range(count)]
    for thread in threads:
        thread.start()
    return threads


def finish(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(10)
    assert not [thread.name for thread in threads if thread.is_alive()]


def line_up(cp: Recorder, callers: threading.Barrier) -> None:
    """Called from a fake's first-hit check, after it read the flag. Unless the caller holds the fake's
    lock (the check is serialized), wait until every caller has read it too, so all of them race for the
    first hit at once: an unsynchronized check-then-set lets more than one through, deterministically."""
    lock: threading.Lock | None = getattr(cp, "lock", None)
    if lock is None or not lock.locked():
        callers.wait(5)


class RacingCrashAt(CrashAt):
    def __init__(self, point: str, callers: threading.Barrier) -> None:
        self.callers = callers
        super().__init__(point)

    @property
    def fired(self) -> bool:
        value = self._fired
        line_up(self, self.callers)
        return value

    @fired.setter
    def fired(self, value: bool) -> None:
        self._fired = value


class RacingEvent(threading.Event):
    def __init__(self, cp: Recorder, callers: threading.Barrier) -> None:
        super().__init__()
        self.cp = cp
        self.callers = callers

    def is_set(self) -> bool:
        value = super().is_set()
        line_up(self.cp, self.callers)
        return value


class SplitSeen(list[str]):
    """`seen` for the pairing test. Unless the caller holds the recorder's lock, thread t0 stops right
    after its append here until t1 has made a whole call, so an unsynchronized Recorder records t1's
    thread before t0's, deterministically. A serialized Recorder never waits."""

    def __init__(self, cp: Recorder, t0_in: threading.Event, t1_done: threading.Event) -> None:
        super().__init__()
        self.cp = cp
        self.t0_in = t0_in
        self.t1_done = t1_done

    def append(self, name: str) -> None:
        super().append(name)
        if threading.current_thread().name != "t0":
            return
        self.t0_in.set()
        lock: threading.Lock | None = getattr(self.cp, "lock", None)
        if lock is None or not lock.locked():
            self.t1_done.wait(5)


def test_recorder_keeps_points_and_threads_paired() -> None:
    cp = Recorder()
    t0_in = threading.Event()
    t1_done = threading.Event()
    cp.seen = SplitSeen(cp, t0_in, t1_done)

    def t1() -> None:
        cp("t1")
        t1_done.set()

    threads = [threading.Thread(target=cp, args=("t0",), name="t0"), threading.Thread(target=t1, name="t1")]
    try:
        threads[0].start()
        assert t0_in.wait(5)
        threads[1].start()
        threads[1].join(10)  # t1 itself releases t0, once its whole call has returned
        assert not threads[1].is_alive()
    finally:
        t1_done.set()  # failure cleanup only: never leave t0 waiting if the test fails early
        finish([thread for thread in threads if thread.ident is not None])
    assert list(cp.seen) == ["t0", "t1"]
    assert cp.threads == ["t0", "t1"]  # each point is paired with the thread that recorded it


def test_a_concurrent_first_hit_crashes_exactly_once() -> None:
    cp = RacingCrashAt("door", threading.Barrier(THREADS))
    crashed: list[int] = []

    def body(i: int) -> None:
        try:
            cp("door")
        except SimulatedCrash:
            crashed.append(i)

    finish(together(THREADS, body))
    assert len(crashed) == 1
    assert sorted(cp.seen) == ["door"] * THREADS


def test_a_concurrent_first_hit_pauses_exactly_one_caller() -> None:
    cp = PauseAt("door")
    cp.reached = RacingEvent(cp, threading.Barrier(THREADS))
    passed: list[int] = []
    others_passed = threading.Event()
    guard = threading.Lock()

    def body(i: int) -> None:
        cp("door")
        with guard:
            passed.append(i)
            if len(passed) == THREADS - 1:
                others_passed.set()

    threads = together(THREADS, body)
    try:
        assert others_passed.wait(5), "more than one caller is paused"
        assert len(passed) == THREADS - 1
    finally:
        cp.go.set()  # never leave a paused caller behind if the test fails
        finish(threads)
    assert sorted(passed) == list(range(THREADS))


def test_crash_at_ignores_other_points() -> None:
    cp = CrashAt("b")
    cp("a")
    with pytest.raises(SimulatedCrash):
        cp("b")
    cp("b")
    assert cp.seen == ["a", "b", "b"]
    assert cp.threads == [threading.current_thread().name] * 3


def test_seen_and_many_forward_without_blocking() -> None:
    seen = Seen("b")
    crash = CrashAt("c")
    cp = Many(seen, crash)
    cp("a")
    assert not seen.reached.is_set()
    cp("b")
    assert seen.reached.is_set()
    with pytest.raises(SimulatedCrash):
        cp("c")
    assert cp.seen == seen.seen == crash.seen == ["a", "b", "c"]
