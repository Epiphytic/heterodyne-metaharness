"""What an operator's reply or reaction on an approval card says (relay delta R27, R28). Pure.

Only an unambiguous reply decides. An approve is a reply that is exactly one approve word or one approve
emoji; a deny is a reply whose first word is a deny word or a deny emoji (the rest is its reason), or that
is exactly "no" or "n". A deny word may be followed by one `:`, `,`, `-` or `—` ("rejected: too broad"); an
approve word may not. Everything else is a note. The words and emojis are listed here and nowhere else.
"""

import re
from dataclasses import dataclass
from typing import Literal

Action = Literal["approve", "deny"]

APPROVE_WORDS = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "lgtm"})
DENY_WORDS = frozenset({"deny", "denied", "reject", "rejected"})
DENY_ALONE = frozenset({"no", "n"})      # a deny only as the whole reply: "no idea" is a note
SKIN_TONES = ("", "\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff")
# 👍 (any skin tone) ✅ ❤ ♥, and 👎 (any skin tone) ❌. Without U+FE0F, which only asks for emoji
# presentation and is ignored (see `emoji_action`).
APPROVE_EMOJIS = frozenset({*("\U0001f44d" + t for t in SKIN_TONES), "\u2705", "\u2764", "\u2665"})
DENY_EMOJIS = frozenset({*("\U0001f44e" + t for t in SKIN_TONES), "\u274c"})
VARIATION_SELECTOR = "\ufe0f"
_TRAILING = re.compile(r"[.!\s]+\Z")
_DENY_SEPARATOR = re.compile(r"[:,\-\u2014]\Z")     # one `:` `,` `-` `—` after a deny word (operator call)


@dataclass(frozen=True)
class Reading:
    action: Action
    reason: str = ""        # a deny's reason as typed (trimmed), possibly empty; the caller redacts it


def emoji_action(emoji: str) -> Action | None:
    """What one emoji (a reaction, or a whole reply) decides, if anything."""
    base = emoji.removesuffix(VARIATION_SELECTOR)
    if base in APPROVE_EMOJIS:
        return "approve"
    if base in DENY_EMOJIS:
        return "deny"
    return None


def fold(text: str) -> str:
    """Trimmed, Unicode-casefolded, and stripped of trailing `.`, `!` and whitespace (R28)."""
    return _TRAILING.sub("", text.strip().casefold())


def read_reply(text: str) -> Reading | None:
    """An approve, a deny with its reason, or None for a note."""
    whole = fold(text)
    if whole in APPROVE_WORDS or emoji_action(whole) == "approve":
        return Reading("approve")
    if whole in DENY_ALONE:
        return Reading("deny")
    words = text.strip().split(maxsplit=1)
    if not words:
        return None
    first = _DENY_SEPARATOR.sub("", fold(words[0]))
    if first in DENY_WORDS or emoji_action(first) == "deny":
        return Reading("deny", words[1].strip() if len(words) == 2 else "")
    return None
