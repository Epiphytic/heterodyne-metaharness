"""Asks: what a local process posts to the operators, and how it is checked and shown (relay spec §2, §6).

Pure: request and reply structs for `ask.sock`, validation (R4, R16, R19), ask IDs (R3) and the rendering of
cards, `!details` and `!asks` lines. Question and merge cards are redacted whole first, and their budget is
counted on the redacted text (P5). An approval card is approve-bead's own readout, whole and never shortened
(relay delta R8); it is refused instead if redaction would change any of it (R21), or if it is over
MAX_APPROVAL_CARD. The daemon does the I/O.

Every card starts with its label (cxmkf K11): what kind of card it is, before anything else. A question may
carry a form (K13): two to six lettered choices with one recommended, or yes/no; its choices, an answer line
and a `Choices:` footer close the card, and a reaction or a short reply maps to a choice (K17q, K18q). Cards
are packed by `chunk.pack`, so a link, a choice line or the footer is never cut (K23).
"""

import json
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import msgspec

from heterodyne.admind import chunk, links, verbs
from heterodyne.admind.approvals import BEAD_ID, PINNED_DIGEST_LINE, Readout
from heterodyne.admind.links import LinkSpec
from heterodyne.admind.notices import NoticePost
from heterodyne.admind.notices import describe as notices_describe
from heterodyne.admind.redact import redact
from heterodyne.admind.store import ASK_ACTIVE, AnswerRow, AskRow

ASK_SOCKET = "ask.sock"
MAX_REQUEST, MAX_REPLY = 262_144, 1_048_576
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
ID_LENGTH = 4
MAX_OPEN, MAX_PER_HOUR, MAX_IN_FLIGHT = 20, 30, 2
MAX_ANSWER, MAX_ANSWERS, MAX_ANSWER_TOTAL = 16_000, 50, 64_000
CARD_LINES, CARD_CHARS = 40, 3_500       # question and merge cards only (R15, revised)
MAX_APPROVAL_CARD = 24_000      # an approval card's readout, in characters (R8, revised)
REDACTED = "this bead holds text admind would redact; decide it at the terminal"
TOO_LONG = "this bead is too long to decide from the phone; decide it at the terminal"
LINK_TOO_LONG = ("this bead has a link longer than this group's message size allows ({n} characters, "
                 "{usable} usable at chunk_chars {c}); shorten the link in the bead or raise chunk_chars")
ASK_LINK_TOO_LONG = "a link line is longer than this group's message size allows; shorten it"
DETAILS_LINK_TOO_LONG = ("Ask {ask_id} has a link longer than this group's message size now allows "
                         "({n} characters, {usable} usable at chunk_chars {c}), so it can't be sent in full; "
                         "raise chunk_chars. Nothing was sent.")
MAX_TITLE, MIN_BODY_CHARS, MAX_BODY = 200, 80, 16_000
ACTIVE = ASK_ACTIVE      # counted by MAX_OPEN; blocked is terminal (r2-4)
TERMINAL = ("approved", "denied", "stale", "superseded", "cancelled", "blocked")
INTERNAL_ERROR = "admind hit an internal error; see the audit log."
AskKind = Literal["question", "merge", "approval"]
FormKind = Literal["choices", "yes-no"]
# K14: a question with no form is accepted with FORM_WARNING while this is False; once True it is refused.
REQUIRE_QUESTION_FORM = False
FORM_WARNING = "warning: questions without --choice or --yes-no will be refused in the next release"
FORM_NEEDED = "a question needs --choice ... --recommended X, or --yes-no"
MIN_CHOICES, MAX_CHOICES, MAX_CHOICE = 2, 6, 80
LETTERS = "ABCDEF"
YES_NO = ("yes", "no")
NONE_OF_THESE = "none"

PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}/pull/[1-9][0-9]{0,9}")
HEAD_SHA = re.compile(r"[0-9a-f]{40}")
POSTER = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
BEAD = BEAD_ID
_TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_CHOICE_BAD = re.compile(r"[\x00-\x1f\x7f-\x9f  ·=]")


