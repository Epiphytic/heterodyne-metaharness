"""The D9 capability table's invariants (plan 4b Task 11): evidence per enabled field, on the pinned CLI."""

import pytest

from heterodyne.agents.registry import ADAPTERS
from heterodyne.config import capabilities
from heterodyne.config.capabilities import (
    CAPABILITIES,
    EVIDENCE,
    PROBLEM_NEEDS_BINDING,
    PROBLEM_NO_EVIDENCE,
    PROBLEM_NO_GENERATOR,
    PROBLEM_NO_SOURCE,
    PROBLEM_STALE_EVIDENCE,
    PROBLEM_UNKNOWN_ADAPTER,
    PROBLEM_WRONG_VERSION,
    Capabilities,
    Evidence,
    table_problems,
)

PINS = {"claude-code": "2.1.286", "codex": "0.160.0"}
BOUND = Evidence("L1,L2", "0.160.0")


def test_the_shipped_table_keeps_every_rule() -> None:
    pins = {name: version for name, adapter in ADAPTERS.items() for version in adapter.versions}
    assert table_problems(CAPABILITIES, EVIDENCE, pins) == ()


def test_the_pins_here_are_the_adapters_pins() -> None:
    """The fixtures below use literal pins; this keeps them honest."""
    assert {name: list(adapter.versions) for name, adapter in ADAPTERS.items()} == {
        name: [pin] for name, pin in PINS.items()}


def test_an_empty_table_needs_no_evidence() -> None:
    assert table_problems({}, {}, PINS) == ()


def test_a_bound_login_with_its_evidence_is_accepted() -> None:
    table = {"codex": Capabilities(login_binding=True)}
    assert table_problems(table, {"codex": {"login_binding": BOUND}}, PINS) == ()


@pytest.mark.parametrize(("table", "evidence", "problem"), [
    ({"gemini": Capabilities()}, {}, PROBLEM_UNKNOWN_ADAPTER),
    ({}, {"gemini": {"login_binding": BOUND}}, PROBLEM_UNKNOWN_ADAPTER),
    ({"codex": Capabilities(login_binding=True)}, {}, PROBLEM_NO_EVIDENCE),
    ({"codex": Capabilities(login_binding=True)},
     {"codex": {"login_binding": BOUND, "trusted_read": Evidence("L7", "0.160.0")}}, PROBLEM_STALE_EVIDENCE),
    ({"codex": Capabilities(login_binding=True)}, {"codex": {"login_binding": Evidence("L1,L2", "0.157.0")}},
     PROBLEM_WRONG_VERSION),
    ({"codex": Capabilities(in_session_usage=True)},
     {"codex": {"in_session_usage": Evidence("L2", "0.160.0")}}, PROBLEM_NEEDS_BINDING),
    ({"claude-code": Capabilities(login_binding=True, trusted_read=True)},
     {"claude-code": {"login_binding": Evidence("L1,L2", "2.1.286"),
                      "trusted_read": Evidence("L7", "2.1.286")}}, PROBLEM_NO_SOURCE),
    ({"codex": Capabilities(login_binding=True, handoff_relaunch=True)},
     {"codex": {"login_binding": BOUND, "handoff_relaunch": Evidence("L4", "0.160.0")}},
     PROBLEM_NO_GENERATOR),
], ids=["unknown-in-table", "unknown-in-evidence", "no-evidence", "stale-evidence", "wrong-version",
        "needs-binding", "claude-trusted-read", "handoff"])
def test_each_broken_rule_is_named(table: dict[str, Capabilities], evidence: dict[str, dict[str, Evidence]],
                                   problem: str) -> None:
    found = table_problems(table, evidence, PINS)
    assert len(found) == 1 and found[0].startswith(problem), found


def test_a_problem_names_the_adapter_and_the_field_only() -> None:
    [found] = table_problems({"codex": Capabilities(login_binding=True)}, {}, PINS)
    assert found == f"{PROBLEM_NO_EVIDENCE}: codex.login_binding"


def test_every_field_is_a_capability_letter() -> None:
    """The rules name fields by their dataclass names; a renamed field must fail here, not pass silently."""
    assert capabilities.FIELDS == ("login_binding", "cross_account_resume", "in_session_usage",
                                   "trusted_read", "limit_signal", "handoff_relaunch")
