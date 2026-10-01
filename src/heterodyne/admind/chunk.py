"""Split a reply into message-sized chunks whose concatenation is exactly the original (ADR §8)."""


def split(text: str, limit: int) -> list[str]:
    """Chunks of at most `limit` characters, cut after a newline when one falls in the second half."""
    if limit < 1:
        raise ValueError("limit must be positive")
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        newline = rest.rfind("\n", 0, limit)
        cut = newline + 1 if newline >= limit // 2 else limit
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out
