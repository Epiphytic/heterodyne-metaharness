"""Synthetic pure Stage 0 tests; no Store, session, Git, native queue or effects."""

import json

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.actions.codec import (
    InvalidAction,
    action_data,
    assemble_envelope,
    decode_envelope,
    decode_intent,
    validate_ref,
)
from heterodyne.actions.contracts import (
    REGISTRY,
    ActionConfig,
    BodyRef,
    Channel,
    Deploy,
    DeployFacts,
    Deployment,
    Envelope,
    Intent,
    Interpretation,
    MergeFacts,
    MergePR,
    Notify,
    NotifyFacts,
    OpenPR,
    Origin,
    PolicyFacts,
    PRFacts,
    PushBranch,
    PushFacts,
    Repository,
)
from heterodyne.actions.policy import adapter_refusal, denial_key, evaluate_policy, materiality
from heterodyne.config import ConfigError

SHA = "a" * 40
SHA256 = "b" * 64
REPO = Repository("product", "product-id", "origin", "sha1", SHA256)
TARGET = Deployment("scratch", "scratch-id", SHA256, "release-id", SHA256)
CHANNEL = Channel("ops", "ops-group", SHA256)
CONFIG = ActionConfig((REPO,), (TARGET,), (CHANNEL,))
ORIGIN = Origin("heterodyne", "btq-71276", "btq-71276:worker:codex", 1, "product")
PUSH = PushBranch("product", "refs/heads/btq/btq-71276", "refs/heads/main", SHA)
PUSH_FACTS = PushFacts(REPO, ORIGIN.bead, PUSH.from_ref, PUSH.to_ref, SHA)
CASES = [
    (PUSH, PUSH_FACTS),
    (OpenPR("product", "refs/heads/feature", "refs/heads/main", "Fix parser", BodyRef(SHA, "docs/pr.md")),
     PRFacts(REPO, SHA)),
    (MergePR("product", 15, SHA, "merge"), MergeFacts(REPO, 15, SHA, "refs/heads/main")),
    (Deploy("scratch"), DeployFacts(TARGET)),
    (Notify("ops", "Reviewed operation complete"), NotifyFacts(CHANNEL)),
]


def envelope() -> Envelope:
    return assemble_envelope(Intent(1, PUSH), ORIGIN, PUSH_FACTS, "btq-ab123", 1, CONFIG)


def encoded(value: object) -> bytes:
    return msgspec.json.encode(value)


@pytest.mark.parametrize(("action", "facts"), CASES)
def test_five_kinds_roundtrip_but_never_admit_effect(action: object, facts: object) -> None:
    raw = json.dumps({"schema_version": 1, "action": msgspec.to_builtins(action)}).encode()
    intent = decode_intent(raw)
    full = Envelope(1, intent.action, facts, ORIGIN, "btq-ab123", 1)  # type: ignore[arg-type]
    checked = decode_envelope(encoded(full), CONFIG)
    assert checked == full
    assert adapter_refusal(checked, CONFIG) == "adapter-unavailable"
    assert len(REGISTRY) == 5 and all(not item.available for item in REGISTRY)


def test_agent_origin_and_authority_fields_rejected() -> None:
    base = {"schema_version": 1, "action": msgspec.to_builtins(PUSH)}
    for field in ("origin", "resolved", "approver", "idempotency_key", "request_revision", "command", "env"):
        with pytest.raises(InvalidAction):
            decode_intent(json.dumps({**base, field: "spoofed"}).encode())


