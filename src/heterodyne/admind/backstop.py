"""The backstop batch: deterministic, drops nothing `!details` can't return (ADR 0001 §8, revision 13)."""

from dataclasses import dataclass

BATCH_SECONDS = 60.0
WHOLE_MAX = 50
HEAD = 10
TAIL = 40
TITLE = "⚠️ Replies batched (summaries unavailable) · reply `!details` for everything"
WORDS = 8
WORDS_CHARS = 60
CUT = "\n… (cut at the message size limit)"
BATCH_MAX_CHARS = 60000  # the largest message Task 1 confirmed is delivered whole (chunk_chars's maximum)


@dataclass(frozen=True)
class Entry:
    origin: str
    text: str


def first_words(text: str) -> str:
    words = text.split()
    shown = " ".join(words[:WORDS])
    if len(words) > WORDS or len(shown) > WORDS_CHARS:
        shown = shown[:WORDS_CHARS].rstrip() + "…"
    return shown


def origin(operator: str | None, at_iso: str, words: str | None) -> str:
    who = operator or "terminal"
    said = f"“{words}”" if words else "(no operator message)"
    return f"— {who} · {at_iso[11:16]} UTC · {said}"


def collapse(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        k = j - i + 1
        out.append(lines[i] if k == 1 else f"{lines[i]} (×{k})")
        i = j + 1
    return out


def render(entries: list[Entry]) -> str:
    lines: list[str] = []
    for e in entries:
        lines.append(e.origin)
        lines.extend(e.text.splitlines() or [""])
    lines = collapse(lines)
    if len(lines) > WHOLE_MAX:
        lines = lines[:HEAD] + [f"… {len(lines) - HEAD - TAIL} lines skipped …"] + lines[-TAIL:]
    return "\n".join([TITLE, *lines])


def fit(text: str, limit: int) -> str:
    """Keep a rendered batch to one message of at most `limit` characters (B10). The caller passes
    `BATCH_MAX_CHARS`, the transport's limit, so a batch the ADR's line rules produce is sent exactly as
    rendered unless it can't be delivered as one message at all (over 60,000 characters: 50 lines of over
    1,000 characters each). Only then is each line after the title cut to an equal share, with a count of
    what was cut, and if that is still too long, the end. `!details` on the batch returns every reply
    whole."""
    if len(text) <= limit:
        return text
    title, *lines = text.split("\n")
    share = max(20, limit // max(1, len(lines)) - 16)
    lines = [line if len(line) <= share else f"{line[:share]}…(+{len(line) - share} chars)" for line in lines]
    text = "\n".join([title, *lines])
    if len(text) <= limit:
        return text
    return text[: limit - len(CUT)] + CUT
