import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.config import ConfigError
from heterodyne.config.policy import RANK, effective_tiers

DEFAULTS = {
    "auto_approve": ["worktree_edit", "run_tests"],
    "escalate": ["push_branch"],
    "hard_deny": ["modify_policy"],
    "locked": ["modify_policy"],
}
CLASSES = ["worktree_edit", "run_tests", "push_branch", "modify_policy"]


def test_defaults_only() -> None:
    assert effective_tiers(DEFAULTS, {}, {})["run_tests"] == "auto_approve"


def test_host_may_retier_but_not_lower_locked() -> None:
    assert effective_tiers(DEFAULTS, {"push_branch": "auto_approve"}, {})["push_branch"] == "auto_approve"
    with pytest.raises(ConfigError, match="locked"):
        effective_tiers(DEFAULTS, {"modify_policy": "escalate"}, {})


def test_restrict_tightens() -> None:
    tiers = effective_tiers(DEFAULTS, {}, {"escalate": ["run_tests"], "hard_deny": ["push_branch"]})
    assert (tiers["run_tests"], tiers["push_branch"]) == ("escalate", "hard_deny")


def test_restrict_cannot_relax_or_name_unknown_classes() -> None:
    with pytest.raises(ConfigError, match="relax"):
        effective_tiers(DEFAULTS, {}, {"escalate": ["modify_policy"]})
    with pytest.raises(ConfigError, match="unknown"):
        effective_tiers(DEFAULTS, {}, {"hard_deny": ["typo_class"]})
    with pytest.raises(ConfigError, match="restrict"):
        effective_tiers(DEFAULTS, {}, {"allow": ["push_branch"]})


@given(
    esc=st.lists(st.sampled_from(CLASSES)),
    deny=st.lists(st.sampled_from(CLASSES)),
)
def test_restrict_never_less_strict_than_host(esc: list[str], deny: list[str]) -> None:
    host = effective_tiers(DEFAULTS, {}, {})
    try:
        tightened = effective_tiers(DEFAULTS, {}, {"escalate": esc, "hard_deny": deny})
    except ConfigError:
        return
    assert all(RANK[tightened[c]] >= RANK[host[c]] for c in CLASSES)


def test_operators_must_be_approvers() -> None:
    from heterodyne.config.policy import build_policy
    policy = build_policy({"approvers": ["op"], "operators": ["op"]}, {}, {})
    assert policy.operators == ("op",)
    with pytest.raises(ConfigError, match="not in approvers"):
        build_policy({"approvers": ["op"], "operators": ["other"]}, {}, {})
    with pytest.raises(ConfigError, match="list of approver names"):
        build_policy({"approvers": ["op"], "operators": "op"}, {}, {})
