"""Closed operation-consent presentation, never management or approve-bead authority.

Only new question-kind requests opt in. Legacy cards never pass through this renderer.
All persisted metadata participates in reproduction and the binding-v2 input digest.
"""

import re
from typing import Literal

import msgspec

from heterodyne.admind import chunk, links, verbs
from heterodyne.admind.redact import redact
from heterodyne.admind.store import AskRow

TAG = "task-management-consent-v1"
VERSION = 2
CHOICES = ["Perform this exact change", "Leave unchanged and held"]
MAX_LINES = 36
MAX_CHARS = 3200
# Renderer-owned lines may not be injected as purported task facts.
_MARKDOWN = re.compile(r"[`*_~|&\\\[\]<>]|(?m:^\s*[#!>]|^ {4}|^\s*[-=]{3,}\s*$)")
_RESERVED = re.compile(
    r"(?im:^\s*(?:APPROVAL\s*:|QUESTION\s*:|Audit\s+details\s*:|Request\s*:|"
    r"(?:Option\s+)?[A-Z]\s*(?:=|/)|Ask\s|Reply\s|Only a verified management binding|"
    r"deny\s*/|Any other reply or contrary answer|Accepted approval words:|"
    r"Denial words:|Positive reactions:|Queue verifies complete history;))"
)
_ANSWER_LEGEND = re.compile(
    r"(?im:^\s*(?:"
    + "|".join(sorted(verbs.APPROVE_WORDS | verbs.DENY_WORDS | verbs.DENY_ALONE))
    + r")\b[^\n]*(?:=|/|:))"
)
_URL = re.compile(r"(?i:(?:[a-z][a-z0-9+.-]*://|www\.|(?:https?|ftp|mailto|tel|data|javascript):))")
REFUSED = "task-management-consent-v1 management card refused: unsafe, invalid or over budget"


class Metadata(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    request_id: str
    descriptor_sha256: str
    audit_url: str


class Refusal(ValueError):
    """A management contract refusal, never a legacy link-width diagnostic."""

    def __init__(self) -> None:
        super().__init__("management-contract")


def present(row: AskRow) -> bool:
    """Select V2 validation without decoding twice; partial/unknown metadata still selects refusal."""
    return any(v is not None for v in (row.presentation, row.render_version, row.management))


def literal(text: str, *, multiline: bool = False) -> bool:
    """Printable ASCII facts only; refuse unsupported text unchanged, never sanitize."""
    return (
        not _MARKDOWN.search(text)
        and not _RESERVED.search(text)
        and not _ANSWER_LEGEND.search(text)
        and not _URL.search(text)
        and all((c == "\n" and multiline) or " " <= c <= "~" for c in text)
        and redact(text) == text
    )


def valid_metadata(value: Metadata) -> bool:
    from heterodyne.admind.binding import HEX_KEY  # deferred: binding owns the wire identity grammar

    return (
        bool(HEX_KEY.fullmatch(value.request_id))
        and bool(HEX_KEY.fullmatch(value.descriptor_sha256))
        and "%" not in value.audit_url
        and len(value.audit_url) <= links.MAX_URL
        and bool(links.PINNED_BLOB.fullmatch(value.audit_url))
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
    from heterodyne.admind.asks import POSTER  # deferred: native request validation owns poster syntax

    if not POSTER.fullmatch(row.poster):
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
        "A / approve / approved / yes / y / ok / okay / lgtm = approve; B = leave held.",
        "Positive reactions: 👍 (all skin tones), ✅, ❤, ♥ = approve.",
        "deny / 👎 ❌ = none of these; request stays held.",
        "Denial words: deny, denied, reject, rejected (optional reason); no or n alone = none of these.",
        "Queue verifies complete history; any contrary or ambiguous answer holds this request.",
        f"Reply A or B, or !answer {row.ask_id} A|B.",
        "Only a verified management binding authorizes this change.",
    ]
    text = "\n".join(result)
    if len(result) > MAX_LINES or len(text) > MAX_CHARS or redact(text) != text:
        raise Refusal()
    return result


def parts(row: AskRow, chunk_chars: int, *, details: bool = False) -> list[str]:
    text = lines(row)
    if details:
        text += [
            "Accepted approval words: approve, approved, yes, y, ok, okay, lgtm.",
            "Denial words: deny, denied, reject, rejected (optional reason); no or n alone.",
            "Positive reactions: 👍 (all skin tones), ✅, ❤, ♥; negative: 👎 (all skin tones), ❌.",
            "Existing reply punctuation and emoji variation-selector handling are unchanged.",
        ]
    # Treat every fact as atomic: splitting a material literal is never a substitute for refusal.
    tagged: list[tuple[str, chunk.Kind]] = [(line, "atomic") for line in text]
    try:
        packed = chunk.pack(tagged, chunk_chars)
    except chunk.TooWide:
        raise Refusal() from None
    complete = "\n".join(packed)
    # Include actual continuation framing, not merely the unchunked card budget.
    if (
        len(complete) > MAX_CHARS
        or len(complete.splitlines()) > MAX_LINES
        or redact(complete) != complete
        or any(redact(p) != p for p in packed)
    ):
        raise Refusal()
    return packed
