"""The live admind suite is opt-in: everything under tests/live/ is skipped unless HZ_LIVE=1, except the
offline isolation checks in OFFLINE, which run in the normal suite.

With HZ_LIVE=1, one isolated stack (harness.Stack) is started per test module and torn down in a finally
block, with an atexit backup in case the session dies before the fixture's finalizer runs. It is one
stack per module because admind allows 30 asks an hour per store, and the modules together post more.
Before each stack is torn down, its audit log is checked for npubs and 64-hex runs.
"""

import atexit
import os
import re
import signal
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))   # tests/live has no __init__; keep `import harness` local

from harness import Stack, mask  # noqa: E402

LIVE = os.environ.get("HZ_LIVE") == "1"
HERE = Path(__file__).parent
OFFLINE = {"test_isolation_offline.py"}    # no network, no live binaries: part of the normal suite


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live admind suite: set HZ_LIVE=1 to run (uses the real Marmot relays)")
    for item in items:
        if item.path.is_relative_to(HERE) and item.path.name not in OFFLINE:
            item.add_marker(skip)


_STACKS: list[Stack] = []


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt(f"signal {signum}")


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    # SIGTERM would otherwise end the interpreter without running finally blocks or atexit; as a
    # KeyboardInterrupt, pytest unwinds and this fixture's teardown runs. SIGKILL cannot be caught:
    # the next run's reap_stale_runs cleans up after it.
    signal.signal(signal.SIGTERM, _interrupt)
    s = Stack()
    _STACKS.append(s)
    atexit.register(s.close)
    try:
        s.start()
        yield s
        audit_clean(s)
    finally:
        s.close()


def audit_clean(s: Stack) -> None:
    """The module's audit log names no npub and holds no 64-hex run (a failure here is a teardown error)."""
    text = s.audit_text()
    assert "npub1" not in text, "the audit log names an npub"
    assert not re.search(r"(?i)[0-9a-f]{64}", text), "the audit log holds a 64-hex run"


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    for s in _STACKS:
        terminalreporter.write_sep("-", f"live stack timings (s), {s.root.name}")
        for step, seconds in s.timings.items():
            terminalreporter.write_line(f"{step:>20}: {seconds}")
        terminalreporter.write_line(f"processes left after teardown (killed by PID): {len(s.sweep_found)}")
        terminalreporter.write_line(f"stale runs reaped at start: {len(s.reaped)}")
        if os.environ.get("HZ_LIVE_KEEP") == "1":
            terminalreporter.write_line(f"temp root kept: {mask(s.root)}")