class AskPost(msgspec.Struct, frozen=True, tag="post", forbid_unknown_fields=True, omit_defaults=True):
    """`omit_defaults`: a post without the cxmkf fields encodes as before, so an older daemon takes it."""
    kind: AskKind
    title: str = ""
    body: str = ""
    pr_url: str | None = None
    head_sha: str | None = None
    bead: str | None = None
    poster: str = "local"
    form: FormKind | None = None
    choices: list[str] = []
    recommended: str | None = None
    links: list[LinkSpec] = []


class AskGet(msgspec.Struct, frozen=True, tag="get", forbid_unknown_fields=True):
    ask_id: str


class AskList(msgspec.Struct, frozen=True, tag="list", forbid_unknown_fields=True):
    pass


class AskCancel(msgspec.Struct, frozen=True, tag="cancel", forbid_unknown_fields=True):
    ask_id: str


class AskBinding(msgspec.Struct, frozen=True, tag="ask.binding.v1", forbid_unknown_fields=True):
    """Source-owned read-back; no caller paths, policy or answer claims."""
    ask_id: str


AskRequest = AskPost | AskGet | AskList | AskCancel | AskBinding | NoticePost


class AnswerView(msgspec.Struct, frozen=True):
    kind: str
    operator: str
    text: str
    at: str
    choice: str | None = None       # what a reaction or a short reply mapped to (K19); None for free text


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
    if isinstance(req, AskGet | AskCancel | AskBinding):
        return None if normalise_id(req.ask_id) is not None else (
            f"an ask ID is {ID_LENGTH} characters from {ID_ALPHABET}")
    if isinstance(req, AskList):
        return None
    if not POSTER.fullmatch(req.poster):
        return "--from must match [a-z0-9][a-z0-9._-]{0,31}"
    if isinstance(req, NoticePost):     # the rest is the handler's, so every refusal is audited (K9)
        return None
    if req.bead is not None and not BEAD.fullmatch(req.bead):
        return "--bead must be a bead ID ([a-z0-9]{1,16}-[a-z0-9.]{1,32})"
    if req.kind == "approval":
        if req.title or req.body or req.pr_url is not None or req.head_sha is not None:
            return "an approval ask takes only --bead: its context is the bead"
        if req.form is not None or req.choices or req.recommended is not None or req.links:
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
    refusal = _check_form(req)
    if refusal is not None:
        return refusal
    why = links.check(req.links)
    return None if why is None else links.MESSAGES[why]


def _check_form(req: AskPost) -> str | None:
    """K13-K15: a question's form, its choices and its recommendation."""
    if req.kind != "question":
        if req.form is not None or req.choices or req.recommended is not None:
            return "--choice, --yes-no and --recommended are for questions only"
        return None
    if req.form is None:
        if req.choices or req.recommended is not None:
            return "--choice and --recommended need --choice ... --recommended X, or --yes-no"
        return FORM_NEEDED if REQUIRE_QUESTION_FORM else None
    if req.form == "yes-no":
        if req.choices:
            return "a question takes --choice or --yes-no, not both"
        if req.recommended not in (None, *YES_NO):
            return "--yes-no takes --recommended yes or no"
        return None
    if not MIN_CHOICES <= len(req.choices) <= MAX_CHOICES:
        return f"a question takes {MIN_CHOICES}-{MAX_CHOICES} --choice"
    letters = LETTERS[:len(req.choices)]
    if req.recommended is None or len(req.recommended) != 1 or req.recommended not in letters:
        return f"--choice needs --recommended, one of {', '.join(letters)}"
    for letter, text in zip(letters, req.choices, strict=True):
        if not 1 <= len(text) <= MAX_CHOICE or not text.strip() or _CHOICE_BAD.search(text):
            return (f"choice {letter} must be one line of 1-{MAX_CHOICE} characters, without controls, "
                    "'·' or '='")
        if redact(text) != text:
            return f"choice {letter} would be redacted; reword it"
    return None


