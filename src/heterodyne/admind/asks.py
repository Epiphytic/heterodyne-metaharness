"""Asks: what a local process posts to the operators, and how it is checked and shown (relay spec §2, §6).

Pure: request and reply structs for `ask.sock`, validation (R4, R16, R19), ask IDs (R3) and the rendering of
cards, `!details` and `!asks` lines. Question and merge cards are redacted whole first, and their budget is
counted on the redacted text (P5). The daemon does the I/O.
"""

import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

import msgspec

from heterodyne.admind.redact import redact
from heterodyne.admind.store import ASK_ACTIVE, AnswerRow, AskRow

ASK_SOCKET = "ask.sock"
MAX_REQUEST, MAX_REPLY = 262_144, 1_048_576
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
ID_LENGTH = 4
MAX_OPEN, MAX_PER_HOUR, MAX_IN_FLIGHT = 20, 30, 2
MAX_ANSWER, MAX_ANSWERS, MAX_ANSWER_TOTAL = 16_000, 50, 64_000
CARD_LINES, CARD_CHARS = 40, 3_500
MAX_TITLE, MIN_BODY_CHARS, MAX_BODY = 200, 80, 16_000
ACTIVE = ASK_ACTIVE      # counted by MAX_OPEN; blocked is terminal (r2-4)
TERMINAL = ("approved", "denied", "stale", "superseded", "cancelled", "blocked")
INTERNAL_ERROR = "admind hit an internal error; see the audit log."
AskKind = Literal["question", "merge", "approval"]

PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}/pull/[1-9][0-9]{0,9}")
HEAD_SHA = re.compile(r"[0-9a-f]{40}")
POSTER = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
BEAD = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
_TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


class AskPost(msgspec.Struct, frozen=True, tag="post", forbid_unknown_fields=True):
    kind: AskKind
    title: str = ""
    body: str = ""
    pr_url: str | None = None
    head_sha: str | None = None
    bead: str | None = None
    poster: str = "local"


class AskGet(msgspec.Struct, frozen=True, tag="get", forbid_unknown_fields=True):
    ask_id: str


class AskList(msgspec.Struct, frozen=True, tag="list", forbid_unknown_fields=True):
    pass


class AskCancel(msgspec.Struct, frozen=True, tag="cancel", forbid_unknown_fields=True):
    ask_id: str


AskRequest = AskPost | AskGet | AskList | AskCancel


class AnswerView(msgspec.Struct, frozen=True):
    kind: str
    operator: str
    text: str
    at: str


class AskSummary(msgspec.Struct, frozen=True):
    ask_id: str
    kind: str
    status: str
    title: str
    bead: str | None
    digest12: str | None
    delivered: bool
    answer_count: int
    outcome: str | None
    decided_by: str | None


class AskView(msgspec.Struct, frozen=True):
    summary: AskSummary
    answers: list[AnswerView]


class AskReply(msgspec.Struct, frozen=True):
    result: Literal["posted", "ok", "refused", "failed"]
    message: str
    ask: AskView | None = None
    asks: list[AskSummary] | None = None


def now() -> datetime:
    """The clock for `created_at` and the hourly limit (patched in tests)."""
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


def hour_ago() -> str:
    return stamp(now() - timedelta(hours=1))


def normalise_id(text: str) -> str | None:
    """An ask ID as typed (any case), or None if it can't be one (R3)."""
    ask_id = text.lower()
    if len(ask_id) != ID_LENGTH or any(c not in ID_ALPHABET for c in ask_id):
        return None
    return ask_id


def new_id(taken: Callable[[str], bool]) -> str:
    """A random ID not `taken`. About 920,000 values, so a retry is rare and a run of 1000 means a bug."""
    for _ in range(1000):
        ask_id = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
        if not taken(ask_id):
            return ask_id
    raise RuntimeError("no free ask ID")


