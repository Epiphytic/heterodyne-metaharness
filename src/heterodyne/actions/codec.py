"""Bounded pure codecs. No Git, native queue, files, credentials or transport access.

Branch ownership/object existence/ref protection must later be independently checked at runtime.
Configured typed facts are inputs, not proof that a caller obtained them from a trusted source.
"""

import json
import re
import unicodedata
from typing import Never, cast

import msgspec

from heterodyne.actions.contracts import (
    ActionConfig,
    Deploy,
    DeployFacts,
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
    Resolved,
)
from heterodyne.config.secret_scan import secret_value

MAX_BYTES = 32_768
MAX_DEPTH = 8
MAX_NODES = 512
MAX_TEXT = 8_192
BEAD_ID = re.compile(r"[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}")
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")


class InvalidAction(ValueError):
    """Fixed safe diagnostics never interpolate untrusted input."""


def safe_label(value: object, maximum: int = 256) -> bool:
    """Closed printable ASCII identity labels; no caller text in diagnostics."""
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.isascii()
        and all(32 <= ord(c) < 127 for c in value)
    )


def _fail(reason: str) -> Never:
    raise InvalidAction(reason)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate-field")
        result[key] = value
    return result


def _walk(value: object, depth: int = 0) -> int:
    if depth > MAX_DEPTH:
        _fail("input-depth")
    if isinstance(value, str):
        if len(value) > MAX_TEXT or any(
            ord(c) < 32
            or 127 <= ord(c) <= 159
            or 0xD800 <= ord(c) <= 0xDFFF
            or c in "\u2028\u2029"
            or unicodedata.category(c) == "Cf"
            for c in value
        ):
            _fail("unsafe-text")
        if secret_value(value):
            _fail("secret-bearing-input")
    elif isinstance(value, dict):
        children = [child for pair in cast(dict[object, object], value).items() for child in pair]
        return 1 + sum(_walk(child, depth + 1) for child in children)
    elif isinstance(value, list):
        return 1 + sum(_walk(child, depth + 1) for child in cast(list[object], value))
    elif type(value) not in (int, bool, type(None)):
        _fail("unsupported-scalar")
    if type(value) is int and not 0 <= value <= 2**63 - 1:
        _fail("integer-bound")
    return 1


def _reject_constant(_: str) -> None:
    _fail("unsupported-scalar")


def _decode[T](data: bytes, cls: type[T], *, max_bytes: int = MAX_BYTES, max_nodes: int = MAX_NODES) -> T:
    if type(data) is not bytes or len(data) > max_bytes:
        _fail("input-size")
    try:
        raw: object = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
        if _walk(raw) > max_nodes:
            _fail("input-nodes")
        return msgspec.convert(raw, type=cls, strict=True)
    except InvalidAction:
        raise
    except (UnicodeError, RecursionError, ValueError, msgspec.ValidationError):
        raise InvalidAction("invalid-schema") from None


def encode_record(value: object) -> bytes:
    """Typed host values can be malformed too; never expose encoder diagnostics."""
    try:
        return msgspec.json.encode(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise InvalidAction("invalid-host-record") from None


def _match(pattern: re.Pattern[str], value: str) -> None:
    if not pattern.fullmatch(value):
        _fail("invalid-identifier")


def _positive(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        _fail("invalid-positive-integer")


def validate_ref(value: str) -> None:
    """Deliberately restricted full branch refs; runtime Git check remains mandatory."""
    if (
        not value.startswith("refs/heads/")
        or len(value) > 255
        or not value.isascii()
        or any(c in value for c in " ~^:?*[\\")
        or ".." in value
        or "@{" in value
    ):
        _fail("invalid-branch-ref")
    parts = value.split("/")
    if any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in parts):
        _fail("invalid-branch-ref")
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        _fail("invalid-branch-ref")


def _path(value: str) -> None:
    if (
        len(value) > 512
        or not value.isascii()
        or not value
        or any(
            not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", part) or part.endswith(".")
            for part in value.split("/")
        )
    ):
        _fail("invalid-object-path")


def _oid(value: str, object_format: str) -> None:
    size = {"sha1": 40, "sha256": 64}.get(object_format)
    if size is None or not re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value):
        _fail("invalid-object-id")