def refused(message: str) -> AskReply:
    return AskReply("refused", message)


def failed() -> AskReply:
    return AskReply("failed", INTERNAL_ERROR)


def describe(req: AskRequest) -> dict[str, object]:
    """The audit fields of a request: IDs, labels and lengths, never text (R18)."""
    if isinstance(req, AskPost):
        return {"op": "post", "ask_kind": req.kind, "poster": req.poster, "bead": req.bead,
                "title_chars": len(req.title), "body_chars": len(req.body), "pr": req.pr_url is not None,
                "form": req.form, "choices": len(req.choices), "links": len(req.links)}
    if isinstance(req, NoticePost):
        return {"op": "notice", **notices_describe(req)}
    if isinstance(req, AskList):
        return {"op": "list"}
    op = "get" if isinstance(req, AskGet) else "ask.binding.v1" if isinstance(req, AskBinding) else "cancel"
    return {"op": op, "ask_id": normalise_id(req.ask_id)}


# --- rendering ---------------------------------------------------------------------------------
def head(row: AskRow) -> str:
    what = "merge request" if row.kind == "merge" else row.kind
    icon = "🔀" if row.kind == "merge" else "❓"
    return f"{icon} Ask {row.ask_id} · {what} · posted by {row.poster} (a local process; unverified)"


def footer(row: AskRow) -> str:
    """The answering line (R30)."""
    if row.kind == "merge":
        return ("Merging is yours to do in GitHub; admind never merges. React 👍 or reply when it is merged, "
                "or reply with what to change.")
    return f"Answer by replying or reacting to this message, or send !answer {row.ask_id} <text>"


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


@dataclass(frozen=True)
class Form:
    """A question's form (K13): its choices (none for yes/no) and the recommended one (a letter, or
    yes/no)."""
    kind: FormKind
    choices: list[str]
    recommended: str

    def options(self) -> list[tuple[str, str | None]]:
        """(name, text) per option: the letters and their choices, or yes and no with no text."""
        if self.kind == "yes-no":
            return [(c, None) for c in YES_NO]
        return list(zip(LETTERS, self.choices, strict=False))


def stored_form(req: AskPost) -> tuple[str | None, str | None]:
    """The `question_form` and `choices` columns for a post (K13): JSON `{"choices": [...], "recommended":
    "A"}`; a yes/no question with no --recommended recommends yes."""
    if req.form is None:
        return None, None
    recommended = (req.recommended or "yes") if req.form == "yes-no" else req.recommended
    return req.form, json.dumps({"choices": list(req.choices), "recommended": recommended})


def form(row: AskRow) -> Form | None:
    if row.kind != "question" or row.question_form not in ("choices", "yes-no") or row.choices is None:
        return None
    data = json.loads(row.choices)
    kind: FormKind = "choices" if row.question_form == "choices" else "yes-no"
    return Form(kind, [str(c) for c in data["choices"]], str(data["recommended"]))


def stored_links(specs: list[LinkSpec]) -> str | None:
    return json.dumps([msgspec.to_builtins(s) for s in specs]) if specs else None


def row_links(row: AskRow) -> list[LinkSpec]:
    return [] if row.links is None else msgspec.convert(json.loads(row.links), list[LinkSpec])


def label(row: AskRow) -> str:
    """A question or merge card's first line (K11; Q1 for merges)."""
    if row.kind == "merge":
        return "ACTION: merge"
    f = form(row)
    if f is None:
        return "QUESTION"
    return "QUESTION: Multiple Options" if f.kind == "choices" else "QUESTION: yes/no"


APPROVAL_LABELS = {"design": "APPROVAL: design", "execution": "APPROVAL: execution"}
NO_PURPOSE = "APPROVAL: purpose not set"


def approval_label(r: Readout) -> str:
    """Only from the readout's purpose, never inferred (K17)."""
    return APPROVAL_LABELS.get(r.purpose or "", NO_PURPOSE)


