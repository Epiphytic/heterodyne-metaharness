"""Deterministic checkpoints for crash-window and interleaving tests.

wsd calls `cp(name)` right after each external effect (`<op>.<step>!`) and each journal write
(`<op>.<step>`). `CrashAt` raises `SimulatedCrash` (a BaseException,
so no `except Exception` in wsd can swallow it) the first time a named point is reached, which models the
process dying at exactly that point. `PauseAt` parks the calling thread at a point until the test lets it go.
"""

import threading


class SimulatedCrash(BaseException):
    pass


class Recorder:
    def __init__(self) -> None:
        self.seen: list[str] = []
        self.threads: list[str] = []  # the calling thread's name for each entry of `seen`

    def __call__(self, name: str) -> None:
        self.seen.append(name)
        self.threads.append(threading.current_thread().name)


class CrashAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.fired = False

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.fired:
            self.fired = True
            raise SimulatedCrash(name)


class PauseAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.reached = threading.Event()
        self.go = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.reached.is_set():
            self.reached.set()
            if not self.go.wait(10):
                raise TimeoutError(name)


class Seen(Recorder):
    """Signals, without blocking, the first time a thread reaches the point: an interleaving test waits on
    `reached` to know the other thread is at that point (for example at a lock's door)."""

    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.reached = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point:
            self.reached.set()


class Many(Recorder):
    """Several checkpoints at once, called in order."""

    def __init__(self, *parts: Recorder) -> None:
        super().__init__()
        self.parts = parts

    def __call__(self, name: str) -> None:
        super().__call__(name)
        for part in self.parts:
            part(name)