def _action(action: object) -> None:
    if isinstance(action, (PushBranch, OpenPR, MergePR)):
        _match(_ALIAS, action.repo)
    if isinstance(action, PushBranch):
        validate_ref(action.from_ref)
        validate_ref(action.to_ref)
    elif isinstance(action, OpenPR):
        validate_ref(action.head)
        validate_ref(action.base)
        _path(action.body_ref.path)
        if not action.title or len(action.title) > 200 or action.head == action.base:
            _fail("invalid-pr")
    elif isinstance(action, MergePR):
        _positive(action.pr_number)
    elif isinstance(action, Deploy):
        _match(_ALIAS, action.target)
    elif isinstance(action, Notify):
        _match(_ALIAS, action.channel_ref)
        if not action.text or len(action.text) > 3500:
            _fail("invalid-notice")
    else:
        _fail("unknown-action")


def decode_intent(data: bytes) -> Intent:
    """Agent-facing pure shape only. Host origin/resolved/approver fields are forbidden."""
    value = _decode(data, Intent)
    _action(value.action)
    # Format choice is host-owned; syntax accepts only supported complete object IDs here.
    sha = None
    if isinstance(value.action, PushBranch):
        sha = value.action.expected_sha
    elif isinstance(value.action, MergePR):
        sha = value.action.expected_head_sha
    elif isinstance(value.action, OpenPR):
        sha = value.action.body_ref.commit
    if sha is not None:
        if len(sha) not in (40, 64):
            _fail("invalid-object-id")
        _oid(sha, "sha1" if len(sha) == 40 else "sha256")
    return value


def _config(config: ActionConfig) -> ActionConfig:
    config = _decode(encode_record(config), ActionConfig, max_bytes=131_072, max_nodes=8192)
    for entries in (config.repositories, config.deployments, config.channels):
        if len(entries) > 64 or len({e.alias for e in entries}) != len(entries):
            _fail("invalid-config-aliases")
        for entry in entries:
            _match(_ALIAS, entry.alias)
    for repo in config.repositories:
        _match(_ID, repo.repository_id)
        _match(_ID, repo.remote_id)
        _oid(repo.mapping_revision, "sha256")
    for target in config.deployments:
        _match(_ID, target.target_id)
        _match(_ID, target.artifact_id)
        _oid(target.definition_revision, "sha256")
        _oid(target.artifact_sha256, "sha256")
    for channel in config.channels:
        _match(_ID, channel.channel_id)
        _oid(channel.definition_revision, "sha256")
    return config


def _push_binding(action: PushBranch, facts: PushFacts, origin: Origin) -> None:
    _oid(action.expected_sha, facts.repository.object_format)
    if (
        facts.source_bead != origin.bead
        or facts.owned_branch != action.from_ref
        or facts.destination_ref != action.to_ref
        or facts.source_sha != action.expected_sha
    ):
        _fail("push-binding")


def _merge_binding(action: MergePR, facts: MergeFacts) -> None:
    _oid(action.expected_head_sha, facts.repository.object_format)
    validate_ref(facts.base_ref)
    if facts.pr_number != action.pr_number or facts.head_sha != action.expected_head_sha:
        _fail("merge-binding")