def _mark(name: str, f: Form) -> str:
    return " (recommended)" if name == f.recommended else ""


def _rejected(f: Form) -> str:
    """What a deny-set reaction records (K17q, Amendment 1 §1): `none` on a multiple-choice card, the option
    not recommended on a yes/no card."""
    if f.kind == "choices":
        return NONE_OF_THESE
    return "yes" if f.recommended == "no" else "no"


def answer_line(row: AskRow, f: Form) -> str:
    deny = _rejected(f)
    how = "a letter or your own answer" if f.kind == "choices" else "your own answer"
    return (f"Answer: 👍 = {f.recommended} (recommended) · 👎 = {deny} · or reply with {how} "
            f"(!answer {row.ask_id} <text>)")


def choices_footer(f: Form, compact: bool = False) -> str:
    """The very last line of a card with a form (K16): every option, or only their names when `compact`."""
    shown = [name + ("" if text is None or compact else f" = {text}") + _mark(name, f)
             for name, text in f.options()]
    return "Choices: " + " · ".join(shown)


def closing(row: AskRow, room: int | None = None) -> list[str]:
    """The fixed lines that close a question or merge card: the choice lines, the answer line and the
    footer (K16), or today's answering footer. The compact footer when the full one is wider than `room`."""
    f = form(row)
    if f is None:
        return [footer(row)]
    full = choices_footer(f)
    last = choices_footer(f, compact=True) if room is not None and len(full) > room else full
    lines = [f"{name} = {text}{_mark(name, f)}" for name, text in f.options() if text is not None]
    return [*lines, answer_line(row, f), last]


def _tagged(row: AskRow, shown: str, room: int | None) -> list[tuple[str, chunk.Kind]]:
    """A question or merge card's lines: the label and head, the (redacted) context, the links, a blank
    line and the closing lines. The merge PR and Head lines and the link lines are never cut."""
    fixed: set[str] = {f"PR: {row.pr_url}", f"Head: {row.head_sha}"} if row.kind == "merge" else set()
    out = _as("atomic", [label(row), head(row)])
    for t in shown.split("\n"):
        out += _as("atomic" if t in fixed else "body", [t])
    return [*out, *_as("atomic", links.block(row_links(row))), ("", "body"),
            *_as("closing", closing(row, room))]


def _as(kind: chunk.Kind, lines: list[str]) -> list[tuple[str, chunk.Kind]]:
    return [(t, kind) for t in lines]


def _shown(row: AskRow) -> tuple[str, bool]:
    """The context within the card budget, redacted whole first (P5), and whether it was cut."""
    shown, truncated = fit(redact(context(row)))
    shown = shown.rstrip("\n")
    if truncated:
        total = redact(context(row)).count("\n") + 1
        shown += f"\n({shown.count(chr(10)) + 1} lines shown of {total}; reply !details for the rest)"
    return shown, truncated


def question_card(row: AskRow) -> tuple[str, bool]:
    """A question or merge card as one text: (redacted text, truncated). The context is redacted whole before
    the budget is counted (P5); the label, head, links and closing lines are fixed framing, never cut."""
    shown, truncated = _shown(row)
    return redact("\n".join(t for t, _ in _tagged(row, shown, None))), truncated


def card_chunks(row: AskRow, chunk_chars: int) -> tuple[list[str], bool]:
    """The card's chunks (K23), and whether its context was cut. Raises chunk.TooWide for a link line wider
    than a chunk (the post is refused)."""
    shown, truncated = _shown(row)
    return _packed(row, shown, chunk_chars), truncated


def _packed(row: AskRow, shown: str, chunk_chars: int) -> list[str]:
    room = chunk.usable(chunk_chars)
    return [redact(c) for c in chunk.pack(_tagged(row, shown, room), chunk_chars)]


def full_text(row: AskRow) -> str:
    """What `!details` on a card sends: the whole ask, redacted."""
    return redact("\n".join(t for t, _ in _tagged(row, redact(context(row)).rstrip(chr(10)), None)))


