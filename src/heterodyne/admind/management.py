"""Closed operation-consent presentation, never management or approve-bead authority.

Only new question-kind requests opt in. Legacy cards never pass through this renderer.
All persisted metadata participates in reproduction and the binding-v2 input digest.
"""

import re
import unicodedata
from typing import Literal

import msgspec

from heterodyne.admind import chunk, links
from heterodyne.admind.redact import redact
from heterodyne.admind.store import AskRow

TAG = "task-management-consent-v1"
VERSION = 2
CHOICES = ["Perform this exact change", "Leave unchanged and held"]
MAX_LINES = 36
MAX_CHARS = 3200
_HEX = re.compile(r"[0-9a-f]{64}")
_AUDIT = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/blob/[0-9a-f]{40}/[^?#]+")
_MARKDOWN = re.compile(r"[`*_~|\\\[\]<>]|(?m:^\s*[#!>])")


class Metadata(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    request_id: str
    descriptor_sha256: str
    audit_url: str


class Refusal(chunk.TooWide):
    """Also handled by existing repeat/details refusal paths; no partial card is queued."""

    def __init__(self) -> None:
        super().__init__(0, 0)


def literal(text: str, *, multiline: bool = False) -> bool:
    """Plain literals only; no format/control/bidi instructions or lossy sanitization."""
    return (
        not _MARKDOWN.search(text)
        and all(
            (c == "\n" and multiline) or unicodedata.category(c) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for c in text
        )
        and redact(text) == text
    )


def valid_metadata(value: Metadata) -> bool:
    return (
        bool(_HEX.fullmatch(value.request_id))
        and bool(_HEX.fullmatch(value.descriptor_sha256))
        and "%" not in value.audit_url
        and len(value.audit_url) <= links.MAX_URL
        and bool(_AUDIT.fullmatch(value.audit_url))
        and links.why(links.LinkSpec("Audit details", value.audit_url)) is None
    )


def stored(row: AskRow) -> Metadata | None:
    """NULL means legacy only when ALL new columns are NULL. Unknown/partial pairs refuse."""
    if row.presentation is None and row.render_version is None and row.management is None:
        return None
    if row.presentation != TAG or type(row.render_version) is not int or row.render_version != VERSION:
        raise Refusal()
    if row.management is None or len(row.management) > 2048:
        raise Refusal()
    try:
        data = msgspec.json.decode(row.management, type=Metadata)
    except msgspec.DecodeError:
        raise Refusal() from None
    if (
        not valid_metadata(data)
        or row.kind != "question"
        or row.question_form != "choices"
        or row.pr_url is not None
        or row.head_sha is not None
        or row.bead is not None
        or row.digest is not None
        or row.refreshed_from is not None
        or row.links is not None
    ):
        raise Refusal()
    # Strict closed choice decoding; a copied tag cannot transform arbitrary/native approval cards.
    try:
        form = msgspec.json.decode(row.choices or "", type=_Choices)
    except msgspec.DecodeError:
        raise Refusal() from None
    if form.choices != CHOICES or form.recommended != "A":
        raise Refusal()
    return data


class _Choices(msgspec.Struct, forbid_unknown_fields=True):
    choices: list[str]
    recommended: Literal["A"]


def lines(row: AskRow) -> list[str]:
    meta = stored(row)
    if meta is None or not literal(row.title) or not literal(row.body, multiline=True):
        raise Refusal()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", row.poster):
        raise Refusal()
    result = [
        "APPROVAL: Task management",
        f"Ask {row.ask_id} · operation consent · posted by {row.poster} (a local process; unverified)",
        row.title,
        *row.body.split("\n"),
        f"Audit details: {meta.audit_url}",
        f"Request: {meta.request_id[:12]}; facts: {meta.descriptor_sha256[:12]}",
        "A = Perform this exact change (recommended)",
        "B = Leave unchanged and held",
        "A / approved / 👍 ✅ ❤ ♥ = approve; B / deny / 👎 ❌ = leave held.",
        "Any other reply or contrary answer holds this request; a later A cannot override it.",
        f"Reply A or B, or !answer {row.ask_id} A|B.",
        "Only a verified management binding authorizes this change.",
    ]
    text = "\n".join(result)
    if len(result) > MAX_LINES or len(text) > MAX_CHARS or redact(text) != text:
        raise Refusal()
    return result


def parts(row: AskRow, chunk_chars: int, *, details: bool = False) -> list[str]:
    text = lines(row)
    # Treat every fact as atomic: splitting a material literal is never a substitute for refusal.
    tagged: list[tuple[str, chunk.Kind]] = [(line, "atomic") for line in text]
    packed = chunk.pack(tagged, chunk_chars)
    complete = "\n".join(packed)
    # Include actual continuation framing, not merely the unchunked card budget.
    if (
        len(complete) > MAX_CHARS
        or len(complete.splitlines()) > MAX_LINES
        or redact(complete) != complete
        or any(redact(p) != p for p in packed)
    ):
        raise Refusal()
    if details:
        grammar = [
            "Accepted approval words: approve, approved, yes, y, ok, okay, lgtm.",
            "Denial words: deny, denied, reject, rejected (optional reason); no or n alone.",
            "Positive reactions: 👍 (all skin tones), ✅, ❤, ♥; negative: 👎 (all skin tones), ❌.",
            "Existing reply punctuation and emoji variation-selector handling are unchanged.",
        ]
        return chunk.pack([*tagged, *((line, "atomic") for line in grammar)], chunk_chars)
    return packed