@pytest.mark.parametrize("raw", [
    b'{"schema_version":1,"schema_version":1,"action":{}}',
    b'{"schema_version":1,"action":{"type":"deploy","target":"scratch","target":"scratch"}}',
    b'{"schema_version":true,"action":{"type":"deploy","target":"scratch"}}',
    b'{"schema_version":2,"action":{"type":"deploy","target":"scratch"}}',
    json.dumps({"schema_version": 1, "action": {"type": "merge_pr", "repo": "product",
               "pr_number": True, "expected_head_sha": SHA, "method": "merge"}}).encode(),
    b'{"schema_version":1,"action":{"type":"shell","command":"true"}}',
    b'{"schema_version":1,"action":{"type":"deploy","target":"scratch","force":false}}',
    b'{"schema_version":NaN}', b'"\\ud800"', b'\xff', b'[]',
])
def test_bad_schema_and_duplicate_keys(raw: bytes) -> None:
    with pytest.raises(InvalidAction):
        decode_intent(raw)


@pytest.mark.parametrize("value", ["x\nsecret", "x\x00", "x\x7f", "x\x85", "x\u2028", "a" * 8193,
                                   "ghp_" + "x" * 24, "sk-" + "a" * 20])
def test_secret_or_unsafe_text_refused_without_echo(value: str) -> None:
    with pytest.raises(InvalidAction) as result:
        decode_intent(encoded(Intent(1, Notify("ops", value))))
    assert value not in str(result.value)


def test_nested_secret_in_body_ref_is_not_exempt() -> None:
    action = OpenPR("product", "refs/heads/f", "refs/heads/main", "Title", BodyRef(SHA, "ghp_" + "a" * 24))
    with pytest.raises(InvalidAction, match="secret-bearing"):
        decode_intent(encoded(Intent(1, action)))


@pytest.mark.parametrize("raw", [b"[" * 100 + b"]" * 100, b'"' + b"x" * 32768 + b'"',
                                 json.dumps([1] * 513).encode(), b'{"schema_version":1e309}'])
def test_resource_bounds(raw: bytes) -> None:
    with pytest.raises(InvalidAction):
        decode_intent(raw)


@pytest.mark.parametrize("ref", ["main", "HEAD~1", "refs/heads/../main", "refs/heads/a..b",
                                 "refs/heads/a.lock",
                                 "refs/heads/.hidden", "refs/heads/a/", "refs/heads/a//b",
                                 "refs/heads/a@{1}",
                                 "refs/heads/a b", "refs/heads/a\\b", "refs/heads/a?b", "refs/heads/é"])
def test_unsafe_refs(ref: str) -> None:
    with pytest.raises(InvalidAction):
        validate_ref(ref)


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=60))
def test_safe_branch_grammar(name: str) -> None:
    validate_ref("refs/heads/" + name)


@pytest.mark.parametrize("path", ["/etc/file", "../file", "a/../b", "a//b", "a\\b", "a:object", "a/", "a/."])
def test_body_object_paths_are_relative_pinned_paths(path: str) -> None:
    action = OpenPR("product", "refs/heads/f", "refs/heads/main", "Title", BodyRef(SHA, path))
    with pytest.raises(InvalidAction):
        decode_intent(encoded(Intent(1, action)))


@pytest.mark.parametrize("fmt,size", [("sha1", 40), ("sha256", 64)])
def test_object_formats_follow_host_config(fmt: str, size: int) -> None:
    repo = Repository("product", "product-id", "origin", fmt, SHA256)  # type: ignore[arg-type]
    action = PushBranch("product", PUSH.from_ref, PUSH.to_ref, "a" * size)
    facts = PushFacts(repo, ORIGIN.bead, action.from_ref, action.to_ref, action.expected_sha)
    config = ActionConfig((repo,))
    assert assemble_envelope(Intent(1, action), ORIGIN, facts, "btq-ab123", 1, config).resolved == facts
    wrong = msgspec.structs.replace(action, expected_sha="a" * (64 if size == 40 else 40))
    with pytest.raises(InvalidAction):
        assemble_envelope(Intent(1, wrong), ORIGIN, facts, "btq-ab123", 1, config)


