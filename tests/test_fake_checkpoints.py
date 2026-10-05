"""The checkpoint fakes themselves: interleaving tests rely on them under concurrent callers."""

import sys
import threading
from collections.abc import Callable, Generator

import pytest
from fakes.checkpoints import CrashAt, Many, PauseAt, Recorder, Seen, SimulatedCrash

THREADS = 8


@pytest.fixture
def fast_switching() -> Generator[None]:
    # Switch threads as often as possible, so an unsynchronized check-then-set would interleave.
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(before)


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


@pytest.mark.usefixtures("fast_switching")
def test_recorder_keeps_points_and_threads_paired() -> None:
    cp = Recorder()

    def body(i: int) -> None:
        for _ in range(2000):
            cp(f"t{i}")

    for thread in together(THREADS, body):
        thread.join(10)
    assert len(cp.seen) == len(cp.threads) == THREADS * 2000
    assert cp.seen == cp.threads  # each thread records its own name as the point


def test_a_concurrent_first_hit_crashes_exactly_once() -> None:
    cp = RacingCrashAt("door", threading.Barrier(THREADS))
    crashed: list[int] = []

    def body(i: int) -> None:
        try:
            cp("door")
        except SimulatedCrash:
            crashed.append(i)

    for thread in together(THREADS, body):
        thread.join(10)
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
    assert others_passed.wait(5), "more than one caller is paused"
    assert len(passed) == THREADS - 1
    cp.go.set()
    for thread in threads:
        thread.join(10)
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
