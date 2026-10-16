"""Pure Stage 0 projections; no classification model, approvals, dispatch or effects."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from heterodyne.actions.codec import action_data, validate_interpretation, validate_policy_facts
from heterodyne.actions.contracts import (
    REGISTRY,
    ActionConfig,
    Envelope,
    Interpretation,
    Materiality,
    PolicyFacts,
    PolicyResult,
    Tier,
)
from heterodyne.config.policy import RANK, effective_tiers

_DEFAULT_TIERS = {"escalate": tuple(entry.kind for entry in REGISTRY)}


def evaluate_policy(envelope: Envelope, config: ActionConfig, facts: PolicyFacts,
                    host_overrides: Mapping[str, str], restrict: Mapping[str, Sequence[str]]) -> PolicyResult:
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
    data = action_data(envelope, config)
    key = {"domain": "plan5-denial-v1", "workstream": envelope.origin.workstream,
           "session_key": envelope.origin.session_key, "action": data["action"], "resolved": data["resolved"]}
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def materiality(original: Interpretation, proposed: Interpretation) -> Materiality:
    """Conservative exact semantic comparison; resolution ambiguity must be represented by caller.

    Use minor only after unique canonical reference resolution: this helper does not resolve aliases.
    Neither result permits dispatch, design approval or an outward effect.
    """
    left, right = validate_interpretation(original), validate_interpretation(proposed)
    return Materiality("minor" if left == right and left.unique_resolution else "confirm")
