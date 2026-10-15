"""Plan 5i part 1: what a coder session is told when it starts (ADR §5.6 for resume steering).

The first brief gives the bead, its acceptance criteria and the rules; a resume brief says where it is and
carries the notes left for it since it last ran (part 2's answers and decisions, part 3's review findings),
oldest first. A how-to line appears only for an op wsd handles, so a session is never told to use a
request that would be refused. The bead's text and the notes are passed through, cut to fit. They are
untrusted, so their terminal controls are shown escaped first (`escape_controls`): an ESC or CR could
otherwise end the bracketed paste and submit the rest as prompts of its own. The title is kept to one line.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from heterodyne.tmux import ESCAPE_GROWTH, escape_controls
from heterodyne.wsd.beads import Bead

log = logging.getLogger(__name__)

TITLE_MAX = 512                 # UTF-8 bytes of the bead's title
TEXT_MAX = 12288                # UTF-8 bytes each of the description and the acceptance criteria
NOTES_MAX = 16384               # UTF-8 bytes of notes in all (the first note may go past it)
NOTE_MAX = 65536                # UTF-8 bytes of one note: part 2's are at most 12,000, part 3's about 54,000
NOTE_HEADER = 16                # the "--- i ---" line before each note (i <= NOTES_MAX // NOTE_HEADER)
SHOWN_NOTE_MAX = ESCAPE_GROWTH * NOTE_MAX     # a note of at most NOTE_MAX, escaped
BRIEF_MAX = TITLE_MAX + 2 * TEXT_MAX + NOTES_MAX + SHOWN_NOTE_MAX + 4096
PENDING = ("[{n} more notes are pending; they will reach you as a message while you work, or in your next "
           "resume brief]")
REQUEST = "/run/hz/ws-request"
HOWTO: Mapping[str, str] = {
    "question": f"To ask the operator a question, run `{REQUEST} question --file F`, where F holds a JSON "
                'object such as {"text": "...", "choices": ["...", "..."], "recommended": "A"} (choices and '
                "recommended are optional; at most 6 choices of 80 characters); then end your turn. The task "
                "is parked and resumed with the answer.",
    "ask": "To ask for approval of something you may not do yourself inside this sandbox, run "
           f"`{REQUEST} ask --file F`, where F holds a JSON object with effect, excludes, why, risks and "
           "context (each at most 4000 characters); then end your turn. You can't ask for a push, a PR, a "
           "merge, a deploy or a notification. The task is parked and resumed with the decision.",
    "close": "When the acceptance criteria are met and your work is committed, write your evidence (what you "
             f"changed and how you tested it) to a file F and run `{REQUEST} close --evidence-file F`; then "
             "end your turn. The bead is closed after a review.",
    "stop": "If you end a turn without closing and without a request, your last message goes to the operator "
            "as a question, and the task is parked until it is answered.",
}


@dataclass(frozen=True)
class NoteBatch:
    notes: tuple[tuple[int, str], ...] = ()     # every undelivered note as (seq, text), in seq order


@dataclass(frozen=True)
class Fitted:
    texts: tuple[str, ...]          # whole notes, oldest first: a prefix of the batch
    upto: int | None                # the seq of the last note in `texts`; None when there are none
    more: int                       # the notes left pending


class ResumeNotes(Protocol):
    def notes_for(self, ws: str, bead: str) -> NoteBatch:
        """The bead's undelivered notes, oldest first."""
        ...

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        """Every note up to `upto` was in a brief the runtime confirmed."""
        ...


class NoNotes:
    def notes_for(self, ws: str, bead: str) -> NoteBatch:
        return NoteBatch()

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        return None


def _cut(text: str, limit: int, sep: str = "\n") -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore") + f"{sep}[cut: {len(raw) - limit} more bytes]"


def _title(bead: Bead) -> str:
    return _cut(" ".join(escape_controls(bead.title).split("\n")), TITLE_MAX, " ")


def _rules(bead: Bead, ops: frozenset[str]) -> list[str]:
    lines = [f"- Work in this worktree and commit locally on the branch btq/{bead.id}. Never push, and "
             "never run btq or bd."]
    lines += [f"- {HOWTO[op]}" for op in ("question", "ask", "close", "stop") if op in ops]
    return lines


def fit(batch: NoteBatch) -> Fitted:
    """The notes a brief carries: whole notes, oldest first, while they fit NOTES_MAX, stopping at the first
    that doesn't, so acking `upto` acks exactly these. They are budgeted as shown (escaped). The first is
    always taken, so every brief makes progress, and a note of at most NOTE_MAX is never cut however much
    escaping grows it (SHOWN_NOTE_MAX); only a longer one, a writer's bug, is. The rest stay pending for the
    steer or the next brief."""
    texts: list[str] = []
    used, upto = 0, None
    for seq, note in batch.notes:
        text = escape_controls(note)
        raw = len(text.encode("utf-8"))
        size = raw + NOTE_HEADER                            # its "--- i ---" line counts too
        if texts and used + size > NOTES_MAX:
            break
        if raw > SHOWN_NOTE_MAX:
            log.warning("a resume note of %d bytes shown is over SHOWN_NOTE_MAX; it is cut", raw)
            text = _cut(text, SHOWN_NOTE_MAX)
        texts.append(text)
        used += size
        upto = seq
    return Fitted(tuple(texts), upto, len(batch.notes) - len(texts))


def _notes(notes: tuple[str, ...], more: int) -> list[str]:
    out = ["", "Messages for you since you last ran, oldest first:"]
    for i, note in enumerate(notes):
        out += [f"--- {i + 1} ---", note]
    if more:
        out.append(PENDING.format(n=more))
    return out


def compose(bead: Bead, *, resume: bool, ops: frozenset[str], notes: tuple[str, ...] = (),
            more: int = 0) -> str:
    if resume:
        lines = [f"You are resumed on bead {bead.id}: {_title(bead)}. Continue where you left off.", "",
                 "The rules still apply:", *_rules(bead, ops)]
    else:
        description = bead.raw.get("description")
        acceptance = bead.raw.get("acceptance_criteria")
        lines = [f"You are working on bead {bead.id}: {_title(bead)}", ""]
        if isinstance(description, str) and description.strip():
            lines += [_cut(escape_controls(description.strip()), TEXT_MAX), ""]
        if isinstance(acceptance, str) and acceptance.strip():
            lines += ["Acceptance criteria:", _cut(escape_controls(acceptance.strip()), TEXT_MAX), ""]
        lines += ["How to work:", *_rules(bead, ops)]
    if notes:
        lines += _notes(notes, more)
    return "\n".join(lines)
