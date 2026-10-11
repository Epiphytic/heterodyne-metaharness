"""pytest plugin: the `slow` marker behind the fast tier (docs/testing/efficiency-review.md).

`slow_tests.txt`, next to this file, lists test functions (`path::name`, or `path::Class::name`, without
parameters) whose run time puts them outside the fast tier. Collection marks every case of a listed
function `slow`, and `-m "not slow"` (scripts/test-fast) leaves them out. The list is data, regenerated
from a timed run by `scripts/run_tiers.py slow-list REPORT.xml`; a stale entry only means a test runs
in the full tier alone, and a new slow test stays in the fast tier until the list is regenerated.

Safety is the default. Only the files SLOW_ELIGIBLE matches (tmux and wsd mechanics) may have slow
tests; every other test file, a new one included, is a safety file (admind auth, operators, redaction,
latches, digests, approvals, sandbox, policy, ...), whose tests run in every tier whatever the list says.
Inside an eligible file, a test whose name matches SAFETY_WORDS is safety too. Collection marks every
safety test `safety`, and fails, rather than quietly dropping it from the fast tier, if the list names
one or anything marks one `slow`: make the test faster instead. Loaded from tests/conftest.py.

scripts/test-changed sets HZ_TIER_KEEP to the files a change reaches; their listed tests are not marked
slow, so one `-m "not slow"` run covers the fast tier and those files whole.
"""

import os
import re
from pathlib import Path

import pytest

SLOW_LIST = Path(__file__).resolve().with_name("slow_tests.txt")
KEEP_ENV = "HZ_TIER_KEEP"     # os.pathsep-separated test files whose slow tests stay in (test-changed)
SLOW_ELIGIBLE = re.compile(r"^tests/(test_tmux\w*|test_wsd_\w+)\.py$")
SAFETY_WORDS = re.compile(r"redact|secret|sandbox|latch|digest|polic|leak|npub|nsec|token|auth|isolat|guard"
                          r"|pin")


def function_id(nodeid: str) -> str:
    """`tests/x.py::test_y[param]` -> `tests/x.py::test_y`."""
    return nodeid.split("[", 1)[0]


def is_safety(nodeid: str) -> bool:
    path, _, rest = function_id(nodeid).partition("::")
    return not SLOW_ELIGIBLE.match(path) or bool(SAFETY_WORDS.search(rest.lower()))


def load(path: Path = SLOW_LIST) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: outside the fast tier (tests/slow_tests.txt)")
    config.addinivalue_line("markers", "safety: runs in every tier; never slow (tests/tier_marks.py)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    slow = load()
    unsafe = sorted(name for name in slow if is_safety(name))
    if unsafe:
        raise pytest.UsageError(f"{SLOW_LIST.name} lists safety tests, which must stay in every tier: "
                                + ", ".join(unsafe))
    keep = {path for path in os.environ.get(KEEP_ENV, "").split(os.pathsep) if path}
    marked: list[str] = []
    for item in items:
        if function_id(item.nodeid) in slow and item.nodeid.partition("::")[0] not in keep:
            item.add_marker(pytest.mark.slow)
        if is_safety(item.nodeid):
            item.add_marker(pytest.mark.safety)
            if item.get_closest_marker("slow") is not None:
                marked.append(item.nodeid)
    if marked:
        raise pytest.UsageError("safety tests are marked slow, which would drop them from the fast tier: "
                                + ", ".join(marked))
