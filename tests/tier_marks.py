"""pytest plugin: the `slow` marker behind the fast tier (docs/testing/efficiency-review.md).

`slow_tests.txt`, next to this file, lists test functions (`path::name`, or `path::Class::name`, without
parameters) whose run time puts them outside the fast tier. Collection marks every case of a listed
function `slow`, and `-m "not slow"` (scripts/test-fast) leaves them out. The list is data, regenerated
from a timed run by `scripts/run_tiers.py slow-list REPORT.xml`; a stale entry only means a test runs
in the full tier alone, and a new slow test stays in the fast tier until the list is regenerated.

A safety test is never slow: anything in SAFETY_FILES, or whose name matches SAFETY_WORDS, stays in
every tier. If the list names one, collection fails rather than quietly dropping it from the fast tier;
make the test faster instead. Loaded from tests/conftest.py.
"""

import re
from pathlib import Path

import pytest

SLOW_LIST = Path(__file__).resolve().with_name("slow_tests.txt")
SAFETY_FILES = re.compile(
    r"^tests/(test_redaction|test_secret_scan|test_policy|test_sandbox_\w+|test_admind_r13_redact"
    r"|test_admind_approvals|test_admind_socket_bounds|test_check_install_agnostic"
    r"|live/test_isolation_offline)\.py$")
SAFETY_WORDS = re.compile(r"redact|secret|sandbox|latch|digest|polic|leak|npub|nsec|token|auth|isolat|guard"
                          r"|pin")


def function_id(nodeid: str) -> str:
    """`tests/x.py::test_y[param]` -> `tests/x.py::test_y`."""
    return nodeid.split("[", 1)[0]


def is_safety(nodeid: str) -> bool:
    path, _, rest = function_id(nodeid).partition("::")
    return bool(SAFETY_FILES.match(path) or SAFETY_WORDS.search(rest.lower()))


def load(path: Path = SLOW_LIST) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: outside the fast tier (tests/slow_tests.txt)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    slow = load()
    if not slow:
        return
    unsafe = sorted(name for name in slow if is_safety(name))
    if unsafe:
        raise pytest.UsageError(f"{SLOW_LIST.name} lists safety tests, which must stay in every tier: "
                                + ", ".join(unsafe))
    mark = pytest.mark.slow
    for item in items:
        if function_id(item.nodeid) in slow:
            item.add_marker(mark)
