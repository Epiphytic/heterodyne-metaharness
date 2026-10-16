"""Whole-field checks and narrowly typed spans carried through final layout."""

import re
import unicodedata
from dataclasses import dataclass

from heterodyne.config.secret_scan import IDENTIFIER_VALUES, SECRET_VALUES

from .dto import RenderPolicy

_EXTRA = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
        r"did:\S+",
        r"(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
        r"[0-9a-f]{40,}",
        r"(?<!\S)/(?!workstreams(?=$|[\s,;:!?\)\]])|status(?=$|[\s,;:!?\)\]])|"
        r"approvals(?=$|[\s,;:!?\)\]])|queue(?=$|[\s,;:!?\)\]])|approve(?=$|[\s,;:!?\)\]])|"
        r"deny(?=$|[\s,;:!?\)\]])|pause(?=$|[\s,;:!?\)\]])|resume(?=$|[\s,;:!?\)\]])|"
        r"stop(?=$|[\s,;:!?\)\]])|harness(?=$|[\s,;:!?\)\]])|trust-group(?=$|[\s,;:!?\)\]]))\S*",
        r"[a-z]:\\|\\\\",
        r"nostr:|nprofile1|npub1",
    )
)
_LINK = re.compile(r"[a-z][a-z0-9+.-]{0,31}://|www\.|mailto:|(?<!\S)//|\]\(|\]:|[<>`]", re.IGNORECASE)
_RESERVED = re.compile(r"↳ part |👍 approve · 👎 deny · ❤️ always · reply to steer")


@dataclass(frozen=True)
class Span:
    text: str
    public: tuple[tuple[int, int], ...] = ()
    syntax: tuple[tuple[int, int], ...] = ()
    template: tuple[tuple[int, int], ...] = ()
    atomic: bool = False
    url: bool = False
    unbreakable: tuple[tuple[int, int], ...] = ()

    def __add__(self, other: "Span") -> "Span":
        offset = len(self.text)
        return Span(
            self.text + other.text,
            self.public + tuple((a + offset, b + offset) for a, b in other.public),
            self.syntax + tuple((a + offset, b + offset) for a, b in other.syntax),
            self.template + tuple((a + offset, b + offset) for a, b in other.template),
            unbreakable=self._atoms() + tuple((a + offset, b + offset) for a, b in other._atoms()),
        )

    def _atoms(self) -> tuple[tuple[int, int], ...]:
        return ((0, len(self.text)),) if self.atomic else self.unbreakable

    def slice(self, start: int, end: int) -> "Span":
        def keep(ranges: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
            return tuple(
                (max(a, start) - start, min(b, end) - start) for a, b in ranges if a < end and b > start
            )

        return Span(
            self.text[start:end],
            keep(self.public),
            keep(self.syntax),
            keep(self.template),
            self.atomic,
            self.url,
            keep(self.unbreakable),
        )


def generated(
    text: str, *, public: bool = False, syntax: bool = False, atomic: bool = False, url: bool = False
) -> Span:
    region = ((0, len(text)),)
    return Span(text, region if public else (), region if syntax else (), region, atomic, url)


def join(parts: tuple[Span, ...] | list[Span], separator: str = "\n") -> Span:
    out = Span("")
    for index, part in enumerate(parts):
        if index:
            out += generated(separator)
        out += part
    return out


def _covered(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(a <= start and end <= b for a, b in ranges)


def safe(span: Span, policy: RenderPolicy, *, multiline: bool = False, final: bool = False) -> bool:
    text = span.text
    for index, char in enumerate(text):
        if char == "\n" and (multiline or final):
            continue
        if unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") or char in (
            "\u2800",
            "\u3164",
            "\uffa0",
        ):
            if not (final and _covered(index, index + 1, span.template)):
                return False
    if any(pattern.search(text) for _, pattern in SECRET_VALUES):
        return False
    if any(form in text for form in policy.login_dir_forms):
        return False
    for pattern in (*[pattern for _, pattern in IDENTIFIER_VALUES], *_EXTRA):
        for match in pattern.finditer(text):
            if not _covered(*match.span(), span.public):
                return False
    for pattern, exceptions in ((_LINK, span.syntax), (_RESERVED, span.template)):
        for match in pattern.finditer(text):
            if not _covered(*match.span(), exceptions):
                return False
    return True


def literal(
    text: str, policy: RenderPolicy, *, multiline: bool = False, effect: bool = False, atomic: bool = False
) -> Span | None:
    syntax = tuple((m.start(), m.end()) for m in re.finditer(r"[<>`]", text)) if effect else ()
    span = Span(text, syntax=syntax, atomic=atomic)
    return span if safe(span, policy, multiline=multiline) else None
