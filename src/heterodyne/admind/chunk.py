"""Split a reply into message-sized chunks whose concatenation is exactly the original (ADR §8)."""


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