def full_chunks(row: AskRow, chunk_chars: int) -> list[str]:
    """`!details` and `!asks repeat` for a question or merge: the whole ask, packed as its card was. Raises
    chunk.TooWide if the chunk size has shrunk since it was posted so that a link no longer fits: a link is
    never cut (K23), so the caller refuses visibly."""
    return _packed(row, redact(context(row)).rstrip("\n"), chunk_chars)


# --- answers on question cards (K17q, K18q, K19) -------------------------------------------------
def map_reaction(row: AskRow, emoji: str) -> str | None:
    """The choice a reaction records: an approve-set emoji is the recommended option; a deny-set emoji is
    the other option on a yes/no card and `none` on a multiple-choice card; anything else, or no form, is no
    choice."""
    f = form(row)
    action = verbs.emoji_action(emoji)
    if f is None or action is None:
        return None
    if action == "approve":
        return f.recommended
    return _rejected(f)


def _short(text: str) -> str:
    """A short reply folded for K18q: case, surrounding spaces, one leading `(`, one trailing `.` or `)`."""
    t = text.strip().casefold()
    t = t.removeprefix("(")
    if t.endswith((".", ")")):
        t = t[:-1]
    return t.strip()


def map_reply(row: AskRow, text: str) -> str | None:
    """The choice a reply records (K18q), or None for a free answer (stored verbatim as always)."""
    f = form(row)
    if f is None:
        return None
    t = _short(text)
    if f.kind == "yes-no":
        return "yes" if t in ("yes", "y") else "no" if t in ("no", "n") else None
    letters = LETTERS[:len(f.choices)]
    if len(t) == 1 and t.upper() in letters:
        return t.upper()
    if t in verbs.APPROVE_WORDS:
        return f.recommended
    return NONE_OF_THESE if t in verbs.DENY_ALONE else None


def recorded(row: AskRow, choice: str, text: str) -> str:
    """The confirmation on the card (K19): `Recorded for k7m2: A = rerun on bel-ci (from 👍).`"""
    f = form(row)
    shown = {name: name if t is None else f"{name} = {t}" for name, t in (f.options() if f else [])}
    what = "none of these" if choice == NONE_OF_THESE else shown.get(choice, choice)
    return f"Recorded for {row.ask_id}: {what} (from {text.strip()})."


# --- approval cards (spec §6, R8, R21, R25; delta R8, R30) ---------------------------------------
_REF_LINE = re.compile(r"^(\s*)- ref .*  \[--doc ([1-9][0-9]*)\]$")


@dataclass(frozen=True)
class ApprovalCard:
    """The exact chunks to queue for the card and for its `!details`, each already checked (R21). The card
    is never shortened, so the two are the same chunks."""
    card_chunks: list[str]
    details_chunks: list[str]


UPDATED = ("Updated content of {bead} (now {digest12}). It cannot be decided yet: its stored pin no longer "
           "matches, so its originator must renew the pin and post it again. Reactions and replies here "
           "decide nothing.")
APPROVAL_ACTIONS = "👍 approve · 👎 deny — react to any part of this card, or reply approve / deny <reason>"


def approval_head(row: AskRow) -> str:
    return (f"🛂 Approval ask {row.ask_id} · bead {row.bead} · posted by {row.poster} "
            "(a local process; unverified)")


def in_flight(outstanding: int, processing: int = 0) -> str:
    """How many other asks are active, for the operator: outstanding (open, no operator response
    yet) first, then in flight (answered, deciding or uncertain, being processed by the harness)."""
    if outstanding == 0 and processing == 0:
        return "No other asks outstanding or in flight."
    head = (f"{outstanding} other ask{'' if outstanding == 1 else 's'} outstanding" if outstanding
            else "No other asks outstanding")
    return f"{head}, {processing} in flight." if processing else f"{head}."


