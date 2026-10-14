"""The flags a profile's `args` may carry, per adapter (plan 4c Amendment 1 Q4). Standard library only, so
`config` still imports no adapter code; each adapter's `profile_flags` must equal its entry here.

Adding a flag is a reviewed change: the review must show that the flag can't set configuration, choose a
config profile, or change approvals, the sandbox, settings sources, MCP or plugins."""

from collections.abc import Mapping
from typing import Any, cast

from heterodyne.config.errors import ConfigError

# Each allowed flag, mapped to whether it takes a value. Both adapters start empty.
FLAGS: Mapping[str, Mapping[str, bool]] = {"claude-code": {}, "codex": {}}

REFUSED = "{where}: argument {index} is not a flag the {adapter} adapter allows"
NO_VALUE = "{where}: argument {index} needs a value for the {adapter} adapter"
NOT_A_LIST = "{where} must be a list of strings"


def check(adapter: str, args: object, where: str, *,
          flags: Mapping[str, Mapping[str, bool]] = FLAGS) -> None:
    """Refuse `args` unless each token is an allowed flag or an allowed flag's value, read left to right.
    The refusal names the token's index, never the token: a value may be a secret."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in cast(list[Any], args)):
        raise ConfigError(NOT_A_LIST.format(where=where))
    tokens = cast(list[str], args)
    allowed = flags.get(adapter, {})
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in allowed:
            if allowed[token]:
                if i + 1 >= len(tokens):
                    raise ConfigError(NO_VALUE.format(where=where, index=i, adapter=adapter))
                i += 1
        else:
            flag = token.split("=", 1)[0]
            # Only a long flag that takes a value may be written --flag=value.
            if not (token.startswith("--") and "=" in token and allowed.get(flag) is True):
                raise ConfigError(REFUSED.format(where=where, index=i, adapter=adapter))
        i += 1