def _repository_binding(value: Envelope, config: ActionConfig) -> None:
    action, facts = value.action, value.resolved
    if not isinstance(facts, (PushFacts, PRFacts, MergeFacts)):
        return
    repo = facts.repository
    if repo not in config.repositories or value.origin.repo_alias != repo.alias:
        _fail("repository-binding")
    if not isinstance(action, (PushBranch, OpenPR, MergePR)) or action.repo != repo.alias:
        _fail("repository-binding")
    if isinstance(action, PushBranch) and isinstance(facts, PushFacts):
        _push_binding(action, facts, value.origin)
    elif isinstance(action, OpenPR) and isinstance(facts, PRFacts):
        _oid(action.body_ref.commit, repo.object_format)
        _oid(facts.head_sha, repo.object_format)
    elif isinstance(action, MergePR) and isinstance(facts, MergeFacts):
        _merge_binding(action, facts)
    else:
        _fail("resolved-kind-mismatch")


def decode_envelope(data: bytes, config: ActionConfig) -> Envelope:
    """Trusted-input codec, NOT a session authenticator or native approval verifier."""
    config = _config(config)
    value = _decode(data, Envelope)
    _action(value.action)
    origin = value.origin
    _match(_ALIAS, origin.workstream)
    _match(_ALIAS, origin.repo_alias)
    _match(BEAD_ID, origin.bead)
    _match(_ID, origin.session_key)
    _positive(origin.generation)
    _positive(value.request_revision)
    _match(BEAD_ID, value.idempotency_key)
    if origin.repo_alias not in {r.alias for r in config.repositories}:
        _fail("unknown-origin-repository")
    _repository_binding(value, config)
    if isinstance(value.action, Deploy):
        if (
            not isinstance(value.resolved, DeployFacts)
            or value.resolved.target not in config.deployments
            or value.action.target != value.resolved.target.alias
        ):
            _fail("deployment-binding")
    elif isinstance(value.action, Notify):
        if (
            not isinstance(value.resolved, NotifyFacts)
            or value.resolved.channel not in config.channels
            or value.action.channel_ref != value.resolved.channel.alias
        ):
            _fail("channel-binding")
    elif not isinstance(value.resolved, (PushFacts, PRFacts, MergeFacts)):
        _fail("resolved-kind-mismatch")
    return value


def validate_request_inputs(intent: Intent, origin: Origin, resolved: Resolved, config: ActionConfig) -> None:
    """Validate the same immutable tuple before allocation. No approval is allocated or asserted.

    The private syntax sentinel is never returned, persisted or hashed as an ask identity.
    The allocated native ID must subsequently pass assemble_envelope unchanged.
    """
    checked = decode_intent(encode_record(intent))
    decode_envelope(
        encode_record(Envelope(1, checked.action, resolved, origin, "validation-00000", 1)), config
    )


def assemble_envelope(
    intent: Intent, origin: Origin, resolved: Resolved, approval_id: str, revision: int, config: ActionConfig
) -> Envelope:
    """Attach separate host-provided facts; callers still owe real launch/native authentication."""
    intent = decode_intent(encode_record(intent))
    envelope = Envelope(1, intent.action, resolved, origin, approval_id, revision)
    return decode_envelope(encode_record(envelope), config)


def action_data(envelope: Envelope, config: ActionConfig) -> dict[str, object]:
    """Whole ID/revision/origin/target payload for the native whole-ask adapter, not its digest.

    Caller retains title, description and canonical repo locators/resolved refs in the native ask.
    Do not hash this projection as a replacement for native context_digest.
    """
    checked = decode_envelope(encode_record(envelope), config)
    return msgspec.to_builtins(checked)


def validate_policy_facts(value: PolicyFacts) -> PolicyFacts:
    value = _decode(encode_record(value), PolicyFacts)
    _oid(value.revision, "sha256")
    return value


def validate_interpretation(value: Interpretation) -> Interpretation:
    value = _decode(encode_record(value), Interpretation)
    _match(_ALIAS, value.workstream)
    _match(_ID, value.repository_id)
    if len(value.requirements) > 32 or any(not item for item in value.requirements):
        _fail("invalid-requirements")
    return value


def validate_text(value: str) -> None:
    """Apply shared bounded text/control/secret checks to a host observation."""
    _walk(value)
