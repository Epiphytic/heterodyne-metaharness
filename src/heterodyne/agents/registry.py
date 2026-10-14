"""The adapters the sandbox runtime can launch, by the name profiles give as `adapter`."""

from collections.abc import Mapping

from heterodyne.agents.base import Adapter
from heterodyne.agents.claude_code import ClaudeCode
from heterodyne.agents.codex import Codex

ADAPTERS: Mapping[str, Adapter] = {"claude-code": ClaudeCode(), "codex": Codex()}


def login_names() -> frozenset[str]:
    """Every adapter's login file names: what `stage` refuses as an install entry's name (plan 4c E7)."""
    return frozenset(n for adapter in ADAPTERS.values() for n in adapter.login_names)
