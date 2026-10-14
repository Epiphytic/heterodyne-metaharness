"""Split a reply into message-sized chunks whose concatenation is exactly the original (ADR §8), and pack a
card's lines into marked, threaded parts that never cut a link, a choice or the footer (cxmkf K23)."""

from typing import Literal


def split(text: str, limit: int) -> list[str]:
    """Chunks of at most `limit` characters, cut after a newline when one falls in the second half. Linear
    in the text: it walks an offset and never copies the remainder (a 16 MiB `!details` took 12 s when
    each chunk copied the rest)."""
    if limit < 1:
        raise ValueError("limit must be positive")
    out: list[str] = []
    pos, size = 0, len(text)
    while size - pos > limit:
        newline = text.rfind("\n", pos, pos + limit)
        cut = newline + 1 if newline >= 0 and newline - pos >= limit // 2 else pos + limit
        out.append(text[pos:cut])
        pos = cut
    if pos < size:
        out.append(text[pos:])
    return out


# --- cards (cxmkf K23) ---------------------------------------------------------------------------
Kind = Literal["body", "atomic", "closing"]
MARKER_ROOM = 16        # room kept in every chunk for `↳ part <i>/<n>` and its newline (n ≤ 999)
MAX_PARTS = 999


class TooWide(ValueError):
    """An atomic line is longer than a chunk's usable width; `length` is its length in characters."""

    def __init__(self, length: int, usable: int) -> None:
        super().__init__(f"an atomic line of {length} characters, {usable} usable")
        self.length = length
        self.usable = usable


def usable(chunk_chars: int) -> int:
    return chunk_chars - MARKER_ROOM


def marker(i: int, n: int) -> str:
    """The first line of continuation chunk `i` (1-based) of `n`."""
    return f"↳ part {i}/{n}\n"


def pack(lines: list[tuple[str, Kind]], chunk_chars: int) -> list[str]:
    """A card's lines as chunks of at most `chunk_chars` characters, markers included (K23). Each chunk is
    packed to `usable(chunk_chars)` characters, then every chunk after the first gets its marker. A body line
    that fits in a chunk is never cut: it starts a new chunk instead; a longer one is cut as `split` cuts
    (a partly filled chunk is closed first when it is at least half full, else filled). An atomic line is
    never cut (TooWide if it can't fit). The closing lines, which must come last, stay together in the
    last chunk when they fit in one; otherwise they are packed line by line, so the last of them is always
    the final line of the final chunk."""
    room = usable(chunk_chars)
    if room < 1:
        raise ValueError("chunk_chars leaves no room")
    chunks: list[str] = []
    cur: list[str] = []
    size = 0

    def free() -> int:
        return room - size - (1 if cur else 0)

    def add(text: str) -> None:
        nonlocal size
        size += len(text) + (1 if cur else 0)
        cur.append(text)

    def flush() -> None:
        nonlocal size
        if cur:
            chunks.append("\n".join(cur))
            cur.clear()
            size = 0

    def whole(text: str) -> None:
        if len(text) > room:
            raise TooWide(len(text), room)
        if len(text) > free():
            flush()
        add(text)

    closing = [text for text, kind in lines if kind == "closing"]
    if closing and any(kind != "closing" for _, kind in lines[len(lines) - len(closing):]):
        raise ValueError("closing lines must come last")
    for text, kind in lines[:len(lines) - len(closing)]:
        if kind == "atomic":
            whole(text)
            continue
        if len(text) <= free():
            add(text)
            continue
        if len(text) <= room or size >= room // 2:
            flush()
        while len(text) > free():
            take = free()
            if take < 1:
                flush()
                continue
            add(text[:take])
            flush()
            text = text[take:]
        add(text)
    if closing:
        block = "\n".join(closing)
        if len(block) <= room:
            whole(block)
        else:
            for text in closing:
                whole(text)
    flush()
    if len(chunks) > MAX_PARTS:
        raise ValueError("too many parts")
    n = len(chunks)
    return [c if i == 0 else marker(i + 1, n) + c for i, c in enumerate(chunks)] if n > 1 else chunks
