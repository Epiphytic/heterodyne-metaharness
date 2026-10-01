"""Best-effort detection of inline secrets in config layers (ADR 0001 §15).

ADR §15 says secrets never appear inline in any layer; config refers to them only by
`{ file = "<path>" }` or `{ command = "<command>" }`. This module enforces that at load time with
two independent checks, applied to every key and value in a layer, arrays included:

- **Name check:** a key whose name contains a secret-like word (matched per segment, split on
  `_`, `-`, `.` and camelCase) must hold a reference.
- **Value check:** a string (value, key, or reference target) that looks like secret material,
  such as an nsec, a PEM private key, a common vendor token prefix or a JWT, is rejected whatever its
  key name is.

Both checks are heuristics and can never be complete. The second layer is the CI and pre-commit
gitleaks gate required by ADR §15 (Task 9). The complete fix is a schema key allowlist per
table, which is future work: later plans define those keys. Error messages never echo the
matched value.
"""

import re
from collections.abc import Mapping
from typing import Any, cast

from heterodyne.config.errors import ConfigError

REFERENCE_KEYS = ("file", "command")

# Any one segment equal to one of these marks the key as secret-named.
SECRET_WORDS = frozenset({
    "credential", "credentials", "cred", "creds", "auth", "authorization", "bearer", "cookie",
    "cookies", "passphrase", "pass", "password", "passwd", "pwd", "secret", "secrets", "token",
    "tokens", "nsec", "apikey", "privkey", "session", "sessions",
})
# A final segment of `key` (bare `key`, `api_key`, `private_key`, `access_key`, `signing_key`, ...).
KEY_SEGMENT = "key"
# Narrow, explicit exemptions for legitimate names the pattern would otherwise flag.
EXEMPT_NAMES = frozenset({
    "public_key",  # public by definition (identities, signing verification)
})

# A token prefix must start the string or follow a non-alphanumeric character. Unlike `\b`, this
# still matches after `_` (for example `HETERODYNE_ghp_...`), but not inside a word (`risk-...`).
_START = r"(?<![A-Za-z0-9])"

SECRET_VALUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("nostr nsec", re.compile(_START + r"nsec1[02-9ac-hj-np-z]{20,}")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("sk- API key", re.compile(_START + r"sk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub token", re.compile(_START + r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained token", re.compile(_START + r"github_pat_[A-Za-z0-9_]{20,}")),
    ("Slack token", re.compile(_START + r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("AWS access key ID", re.compile(_START + r"(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")),
    ("JWT", re.compile(_START + r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# Not secrets, but identifiers that must never be printed (ADR §15, "never print npubs"); `show` only.
# Redaction deliberately matches anywhere in the string (no boundaries): these never reject a config,
# so an embedded identifier ("operator" + npub, "a" + 64 hex) must still be hidden.
IDENTIFIER_VALUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("npub", re.compile(r"npub1[02-9ac-hj-np-z]{20,}", re.IGNORECASE)),
    ("hex key", re.compile(r"[0-9A-Fa-f]{64}")),
)

_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _escape_controls(text: str) -> str:
    """C0, DEL and C1 characters as `\\xNN`, so no value can drive a terminal."""
    return _CONTROLS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_SEPARATORS = re.compile(r"[_\-.]+")


def segments(key: str) -> list[str]:
    return [s for s in _SEPARATORS.split(_CAMEL.sub(r"\1_\2", key).lower()) if s]


def secret_name(key: str) -> bool:
    parts = segments(key)
    if "_".join(parts) in EXEMPT_NAMES:
        return False
    return bool(parts) and (parts[-1] == KEY_SEGMENT or any(p in SECRET_WORDS for p in parts))


def secret_value(text: str) -> str | None:
    """The kind of secret material `text` contains, or None."""
    for kind, pattern in SECRET_VALUES:
        if pattern.search(text):
            return kind
    return None


def identifier_kind(text: str) -> str | None:
    """The kind of public identifier (npub, 64-hex key) embedded in `text`, or None."""
    for ident, pattern in IDENTIFIER_VALUES:
        if pattern.search(text):
            return ident
    return None


def sensitive_kind(text: str) -> str | None:
    """The kind of secret or public identifier `text` contains, or None. For emitters that cannot
    redact (their output must be usable verbatim) and so refuse the value instead."""
    return secret_value(text) or identifier_kind(text)


def show(value: Any, quote: bool = True) -> str:
    """Render a user-supplied value for an error message, redacting secrets and public identifiers.

    Redacts anything `secret_value` flags, and npubs and 64-hex keys (`IDENTIFIER_VALUES`), and
    escapes C0/DEL/C1 control characters (`\\xNN`), so it is the one helper for human-readable output.

    Every ConfigError that interpolates a config value or key goes through this. `quote=False`
    renders a string bare, for key and path segments.
    """
    if isinstance(value, str):
        kind = secret_value(value)
        if kind:
            return f"<redacted {kind}>"
        ident = identifier_kind(value)
        if ident:
            return f"<redacted {ident}>"
        return repr(value) if quote else _escape_controls(value)
    if isinstance(value, list | tuple | set | frozenset):
        items = cast(list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any], value)
        ordered = sorted(items, key=repr) if isinstance(items, set | frozenset) else list(items)
        return "[" + ", ".join(show(v, quote) for v in ordered) + "]"
    if isinstance(value, Mapping):
        table = cast(Mapping[Any, Any], value)
        return "{" + ", ".join(f"{show(k, quote)}: {show(v, quote)}" for k, v in table.items()) + "}"
    return repr(value)


def is_reference(value: Any) -> bool:
    """A reference is a one-key table, `file` or `command`, whose value is a non-empty string."""
    if not isinstance(value, Mapping):
        return False
    ref = cast(Mapping[str, Any], value)
    if len(ref) != 1:
        return False
    key, target = next(iter(ref.items()))
    return key in REFERENCE_KEYS and isinstance(target, str) and target.strip() != ""


def check(tree: Mapping[str, Any], layer: str) -> None:
    """Raise ConfigError on the first inline secret in `tree`, naming `layer` and the indexed path."""
    _walk(tree, layer, "")


def _reject_value(text: str, layer: str, path: str) -> None:
    kind = secret_value(text)
    if kind:
        raise ConfigError(f"{show(layer, False)}: {path}: value looks like secret material ({kind}); "
                          '{ file = "<path>" } or { command = "<command>" } references only')


def _walk(value: Any, layer: str, path: str) -> None:
    if isinstance(value, str):
        _reject_value(value, layer, path)
    elif isinstance(value, list):
        for i, item in enumerate(cast(list[Any], value)):
            _walk(item, layer, f"{path}[{i}]")
    elif isinstance(value, Mapping):
        for key, child in cast(Mapping[str, Any], value).items():
            dotted = f"{path}.{show(key, False)}" if path else show(key, False)  # keys may be identifiers
            _reject_value(key, layer, f"{path or '<top>'} key")
            if secret_name(key) and not is_reference(child):
                raise ConfigError(f"{show(layer, False)}: {dotted}: secrets must be a reference, "
                                  '{ file = "<path>" } or { command = "<command>" } '
                                  "(exactly one, non-empty)")
            _walk(child, layer, dotted)
