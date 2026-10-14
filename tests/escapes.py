"""pytest plugin: escaped test descendants end themselves, and each test proves it (spec
2026-10-13-u63ak-escaped-fake-cleanup-design.md).

A test that starts a long-lived descendant takes the `escapes` fixture and starts it as a sleeper
(tests/fakes/sleeper.py): with `sleeper.spawn` or `sleeper.fork_held` and the use's `path` (fakes read it from
`HZ_ESCAPE_DIR`), or from a shell script that begins with `shell_prelude()`. Teardown releases the lifeline,
then takes the census exclusively within a bound: that proves every sleeper of the use has exited, or it fails
the test naming the PIDs that reported. Nothing here ever signals a process. A survivor (a sleeper that is
stopped, frozen or never scheduled) is reported and left for the operator.

Every use lives in `<run>/<n>`, where `<run>` is a private mkdtemp made once per pytest process. At session
end any use whose teardown never ran is ended, reported, and fails the session.

The lifeline fd is O_CLOEXEC and only this process holds it, so no sleeper inherits it, except through a
fork of the pytest process that doesn't exec at once: tests must not fork and keep running Python.
Loaded from tests/conftest.py, or with `-p escapes`.
"""

import fcntl
import itertools
import os
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

FAKES = Path(__file__).resolve().with_name("fakes")
if str(FAKES) not in sys.path:
    sys.path.insert(0, str(FAKES))

import sleeper  # noqa: E402

BOUND = 10.0            # seconds: teardown's budget for every sleeper of a use to exit

_run: Path | None = None
_count = itertools.count()
_open: dict[Path, "Escapes"] = {}


def run_dir() -> Path:
    global _run
    if _run is None:
        _run = Path(tempfile.mkdtemp(prefix="hze"))
    return _run


def reported(path: Path) -> list[int]:
    try:
        return sorted(int(p.name) for p in (path / sleeper.PIDS).iterdir() if p.name.isdigit())
    except OSError:
        return []


class Escapes:
    """One use: its directory, and the lifeline held for as long as the test runs."""

    def __init__(self) -> None:
        self.path = run_dir() / str(next(_count))
        self.path.mkdir(mode=0o700)
        (self.path / sleeper.PIDS).mkdir()
        (self.path / sleeper.CENSUS).touch(0o600)
        (self.path / sleeper.LIFELINE).touch(0o600)
        self._lifeline: int | None = os.open(self.path / sleeper.LIFELINE, os.O_RDONLY | os.O_CLOEXEC)
        fcntl.flock(self._lifeline, fcntl.LOCK_EX)
        _open[self.path] = self

    @property
    def env(self) -> dict[str, str]:
        return {sleeper.ENV: str(self.path)}

    def pids(self) -> list[int]:
        """The sleepers that reported (`pids/`): for reports and positive controls, never the proof."""
        return reported(self.path)

    def shell_prelude(self) -> str:
        return shell_prelude(self.path)

    def sleeper_line(self, *opts: str) -> str:
        return sleeper_line(self.path, *opts)

    def end(self, bound: float = BOUND) -> None:
        """Release the lifeline, then take the census exclusively within `bound` and remove the use; raise
        AssertionError, naming the reported PIDs, if the bound passes first. Never signals anything."""
        if self._lifeline is not None:
            os.close(self._lifeline)
            self._lifeline = None
        if not self.path.exists():
            _open.pop(self.path, None)
            return
        fd = os.open(self.path / sleeper.CENSUS, os.O_RDONLY | os.O_CLOEXEC)
        try:
            deadline = time.monotonic() + bound
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        pids = self.pids()
                        named = ", ".join(map(str, pids)) if pids else "no PID reported"
                        raise AssertionError(f"escaped sleepers outlived their test ({self.path}): census "
                                             f"still held after {bound} s; {named}") from None
                    time.sleep(sleeper.POLL)
            shutil.rmtree(self.path)                # only while the census is held exclusively
            _open.pop(self.path, None)
        finally:
            os.close(fd)


def shell_prelude(path: Path) -> str:
    """A shell script's first lines: open the census as fd 9 and take it shared (`--take`), or exit 97
    having spawned nothing. Every later `&` child inherits fd 9, and so the census; close it with
    `exec 9<&-` right after the last sleeper spawn."""
    census = shlex.quote(str(path / sleeper.CENSUS))
    return (f"command exec 9<{census} || exit {sleeper.REFUSED}\n"
            f"{shlex.quote(sys.executable)} -I {shlex.quote(sleeper.SLEEPER)} --take 9 "
            f"{shlex.quote(str(path))} || exit {sleeper.REFUSED}\n")


def sleeper_line(path: Path, *opts: str) -> str:
    """The command that runs a sleeper on the prelude's fd 9 (append `&` to start it)."""
    words = [sys.executable, "-I", sleeper.SLEEPER, str(path), "--census-fd", "9", *opts]
    return " ".join(shlex.quote(w) for w in words)


@pytest.fixture
def escapes() -> Iterator[Escapes]:
    use = Escapes()
    yield use
    use.end()


def pytest_sessionfinish(session: pytest.Session) -> None:
    """End every use whose teardown never ran, report it, and fail the session; then remove the run."""
    global _run
    if _run is None:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    left = list(_open.values())
    for use in left:
        try:
            use.end()
            line = f"escapes: a use was never ended by its test: {use.path} (now ended)"
        except AssertionError as e:
            line = f"escapes: {e}"
        if reporter is not None:
            reporter.write_line(line, red=True)
        else:
            print(line, file=sys.stderr)
    if left:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    shutil.rmtree(_run, ignore_errors=True)
    _run = None
