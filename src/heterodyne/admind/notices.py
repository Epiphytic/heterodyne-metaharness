"""FYI notices: one-way news for the operators, never an ask (cxmkf K1-K10). Pure.

A notice is a title, an optional body and links. It requests nothing, so it has no ask ID, status, answer
footer or `!details`, and nothing tracks it after delivery. It is bounded per notice (K4) and in rate and
queue (K5); the daemon does the counting and the I/O. Refusals carry a fixed K9 code, never text.
"""

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass

import msgspec

from heterodyne.admind import chunk, links
from heterodyne.admind.links import LinkSpec
from heterodyne.admind.redact import redact

NOTICE_MAX_CHARS, NOTICE_MAX_PARTS = 6_000, 4
NOTICE_PER_HOUR, NOTICE_UNDELIVERED = 12, 10
NOTICE_RETENTION_DAYS = 7
MAX_TITLE, MAX_BODY_CHARS, MAX_BODY_LINES = 200, 3_500, 40       # R4's title; the card budget (CARD_*)
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"     # the ask alphabet (R3)
ID_LENGTH = 6           # never mistaken for an ask ID (4)
LABEL = "FYI: no reply needed"
NOTICE_KEY = re.compile(r"\Anotice:([2-9a-hjkmnp-z]{6}):(\d+)\Z")
_TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
WHY = ("latched", "title", "body-too-long", "notice-too-long", "too-many-parts", "too-many-links",
       "link-label", "link-format", "link-too-long", "link-line-too-long", "link-redacted", "rate-hour",
       "undelivered", "internal")
MESSAGES = {
    "latched": "admind is latched and posts nothing; the notice was not posted.",
    "title": f"--title must be one line of 1-{MAX_TITLE} characters",
    "body-too-long": f"the body is over {MAX_BODY_CHARS:,} characters or {MAX_BODY_LINES} lines",
    "notice-too-long": f"the notice is over {NOTICE_MAX_CHARS:,} characters",
    "too-many-parts": f"the notice needs more than {NOTICE_MAX_PARTS} messages at this group's message size",
    "link-line-too-long": "a link line is longer than this group's message size allows; shorten it",
    "rate-hour": f"{NOTICE_PER_HOUR} notices were posted in the last hour; try again later.",
    "undelivered": f"{NOTICE_UNDELIVERED} notices are still waiting to be delivered; try again later.",
    **links.MESSAGES,
}


class NoticePost(msgspec.Struct, frozen=True, tag="notice", forbid_unknown_fields=True):
    title: str
    body: str = ""
    links: list[LinkSpec] = []
    poster: str = "local"


@dataclass(frozen=True)
class Admitted:
    chunks: list[str]
    text: str               # the whole notice, redacted


def head(poster: str) -> str:
    return f"ℹ️ Notice · posted by {poster} (a local process; unverified)"


def lines(req: NoticePost) -> tuple[list[str], set[str]]:
    """The notice's lines before redaction, and the lines that are atomic (the label, the head, links)."""
    framing = [LABEL, head(req.poster)]
    shown = [req.title, *(["", *req.body.rstrip("\n").split("\n")] if req.body.strip() else [])]
    link_lines = links.block(req.links)
    return [*framing, *shown, *link_lines], {*framing, *map(links.line, req.links)}


def admit(req: NoticePost, chunk_chars: int) -> Admitted | str:
    """The notice's chunks, or the K9 code it is refused for. The whole text is redacted before it is
    measured and split (K8); the label, the head and each link line are never cut (K23)."""
    if not 1 <= len(req.title) <= MAX_TITLE or not req.title.strip() or _TITLE_CONTROLS.search(req.title):
        return "title"
    if len(req.body) > MAX_BODY_CHARS or req.body.rstrip("\n").count("\n") + 1 > MAX_BODY_LINES:
        return "body-too-long"
    refusal = links.check(req.links)
    if refusal is not None:
        return refusal
    raw, atomic = lines(req)
    text = redact("\n".join(raw))
    if len(text) > NOTICE_MAX_CHARS:
        return "notice-too-long"
    tagged: list[tuple[str, chunk.Kind]] = [(t, "atomic" if t in atomic else "body")
                                            for t in text.split("\n")]
    try:
        parts = chunk.pack(tagged, chunk_chars)
    except chunk.TooWide:
        return "link-line-too-long"
    if len(parts) > NOTICE_MAX_PARTS:
        return "too-many-parts"
    return Admitted(parts, text)


def new_id(taken: Callable[[str], bool]) -> str:
    """A random 6-character notice ID not `taken` (about 887 million values)."""
    for _ in range(1000):
        notice_id = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
        if not taken(notice_id):
            return notice_id
    raise RuntimeError("no free notice ID")


def describe(req: NoticePost) -> dict[str, object]:
    """The lengths of a notice request for the audit (K9): never its text."""
    return {"poster": req.poster, "title_chars": len(req.title), "body_chars": len(req.body),
            "links": len(req.links)}