def count_others(rows: Iterable[AskRow]) -> tuple[int, int]:
    """(outstanding, in flight) among `rows`, which are active asks other than the one replied about."""
    rows = list(rows)
    outstanding = sum(1 for o in rows if o.status == "open")
    return outstanding, len(rows) - outstanding


def decision_lines(bead: str) -> list[str]:
    """The fixed closing lines (R30)."""
    return ["", f"Approving records your approval of {bead} in btq, as you, via Marmot. Any other reply is a "
            "note for the poster."]


def _linked(lines: list[str], links: dict[int, str]) -> list[tuple[str, chunk.Kind]]:
    """The ask's lines with each pinned digest cut to 12 hex digits (R21) and, under every numbered ref, its
    forge link or a note that it has none (R25), after the ref's `resolved:` line. A `link:` line is atomic:
    it is never cut across chunks (K23)."""
    out: list[tuple[str, chunk.Kind]] = []
    pending: tuple[str, tuple[str, chunk.Kind]] | None = None      # (indent, link line) owed after the ref
    for line in lines:
        if pending is not None and not line.startswith(pending[0] + "    resolved:"):
            out.append(pending[1])
            pending = None
        out.append((PINNED_DIGEST_LINE.sub(r"\1\2", line), "body"))
        if pending is not None:
            out.append(pending[1])
            pending = None
        m = _REF_LINE.match(line)
        if m is not None:
            n = int(m.group(2))
            url = links.get(n)
            none = f"{m.group(1)}    (no forge link; read it on the host with approve-bead --doc {n})"
            shown: tuple[str, chunk.Kind] = ((f"{m.group(1)}    link: {url}", "atomic") if url is not None
                                             else (none, "body"))
            pending = (m.group(1), shown)
    if pending is not None:
        out.append(pending[1])
    return out


def approval_title(r: Readout, bead: str) -> str:
    """The ask's title for `!asks` and the poster: the readout's first title line, else the bead ID."""
    for line in r.readout["title"][1:]:
        text = line.strip().removeprefix("│").strip()
        if text:
            return text[:MAX_TITLE]
    return bead


def _checked(lines: list[tuple[str, chunk.Kind]], chunk_chars: int) -> list[str] | str:
    """The chunks `Admind.post` will queue for `lines` (packed as K23 says, its link lines whole), if
    redaction changes neither the whole text nor any chunk (a split can create a match the whole text did
    not have, r2-1): otherwise REDACTED; LINK_TOO_LONG if a link line is wider than a chunk."""
    text = "\n".join(t for t, _ in lines)
    if redact(text) != text:
        return REDACTED
    try:
        parts = chunk.pack(lines, chunk_chars)
    except chunk.TooWide as exc:
        return LINK_TOO_LONG.format(n=exc.length, usable=exc.usable, c=chunk_chars)
    return parts if all(redact(p) == p for p in parts) else REDACTED


def approval_card(row: AskRow, r: Readout, chunk_chars: int,
                  others: tuple[int, int] = (0, 0)) -> ApprovalCard | str:
    """The card for the readout `r`, whole, or why it is refused: REDACTED if it fails R21, then TOO_LONG if
    the readout is over MAX_APPROVAL_CARD characters (R8). The readout is the title, the ask (linked beads
    and refs included) and the description, as approve-bead renders them. `others` is (outstanding, in
    flight) among the other asks when it is posted."""
    if row.bead is None or r.digest is None:
        return REDACTED         # not reached: postable() refuses a readout without a digest first
    top: list[tuple[str, chunk.Kind]] = [(approval_label(r), "atomic"), (approval_head(row), "atomic"),
                                         (APPROVAL_ACTIONS, "body"), (f"digest {r.digest[:12]}", "body"),
                                         (in_flight(*others), "body")]
    card = _whole(top, r, decision_lines(row.bead), chunk_chars)
    return card if isinstance(card, str) else ApprovalCard(card, card)


def updated_content(bead: str, r: Readout, chunk_chars: int) -> list[str] | str:
    """A pinned bead's changed readout, built and checked exactly as a card but marked undecidable (R29),
    as its chunks, or why it is refused (REDACTED or TOO_LONG)."""
    return _whole([(UPDATED.format(bead=bead, digest12=(r.digest or "none")[:12]), "body")], r, [],
                  chunk_chars)


