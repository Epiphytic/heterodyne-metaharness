"""P0 consumer hardening, synthetic values only."""

import msgspec
import pytest

from heterodyne.actions.codec import InvalidAction, action_data, assemble_envelope, decode_intent
from heterodyne.actions.contracts import (
    ActionConfig,
    Intent,
    Interpretation,
    Notify,
    Origin,
    PolicyFacts,
    PushBranch,
    PushFacts,
    Repository,
)
from heterodyne.actions.policy import denial_key, evaluate_policy, materiality

REV = "b" * 64
SHA = "a" * 40
REPO = Repository("product", "product-id", "origin", "sha1", REV)
ORIGIN = Origin("heterodyne", "btq-efddg", "session-1", 1, "product")
PUSH = PushBranch("product", "refs/heads/feature", "refs/heads/main", SHA)


def full(repo=REPO, origin=ORIGIN, action=PUSH, config=None):
    config = config or ActionConfig((repo,))
    return assemble_envelope(
        Intent(1, action),
        origin,
        PushFacts(repo, origin.bead, action.from_ref, action.to_ref, SHA),
        "btq-ab123",
        1,
        config,
    ), config


def test_missing_safety_facts_fail_closed():
    interpretation = Interpretation("heterodyne", "product-id", ("fix",), "action", False, False)
    assert materiality(interpretation, interpretation).result == "confirm"
    value, config = full()
    assert evaluate_policy(value, config, PolicyFacts(REV), {}, {}).tier == "hard_deny"


def test_stdlib_big_integer_is_typed_safe_error():
    with pytest.raises(InvalidAction):
        decode_intent(b'{"schema_version":' + b"1" * 5000 + b"}")


@pytest.mark.parametrize("char", ["\u202e", "\u2066", "\u200b", "\ufeff"])
def test_format_characters_refused(char):
    with pytest.raises(InvalidAction, match="unsafe-text"):
        decode_intent(msgspec.json.encode(Intent(1, Notify("ops", "message" + char))))


def test_alias_cannot_bypass_denial():
    alias = msgspec.structs.replace(REPO, alias="mirror")
    config = ActionConfig((REPO, alias))
    left, _ = full(config=config)
    right, _ = full(
        alias,
        msgspec.structs.replace(ORIGIN, repo_alias="mirror"),
        msgspec.structs.replace(PUSH, repo="mirror"),
        config,
    )
    assert denial_key(left, config) == denial_key(right, config)


def test_host_invalid_record_cannot_escape_typed_error():
    value, config = full()
    for bad in (
        msgspec.structs.replace(value, origin=msgspec.structs.replace(ORIGIN, generation=2**90)),
        msgspec.structs.replace(value, action=Notify("ops", "\ud800")),
    ):
        with pytest.raises(InvalidAction):
            action_data(bad, config)


def test_config_namespace_capacity_has_separate_bound():
    repos = tuple(msgspec.structs.replace(REPO, alias=f"repo{i}", repository_id=f"id-{i}") for i in range(64))
    value, config = full(config=ActionConfig((REPO, *repos[:63])))
    assert action_data(value, config)["schema_version"] == 1


def test_closed_registry_matches_tagged_union_and_all_remain_unavailable():
    from typing import get_args

    from heterodyne.actions.contracts import REGISTRY, Action, ActionKind

    assert {entry.kind for entry in REGISTRY} == set(get_args(ActionKind))
    assert {cls.__struct_config__.tag for cls in get_args(Action)} == set(get_args(ActionKind))
    assert all(not entry.available for entry in REGISTRY)