@pytest.mark.parametrize("change", [
    {"source_bead": "btq-zzzzz"}, {"owned_branch": "refs/heads/foreign"},
    {"destination_ref": "refs/heads/other"}, {"source_sha": "c" * 40},
    {"repository": msgspec.structs.replace(REPO, remote_id="other")},
])
def test_push_bindings_reject_drift(change: dict[str, object]) -> None:
    facts = msgspec.structs.replace(PUSH_FACTS, **change)
    with pytest.raises(InvalidAction):
        assemble_envelope(Intent(1, PUSH), ORIGIN, facts, "btq-ab123", 1, CONFIG)


@pytest.mark.parametrize("action,facts", [
    (PUSH, PRFacts(REPO, SHA)),
    (MergePR("product", 2, SHA, "merge"), MergeFacts(REPO, 1, SHA, "refs/heads/main")),
    (MergePR("product", 1, SHA, "merge"), MergeFacts(REPO, 1, "c" * 40, "refs/heads/main")),
    (Deploy("scratch"), DeployFacts(msgspec.structs.replace(TARGET, definition_revision="c" * 64))),
    (Notify("ops", "done"), NotifyFacts(msgspec.structs.replace(CHANNEL, channel_id="foreign"))),
])
def test_cross_kind_and_target_drift(action: object, facts: object) -> None:
    with pytest.raises(InvalidAction):
        decode_envelope(encoded(Envelope(1, action, facts, ORIGIN, "btq-ab123", 1)), CONFIG)  # type: ignore


@pytest.mark.parametrize("field,value", [("generation", True), ("generation", 0), ("bead", "btq-old"),
                                        ("session_key", "../other"), ("repo_alias", "other")])
def test_origin_shapes_are_strict_not_an_authentication_claim(field: str, value: object) -> None:
    origin = msgspec.structs.replace(ORIGIN, **{field: value})
    with pytest.raises(InvalidAction):
        assemble_envelope(Intent(1, PUSH), origin, PUSH_FACTS, "btq-ab123", 1, CONFIG)


def test_config_duplicate_alias_and_unknown_target_refuse() -> None:
    with pytest.raises(InvalidAction):
        decode_envelope(encoded(envelope()), ActionConfig((REPO, REPO)))
    with pytest.raises(InvalidAction):
        decode_envelope(encoded(envelope()), ActionConfig())


def test_full_action_data_retains_id_revision_origin_and_targets_without_context_digest() -> None:
    data = action_data(envelope(), CONFIG)
    assert data == json.loads(encoded(envelope()))
    assert data["idempotency_key"] == "btq-ab123"
    assert "decided_by" not in data and "context_digest" not in data


@pytest.mark.parametrize("tier", ["auto_approve", "escalate", "hard_deny"])
def test_outward_floor_cannot_be_lowered_by_host(tier: str) -> None:
    result = evaluate_policy(envelope(), CONFIG, PolicyFacts(SHA256), {"push_branch": tier}, {})
    assert result.tier == ("hard_deny" if tier == "hard_deny" else "escalate")


def test_permissions_widening_is_hard_deny_and_bad_policy_never_passes() -> None:
    assert evaluate_policy(envelope(), CONFIG, PolicyFacts(SHA256, True), {}, {}).tier == "hard_deny"
    for override in ({"unknown": "auto_approve"}, {"push_branch": "unknown"}):
        with pytest.raises(ConfigError):
            evaluate_policy(envelope(), CONFIG, PolicyFacts(SHA256), override, {})
    with pytest.raises(InvalidAction):
        evaluate_policy(envelope(), CONFIG, PolicyFacts("bad"), {}, {})


def test_restriction_and_hard_deny_have_no_approve_or_dispatch_route() -> None:
    result = evaluate_policy(envelope(), CONFIG, PolicyFacts(SHA256), {}, {"hard_deny": ["push_branch"]})
    assert result.tier == "hard_deny" and adapter_refusal(envelope(), CONFIG) == "adapter-unavailable"


