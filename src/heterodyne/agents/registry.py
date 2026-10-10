"""The adapters the sandbox runtime can launch, by the name profiles give as `adapter`."""

from collections.abc import Mapping

from heterodyne.agents.base import Adapter
from heterodyne.agents.claude_code import ClaudeCode
from heterodyne.agents.codex import Codex

ADAPTERS: Mapping[str, Adapter] = {"claude-code": ClaudeCode(), "codex": Codex()}
