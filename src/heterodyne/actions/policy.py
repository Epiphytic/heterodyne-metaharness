"""Pure Stage 0 projections; no classification model, approvals, dispatch or effects."""

import hashlib
import json
from collections.abc import Mapping
from typing import cast

import msgspec

from heterodyne.actions.codec import (
    action_data,
    validate_interpretation,
    validate_policy_facts,
    validate_request_inputs,
)
from heterodyne.actions.contracts import (
    REGISTRY,
    ActionConfig,
    Envelope,
    Intent,
    Interpretation,
    Materiality,
    Origin,
    PolicyFacts,
    PolicyResult,
    Resolved,
    Tier,
)
from heterodyne.config.policy import RANK, effective_tiers

_DEFAULT_TIERS = {"escalate": tuple(entry.kind for entry in REGISTRY)}


def evaluate_policy(
    envelope: Envelope,
    config: ActionConfig,
    facts: PolicyFacts,
    host_overrides: Mapping[str, str],
    restrict: Mapping[str, list[str]],
) -> PolicyResult:
    """Host overrides/restrictions retain existing semantics, then apply the outward floor."""
    payload = action_data(envelope, config)
    facts = validate_policy_facts(facts)
    # Validate even when a hard deny applies; malformed host policy is not usable policy.
    tiers = effective_tiers(_DEFAULT_TIERS, host_overrides, restrict)
    action = cast(dict[str, object], payload["action"])
    tier = tiers[cast(str, action["type"])]
    if facts.widens_permissions:
        return PolicyResult("hard_deny", "permissions-widening")
    if RANK[tier] < RANK["escalate"]:
        return PolicyResult("escalate", "outward-floor")
    return PolicyResult(cast(Tier, tier), "policy")


def adapter_refusal(envelope: Envelope, config: ActionConfig) -> str:
    """All five Stage 0 entries refuse. No enable flag or effect port exists."""
    action_data(envelope, config)
    return "adapter-unavailable"


def denial_key(envelope: Envelope, config: ActionConfig) -> str:
    """Specified intent-denial fingerprint, NOT an approval/context digest.

    Session scope survives generation changes. Ignore explanation, approval ID and request revision;
    retain all effect fields, immutable targets and host mapping revision.
    """
    action_data(envelope, config)
    return request_denial_key(Intent(1, envelope.action), envelope.origin, envelope.resolved, config)


def request_denial_key(intent: Intent, origin: Origin, resolved_facts: Resolved, config: ActionConfig) -> str:
    """Same denial identity before and after shell allocation; no synthetic approval identity."""
    validate_request_inputs(intent, origin, resolved_facts, config)
    action = cast(dict[str, object], msgspec.to_builtins(intent.action))
    # Presentation aliases are not target identities. Preserve every actual effect field.
    action = {key: value for key, value in action.items() if key not in {"repo", "target", "channel_ref"}}
    resolved = cast(dict[str, object], msgspec.to_builtins(resolved_facts))
    resolved = {
        key: (
            {k: v for k, v in cast(dict[str, object], value).items() if k != "alias"}
            if isinstance(value, dict)
            else value
        )
        for key, value in resolved.items()
    }
    key = {
        "domain": "plan5-denial-v1",
        "workstream": origin.workstream,
        "session_key": origin.session_key,
        "action": action,
        "resolved": resolved,
    }
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materiality(original: Interpretation, proposed: Interpretation) -> Materiality:
    """Conservative exact semantic comparison; resolution ambiguity must be represented by caller.

    Use minor only after unique canonical reference resolution: this helper does not resolve aliases.
    Neither result permits dispatch, design approval or an outward effect.
    """
    left, right = validate_interpretation(original), validate_interpretation(proposed)
    return Materiality("minor" if left == right and left.unique_resolution else "confirm")
