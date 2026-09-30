"""Configuration layering (ADR 0001 §15): defaults -> host -> workstream -> env, with host-only policy."""

import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from heterodyne.config import layers, paths
from heterodyne.config.policy import ConfigError, Policy, load_policy

__all__ = ["Config", "ConfigError", "Policy", "load"]


@dataclass(frozen=True)
class Config:
    values: dict[str, Any]
    sources: dict[str, str]
    policy: Policy

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in dotted.split("."):
            table = cast(dict[str, Any], node) if isinstance(node, dict) else None
            if table is None or part not in table:
                return default
            node = table[part]
        return node


def load(workstream: str | None = None, env: Mapping[str, str] = os.environ) -> Config:
    config_dir = paths.config_dir(env)
    defaults = layers.read_defaults()
    host = layers.read_toml(config_dir / "config.toml")
    layers.check_host(host)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    layers.merge(values, copy.deepcopy(defaults), "defaults", sources)
    layers.merge(values, host, "host:config.toml", sources)
    restrict: dict[str, Any] = {}
    if workstream:
        ws = layers.read_toml(config_dir / "workstreams" / f"{workstream}.toml")
        layers.check_workstream(workstream, ws, values)
        restrict = ws.pop("restrict", {})
        layers.merge(values, ws, f"workstream:{workstream}.toml", sources)
    env_values, env_labels = layers.env_layer(env)
    for table, entries in env_values.items():
        values.setdefault(table, {}).update(entries)
    sources.update(env_labels)
    layers.check_profiles(values)
    layers.check_secrets(values)
    policy = load_policy(config_dir / "policy.toml", defaults.get("tiers", {}), restrict)
    return Config(values=values, sources=sources, policy=policy)
