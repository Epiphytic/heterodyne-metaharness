"""One redaction for everything admind posts, summarizes or audits (ADR 0001 §8, revision 13; B1).

Each match is replaced by a marker and the rest of the text is kept: secrets (the patterns of
`config.secret_scan`, with a PEM block hidden to its END line), npubs, and runs of 64 or more hex
digits. Newline and tab are kept (they are layout); every other C0, DEL and C1 character is escaped as
`\\xNN`.

One pass matches secrets and npubs on the text, then escapes controls, then replaces hex runs, so hex
digits an escape adds next to a run are caught in the same pass. A pass can still make a new match: a
marker is a boundary, and a secret pattern that refused to start right after a hex digit starts right
after `>`. So `redact` repeats the pass until the text stops changing, which makes it idempotent by
construction. Each pass that changes the text replaces at least one match by a marker, and markers
match nothing, so this ends quickly; `MAX_PASSES` is a backstop, and text that is still changing after
it becomes `UNSTABLE` whole (fail closed).
"""

import re
from collections.abc import Container, Iterator

from heterodyne.config.secret_scan import IDENTIFIER_VALUES, SECRET_VALUES

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
                  re.DOTALL)
_SECRETS = tuple((kind, pattern) for kind, pattern in SECRET_VALUES if kind != "PEM private key")
_NPUB = dict(IDENTIFIER_VALUES)["npub"]
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")
_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
FRAGMENT = "<redacted fragment>"
UNSTABLE = "<redacted text>"
MAX_PASSES = 10


def _pass(text: str) -> str:
    text = _PEM.sub("<redacted PEM private key>", text)
    for kind, pattern in _SECRETS:
        text = pattern.sub(f"<redacted {kind}>", text)
    text = _NPUB.sub("<redacted npub>", text)
    text = _CONTROLS.sub(lambda m: f"\\x{ord(m.group()):02x}", text)
    return _HEX_RUN.sub("<redacted hex key>", text)


def redact(text: str) -> str:
    for _ in range(MAX_PASSES):
        after = _pass(text)
        if after == text:
            return text
        text = after
    return UNSTABLE


def unique_key(key: str, taken: Container[str]) -> str:
    """A redacted dict key, made distinct from the keys already `taken`: two keys can redact to one marker,
    and neither entry may be lost, so later ones become `key #2`, `key #3`… (Codex r5 finding 1, r6
    finding 3)."""
    out, n = key, 1
    while out in taken:
        n += 1
        out = f"{key} #{n}"
    return out


def _hex_spans(text: str) -> list[tuple[int, int]]:
    """Hex runs as `_pass` sees them (after control escapes), as spans of the unescaped `text`: an escape's
    own digits belong to the character it replaces."""
    escaped: list[str] = []
    owner: list[int] = []
    for i, c in enumerate(text):
        piece = f"\\x{ord(c):02x}" if _CONTROLS.fullmatch(c) else c
        escaped.append(piece)
        owner.extend([i] * len(piece))
    return [(owner[m.start()], owner[m.end() - 1] + 1) for m in _HEX_RUN.finditer("".join(escaped))]


def _spans(text: str) -> Iterator[tuple[int, int]]:
    """Every span `redact` would hide, in offsets of `text`. Like `redact`, it follows `_pass` (secrets and
    npubs on the raw text, then hex runs on the escaped text) and repeats: found spans are masked with `<`
    (a boundary, as a marker is) at the same length, and the text is scanned again."""
    patterns = (_PEM, *(p for _, p in _SECRETS), _NPUB)
    for _ in range(MAX_PASSES):
        found = [(m.start(), m.end()) for p in patterns for m in p.finditer(text)]
        masked = text
        for start, end in found:
            masked = masked[:start] + "<" * (end - start) + masked[end:]
        found += _hex_spans(masked)
        if not found:
            return
        yield from found
        for start, end in found:
            text = text[:start] + "<" * (end - start) + text[end:]
    yield 0, len(text)          # still finding after the backstop: treat it all as one value


def redact_continuation(previous: str, text: str) -> str:
    """`text` continues `previous`, which was already sent (B17). A value that straddles the boundary is
    hidden in `text` up to its end (its start went out already; nothing can undo that); then `text` is
    redacted as usual."""
    edge = len(previous)
    cut = max((end - edge for start, end in _spans(previous + text) if start < edge < end), default=0)
    if cut:
        text = FRAGMENT + text[cut:]
    return redact(text)
