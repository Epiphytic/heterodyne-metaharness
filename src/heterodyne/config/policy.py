"""Host-only policy and effective action tiers (ADR 0001 §5.3, §15)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

RANK = {"auto_approve": 0, "escalate": 1, "hard_deny": 2}
POLICY_KEYS = frozenset({"approvers", "identities", "operators", "tiers", "hard_deny_rules",
                         "action_registry", "tier_floor", "policy"})
RESTRICT_KEYS = frozenset({"escalate", "hard_deny"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    approvers: tuple[str, ...] = ()
    identities: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    tiers: dict[str, str] = field(default_factory=dict[str, str])


def effective_tiers(default_tiers: Mapping[str, Sequence[str]], host_overrides: Mapping[str, str],
                    restrict: Mapping[str, Sequence[str]]) -> dict[str, str]:
    tier = {c: t for t in RANK for c in default_tiers.get(t, [])}
    locked = set(default_tiers.get("locked", []))
    for cls, target in host_overrides.items():
        if cls not in tier or target not in RANK:
            raise ConfigError(f"policy.toml [tiers]: unknown class or tier {cls} = {target!r}")
        if cls in locked and RANK[target] < RANK[tier[cls]]:
            raise ConfigError(f"policy.toml [tiers]: {cls} is locked and cannot be lowered")
        tier[cls] = target
    unknown = set(restrict) - RESTRICT_KEYS
    if unknown:
        raise ConfigError(f"[restrict] allows only {sorted(RESTRICT_KEYS)}, not {sorted(unknown)}")
    for key in ("escalate", "hard_deny"):
        for cls in restrict.get(key, []):
            if cls not in tier:
                raise ConfigError(f"[restrict].{key}: unknown class {cls}")
            if RANK[key] < RANK[tier[cls]]:
                raise ConfigError(f"[restrict].{key} would relax {cls} (currently {tier[cls]})")
            tier[cls] = key
    return tier


def build_policy(raw: Mapping[str, Any], default_tiers: Mapping[str, Sequence[str]],
                 restrict: Mapping[str, Sequence[str]]) -> Policy:
    """Policy from the parsed host `policy.toml` (already secret-checked by the loader)."""
    unknown = set(raw) - POLICY_KEYS
    if unknown:
        raise ConfigError(f"policy.toml: unknown keys {sorted(unknown)}")
    return Policy(approvers=tuple(raw.get("approvers", ())),
                  identities=dict(raw.get("identities", {})),
                  tiers=effective_tiers(default_tiers, raw.get("tiers", {}), restrict))