def check(req: AskRequest) -> str | None:
    """Why a request is refused before it is logged or handled (R4, R16, R19); None if it may go on."""
    if isinstance(req, AskGet | AskCancel):
        return None if normalise_id(req.ask_id) is not None else (
            f"an ask ID is {ID_LENGTH} characters from {ID_ALPHABET}")
    if isinstance(req, AskList):
        return None
    if not POSTER.fullmatch(req.poster):
        return "--from must match [a-z0-9][a-z0-9._-]{0,31}"
    if req.bead is not None and not BEAD.fullmatch(req.bead):
        return "--bead must be a bead ID ([a-z0-9]{1,16}-[a-z0-9.]{1,32})"
    if req.kind == "approval":
        if req.title or req.body or req.pr_url is not None or req.head_sha is not None:
            return "an approval ask takes only --bead: its context is the bead"
        return None if req.bead is not None else "an approval ask needs --bead"
    if not 1 <= len(req.title) <= MAX_TITLE or not req.title.strip() or _TITLE_CONTROLS.search(req.title):
        return f"--title must be one line of 1-{MAX_TITLE} characters"
    if len(req.body) > MAX_BODY:
        return f"the body is over {MAX_BODY:,} characters"
    if sum(not c.isspace() for c in req.body) < MIN_BODY_CHARS:
        return (f"the body must carry the context needed to answer: at least {MIN_BODY_CHARS} "
                "non-space characters")
    if req.kind == "merge":
        if req.pr_url is None or not PR_URL.fullmatch(req.pr_url):
            return "a merge ask needs --pr https://github.com/<owner>/<repo>/pull/<n>"
        if req.head_sha is None or not HEAD_SHA.fullmatch(req.head_sha):
            return "a merge ask needs --head, the pull request's 40-hex head commit"
    elif req.pr_url is not None or req.head_sha is not None:
        return "--pr and --head are for merge asks only"
    return None


def refused(message: str) -> AskReply:
    return AskReply("refused", message)


def failed() -> AskReply:
    return AskReply("failed", INTERNAL_ERROR)


def describe(req: AskRequest) -> dict[str, object]:
    """The audit fields of a request: IDs, labels and lengths, never text (R18)."""
    if isinstance(req, AskPost):
        return {"op": "post", "ask_kind": req.kind, "poster": req.poster, "bead": req.bead,
                "title_chars": len(req.title), "body_chars": len(req.body), "pr": req.pr_url is not None}
    if isinstance(req, AskList):
        return {"op": "list"}
    return {"op": "get" if isinstance(req, AskGet) else "cancel", "ask_id": normalise_id(req.ask_id)}


# --- rendering ---------------------------------------------------------------------------------
def head(row: AskRow) -> str:
    what = "merge request" if row.kind == "merge" else row.kind
    icon = "🔀" if row.kind == "merge" else "❓"
    return f"{icon} Ask {row.ask_id} · {what} · posted by {row.poster} (a local process; unverified)"


def footer(row: AskRow) -> str:
    if row.kind == "merge":
        return (f"Merging is yours to do in GitHub; admind never merges. Reply to this message (or !answer "
                f"{row.ask_id} <text>) when it is merged, or with what to change.")
    return f"Answer: reply to this message, or send !answer {row.ask_id} <text>"


def context(row: AskRow) -> str:
    """The poster's context as the card shows it: title, the PR lines of a merge ask, then the body."""
    lines = [row.title]
    if row.kind == "merge":
        lines += [f"PR: {row.pr_url}", f"Head: {row.head_sha}"]
    return "\n".join(lines) + "\n\n" + row.body


def fit(text: str) -> tuple[str, bool]:
    """The part of `text` within the card budget (CARD_CHARS characters, then CARD_LINES lines), and
    whether anything was left out."""
    cut = text[:CARD_CHARS]
    lines = cut.split("\n")
    if len(lines) > CARD_LINES:
        cut = "\n".join(lines[:CARD_LINES])
    return cut, cut != text


def question_card(row: AskRow) -> tuple[str, bool]:
    """A question or merge card: (redacted text, truncated). The context is redacted whole before the
    budget is counted (P5); the first line and the answering footer are fixed framing, never cut."""
    shown, truncated = fit(redact(context(row)))
    shown = shown.rstrip("\n")
    if truncated:
        total = redact(context(row)).count("\n") + 1
        shown += f"\n({shown.count(chr(10)) + 1} lines shown of {total}; reply !details for the rest)"
    return redact(f"{head(row)}\n{shown}\n\n{footer(row)}"), truncated


def full_text(row: AskRow) -> str:
    """What `!details` on a card sends: the whole ask, redacted."""
    return redact(f"{head(row)}\n{context(row).rstrip(chr(10))}\n\n{footer(row)}")


def age(created_at: str, at: datetime) -> str:
    seconds = max(0, int((at - datetime.fromisoformat(created_at)).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 48 * 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def list_line(row: AskRow, at: datetime) -> str:
    """One `!asks` line: `k7m2 question · 3h · <first 60 characters of the title>`."""
    return f"{row.ask_id} {row.kind} · {age(row.created_at, at)} · {row.title[:60]}"


def summary(row: AskRow, delivered: bool, answer_count: int) -> AskSummary:
    return AskSummary(row.ask_id, row.kind, row.status, row.title, row.bead,
                      None if row.digest is None else row.digest[:12], delivered, answer_count, row.outcome,
                      row.decided_by)


def view(row: AskRow, delivered: bool, answers: list[AnswerRow]) -> AskView:
    return AskView(summary(row, delivered, len(answers)),
                   [AnswerView(a.kind, a.operator, a.text, a.at) for a in answers])