def test_denial_key_survives_prose_rewording_revision_and_session_generation() -> None:
    first = envelope()
    later = assemble_envelope(
        Intent(1, PUSH, "new explanation"), msgspec.structs.replace(ORIGIN, generation=2),
        PUSH_FACTS, "btq-zzzzz", 17, CONFIG
    )
    assert denial_key(first, CONFIG) == denial_key(later, CONFIG)
    other = msgspec.structs.replace(first, origin=msgspec.structs.replace(ORIGIN, session_key="other"))
    assert denial_key(first, CONFIG) != denial_key(other, CONFIG)
    changed = PushBranch("product", PUSH.from_ref, PUSH.to_ref, "c" * 40)
    changed_facts = msgspec.structs.replace(PUSH_FACTS, source_sha=changed.expected_sha)
    corrected = assemble_envelope(Intent(1, changed), ORIGIN, changed_facts, "btq-ab123", 2, CONFIG)
    assert denial_key(first, CONFIG) != denial_key(corrected, CONFIG)


def test_materiality_cannot_select_dispatch_or_turn_question_into_action() -> None:
    original = Interpretation("heterodyne", "product-id", ("fix parser",), "question", False, False)
    assert materiality(original, original).result == "minor"
    for change in ({"kind": "action"}, {"workstream": "another"}, {"repository_id": "other"},
                   {"requirements": ("delete data",)}, {"outward": True}, {"destructive": True},
                   {"unique_resolution": False}):
        result = materiality(original, msgspec.structs.replace(original, **change))
        assert result.result == "confirm" and not result.dispatch_allowed
    ambiguous = msgspec.structs.replace(original, unique_resolution=False)
    assert materiality(ambiguous, ambiguous).result == "confirm"


def test_materiality_input_is_bounded_and_secret_checked() -> None:
    bad = Interpretation("heterodyne", "product-id", ("ghp_" + "a" * 24,), "action", True, False)
    with pytest.raises(InvalidAction):
        materiality(bad, bad)


@pytest.mark.parametrize("action,facts", CASES)
def test_every_registered_kind_gets_outward_floor(action: object, facts: object) -> None:
    full = decode_envelope(
        encoded(Envelope(1, action, facts, ORIGIN, "btq-ab123", 1)), CONFIG  # type: ignore
    )
    kind = msgspec.to_builtins(full.action)["type"]
    assert evaluate_policy(full, CONFIG, PolicyFacts(SHA256), {kind: "auto_approve"}, {}).tier == "escalate"
    assert evaluate_policy(full, CONFIG, PolicyFacts(SHA256, True), {}, {}).tier == "hard_deny"


@pytest.mark.parametrize("revision", [True, 0, -1, 2**31])
def test_invalid_request_revision_never_reaches_ask_projection(revision: int) -> None:
    invalid = msgspec.structs.replace(envelope(), request_revision=revision)
    with pytest.raises(InvalidAction):
        action_data(invalid, CONFIG)


def test_records_and_registry_are_immutable() -> None:
    with pytest.raises(AttributeError):
        envelope().request_revision = 2  # type: ignore[misc]
    with pytest.raises(AttributeError):
        REGISTRY[0].available = True  # type: ignore[misc]


def test_denial_key_changes_with_resolved_resource_identity() -> None:
    first = assemble_envelope(Intent(1, Notify("ops", "done")), ORIGIN,
                              NotifyFacts(CHANNEL), "btq-ab123", 1, CONFIG)
    next_channel = msgspec.structs.replace(CHANNEL, channel_id="new-group")
    config = ActionConfig((REPO,), (TARGET,), (next_channel,))
    second = assemble_envelope(Intent(1, Notify("ops", "done")), ORIGIN,
                               NotifyFacts(next_channel), "btq-ab123", 1, config)
    assert denial_key(first, CONFIG) != denial_key(second, config)


def test_materiality_requirement_count_is_bounded() -> None:
    bad = Interpretation("heterodyne", "product-id", ("request",) * 33, "question", False, False)
    with pytest.raises(InvalidAction):
        materiality(bad, bad)
