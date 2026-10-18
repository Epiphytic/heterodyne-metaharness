"""Deterministic bounded physical-line layout; overflow returns no partial pages."""

from collections.abc import Callable

from . import dto as d
from .safety import Span, generated, join


class TooWide(ValueError):
    pass


class UnsafeContinuation(ValueError):
    """Wrapping would turn content into protected document framing."""


def wrap(span: Span, *, forbidden_continuation: Callable[[str], bool] | None = None) -> tuple[Span, ...]:
    if span.url:
        if len(span.text) > 512 or "\n" in span.text:
            raise TooWide
        return (span,)
    out: list[Span] = []
    offset = 0
    for text in span.text.split("\n"):
        line = span.slice(offset, offset + len(text))
        offset += len(text) + 1
        if span.atomic:
            if len(text) > 120:
                raise TooWide
            out.append(line)
            continue
        while len(line.text) > 120:
            cut = line.text.rfind(" ", 0, 121)
            cut = cut if cut > 0 else 120
            # Typed exceptions and atomic links must never be cut into smaller grants.
            if any(a < cut < b for ranges in (line.public, line.syntax, line.unbreakable) for a, b in ranges):
                starts = [a for a, b in line.unbreakable if a < cut < b]
                if not starts or min(starts) == 0:
                    raise TooWide
                cut = min(starts)
                if line.text[cut - 1 : cut] == " ":
                    cut -= 1
            out.append(line.slice(0, cut))
            advance = cut + (line.text[cut : cut + 1] == " ")
            line = line.slice(advance, len(line.text))
            if forbidden_continuation is not None and forbidden_continuation(line.text):
                raise UnsafeContinuation
        out.append(line)
    return tuple(out)


def paginate(
    header: tuple[Span, ...], data: tuple[Span, ...], footer: tuple[Span, ...], max_chars: int
) -> tuple[Span, ...] | d.NeedsDetails:
    try:
        head = tuple(line for span in header for line in wrap(span))
        body = tuple(line for span in data for line in wrap(span))
        tail = tuple(line for span in footer for line in wrap(span))
    except TooWide:
        return d.NeedsDetails("content-too-wide")
    if len(head) + len(body) + len(tail) <= 8:
        single = join((*head, *body, *tail))
        if len(single.text) <= max_chars:
            return (single,)
    for digits in (1, 2):
        pages: list[tuple[Span, ...]] = []
        current: list[Span] = []
        # Width is conservatively final n width, then exact actual markers are emitted.
        marker = generated("↳ part " + "9" * digits + "/" + "9" * digits)
        for line in body:
            trial = (*head, *current, line, *tail, marker)
            if len(trial) <= 8 and len(join(trial).text) <= max_chars:
                current.append(line)
                continue
            if not current:
                return d.NeedsDetails("content-too-wide")
            pages.append(tuple(current))
            if len(pages) >= 20:
                return d.NeedsDetails("excessive-parts")
            current = [line]
            if (
                len((*head, line, *tail, marker)) > 8
                or len(join((*head, line, *tail, marker)).text) > max_chars
            ):
                return d.NeedsDetails("content-too-wide")
        if current or not pages:
            pages.append(tuple(current))
        if len(pages) > 20:
            return d.NeedsDetails("excessive-parts")
        if digits == 1 and len(pages) >= 10:
            continue
        count = len(pages)
        return tuple(
            join((*head, *page, *tail, generated(f"↳ part {index + 1}/{count}")))
            for index, page in enumerate(pages)
        )
    return d.NeedsDetails("excessive-parts")