def _whole(top: list[tuple[str, chunk.Kind]], r: Readout, bottom: list[str],
           chunk_chars: int) -> list[str] | str:
    """`top`, the readout and `bottom` as checked chunks: REDACTED if it fails R21, LINK_TOO_LONG if a link
    line can't fit in a chunk (K23), then TOO_LONG if the readout is over MAX_APPROVAL_CARD characters
    (R8)."""
    forge = {link.doc: link.url for link in r.links}
    ask_lines = _linked(r.readout["ask"], forge)
    context: list[tuple[str, chunk.Kind]] = [
        *((t, "body") for t in r.readout["title"]), *([("ask:", "body"), *ask_lines] if ask_lines else []),
        *((t, "body") for t in r.readout["description"])]
    parts = _checked([*top, *context, *((t, "body") for t in bottom)], chunk_chars)
    if isinstance(parts, str):
        return parts
    if len("\n".join(t for t, _ in context)) > MAX_APPROVAL_CARD:
        return TOO_LONG
    return parts


def approval_body(r: Readout, card: ApprovalCard) -> str:
    """What `asks.body` holds for an approval ask: the readout and the checked `!details` chunks (r2-1)."""
    return json.dumps({"readout": msgspec.to_builtins(r), "details": card.details_chunks})


def stored_details(row: AskRow) -> list[str]:
    return [str(p) for p in json.loads(row.body)["details"]]


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


def reminder(row: AskRow, at: datetime, every: int | None = None) -> str:
    """A bump (asks bump B4, B14): enough of the ask to find its card without threading. The whole title
    is redacted before it is cut, so a secret crossing character 80 never leaves a fragment; the caller
    redacts the whole text again before splitting it, as for every notice."""
    lead = "Still outstanding" + ("" if every is None else f" (automatic reminder, every {every} h)")
    how = ("React 👍 or 👎 on the card above, or reply to it with approve or deny <reason>."
           if row.kind == "approval" else "Reply to the card above to answer.")
    return (f"{lead}: ask {row.ask_id} · {row.kind} · {age(row.created_at, at)} · {redact(row.title)[:80]}.\n"
            f"{how}")


NO_OUTSTANDING = "No outstanding asks."
SKIPPED = {"answered": "{ask_id} is answered; awaiting its asker", "deciding": "{ask_id} is deciding",
           "uncertain": "{ask_id} is uncertain",
           "card not delivered": "{ask_id}: card not delivered; try !asks repeat",
           "link too long": "{ask_id}: a link is longer than this group's message size now allows; "
                            "raise chunk_chars to repeat it"}


def remind_summary(verb: str, done: list[str], skipped: list[tuple[str, str]]) -> str:
    """`!asks bump` and `!asks repeat`'s own reply (B5, B11): `<verb> <n> asks: <id>, <id>.` and one line
    per skipped ask, as (ask ID, why), or NO_OUTSTANDING when there is no active ask at all."""
    if not done and not skipped:
        return NO_OUTSTANDING
    head = f"{verb} {len(done)} ask{'' if len(done) == 1 else 's'}"
    head += f": {', '.join(done)}." if done else "."
    return "\n".join([head, *(SKIPPED[why].format(ask_id=ask_id) for ask_id, why in skipped)])


def summary(row: AskRow, delivered: bool, answer_count: int) -> AskSummary:
    return AskSummary(row.ask_id, row.kind, row.status, row.title, row.bead,
                      None if row.digest is None else row.digest[:12], delivered, answer_count, row.outcome,
                      row.decided_by)


def view(row: AskRow, delivered: bool, answers: list[AnswerRow]) -> AskView:
    return AskView(summary(row, delivered, len(answers)),
                   [AnswerView(a.kind, a.operator, a.text, a.at, a.choice) for a in answers])
