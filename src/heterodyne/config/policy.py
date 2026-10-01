"""Host-only policy and effective action tiers (ADR 0001 §5.3, §15)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from heterodyne.config.errors import ConfigError
from heterodyne.config.secret_scan import show

__all__ = ["RANK", "ConfigError", "Policy", "build_policy", "effective_tiers"]

RANK = {"auto_approve": 0, "escalate": 1, "hard_deny": 2}
POLICY_KEYS = frozenset({"approvers", "identities", "operators", "tiers", "hard_deny_rules",
                         "action_registry", "tier_floor", "policy"})
RESTRICT_KEYS = frozenset({"escalate", "hard_deny"})




@dataclass(frozen=True)
class Policy:
    approvers: tuple[str, ...] = ()
    identities: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    tiers: dict[str, str] = field(default_factory=dict[str, str])
    operators: tuple[str, ...] = ()


def effective_tiers(default_tiers: Mapping[str, Sequence[str]], host_overrides: Mapping[str, str],
                    restrict: Mapping[str, Sequence[str]]) -> dict[str, str]:
    tier = {c: t for t in RANK for c in default_tiers.get(t, [])}
    locked = set(default_tiers.get("locked", []))
    for cls, target in host_overrides.items():
        if cls not in tier or target not in RANK:
            raise ConfigError("policy.toml [tiers]: unknown class or tier "
                              f"{show(cls, False)} = {show(target)}")
        if cls in locked and RANK[target] < RANK[tier[cls]]:
            raise ConfigError(f"policy.toml [tiers]: {show(cls, False)} is locked and cannot be lowered")
        tier[cls] = target
    unknown = set(restrict) - RESTRICT_KEYS
    if unknown:
        raise ConfigError(f"[restrict] allows only {sorted(RESTRICT_KEYS)}, not {show(unknown)}")
    for key in ("escalate", "hard_deny"):
        listed: Any = restrict.get(key, [])
        if not isinstance(listed, list) or not all(isinstance(c, str) for c in cast(list[Any], listed)):
            raise ConfigError(f"[restrict].{key} must be a list of action-class names")
        for cls in cast(list[str], listed):
            if cls not in tier:
                raise ConfigError(f"[restrict].{key}: unknown class {show(cls)}")
            if RANK[key] < RANK[tier[cls]]:
                raise ConfigError(f"[restrict].{key} would relax {show(cls, False)} (currently {tier[cls]})")
            tier[cls] = key
    return tier


def build_policy(raw: Mapping[str, Any], default_tiers: Mapping[str, Sequence[str]],
                 restrict: Mapping[str, Sequence[str]]) -> Policy:
    """Policy from the parsed host `policy.toml` (already secret-checked by the loader)."""
    unknown = set(raw) - POLICY_KEYS
    if unknown:
        raise ConfigError(f"policy.toml: unknown keys {show(unknown)}")
    approvers: Any = raw.get("approvers", [])
    if not isinstance(approvers, list) or not all(isinstance(a, str) for a in cast(list[Any], approvers)):
        raise ConfigError("policy.toml: approvers must be a list of names")
    operators: Any = raw.get("operators", [])
    if not isinstance(operators, list) or not all(isinstance(o, str) for o in cast(list[Any], operators)):
        raise ConfigError("policy.toml: operators must be a list of approver names")
    missing = [o for o in cast(list[str], operators) if o not in approvers]
    if missing:
        raise ConfigError(f"policy.toml: operators {show(missing)} are not in approvers")
    return Policy(approvers=tuple(cast(list[str], approvers)),
                  identities=_identities(raw.get("identities", {})),
                  tiers=effective_tiers(default_tiers, _tier_overrides(raw.get("tiers", {})), restrict),
                  operators=tuple(cast(list[str], operators)))


def _identities(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ConfigError("policy.toml: identities must be a table of tables")
    out: dict[str, dict[str, str]] = {}
    for name, ids in cast(dict[str, Any], value).items():
        fields: Any = ids
        if not isinstance(fields, dict) or not all(
                isinstance(v, str) for v in cast(dict[str, Any], fields).values()):
            raise ConfigError(f"policy.toml: identities.{show(name, False)} must be a table of strings")
        out[name] = dict(cast(dict[str, str], ids))
    return out


def _tier_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
            isinstance(v, str) for v in cast(dict[str, Any], value).values()):
        raise ConfigError("policy.toml: tiers must be a table of class = \"tier\" strings")
    return cast(dict[str, str], value)
