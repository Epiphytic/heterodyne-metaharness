"""Plan 5i part 2: the ask bridge between a workstream's coder sessions and its operators.

A session's Stop without a close and its `ws-request question` become questions for the operator; its
`ws-request ask`, an approval (Task 7). A request is answered `accepted` by part 1's socket thread before
any handler runs (part 1's D1), so a refused one is owed a note: `_refuse` records the refusal and its note,
and creates nothing else. `advance` steers every bead's pending notes, refusals and an overflowing brief's
remainder alike, into a session that runs, at a turn boundary; a bead that doesn't run gets them in its next
resume brief (`notes_for`). Every text this module writes for the agent is fixed: the agent's own words
never reach a refusal.
"""

import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.sandbox.runtime import STEER_MAX
from heterodyne.tmux import escape_controls
from heterodyne.wsd import gitwip, relay
from heterodyne.wsd.beads import (
    ANSWER_MARK,
    QUESTION,
    REF_KEY,
    BeadsAdapter,
    BeadsUnavailable,
    CreateUnverified,
    RecordUnreadable,
)
from heterodyne.wsd.brief import NOTE_HEADER, NoteBatch
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import ANSWER_MAX, AskPinRefused, AskRow, Journal, OpConflict
from heterodyne.wsd.launches import LAUNCHED, LaunchEntry
from heterodyne.wsd.notices import Notices
from heterodyne.wsd.relay import AskPost, RelayRefused, RelayUnavailable, RelayUncertain
from heterodyne.wsd.runtime import AgentRuntime, SessionEvent, SteerRefused, SteerUncertain
from heterodyne.wsd.settings import RelaySettings
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.workstream import SessionHandler

if TYPE_CHECKING:
    from heterodyne.wsd.park import Parker

log = logging.getLogger(__name__)

QUESTION_TITLE = "Question from {bead}: {line}"
NO_TEXT = "The agent stopped without saying anything and without closing the bead."
ASK_UNPOSTABLE = "the question could not be posted to the operator"
ASK_POST_UNKNOWN = "a post of the question had an unknown outcome and admind no longer lists it"
# The refusals: each completes "Your request <ref> was refused: <why>." (REFUSED_NOTE).
ACTIONS_UNSUPPORTED = ("it asks for an action that is never approved from inside the sandbox (a push, a PR, "
                       "a merge, a deploy or a notification)")
ASK_INCOMPLETE = ("an approval request needs effect, excludes, why, risks and context, each a non-empty "
                  "string of at most 4000 characters")
ASK_WITHHELD = "it contained a secret or a public identifier, which is never sent to the operator"
ALREADY_DENIED = "the operator already denied the same request in this session"
OPEN_ASK = ("this task already has an open question or request, or this session is not the one running "
            "the task")
NO_APPROVERS = "no operator can approve requests for this workstream"
QUESTION_INCOMPLETE = 'a question needs a "text" of 1 to 12000 characters that is not only whitespace'
REFUSALS = (ACTIONS_UNSUPPORTED, ASK_INCOMPLETE, ASK_WITHHELD, ALREADY_DENIED, OPEN_ASK, NO_APPROVERS,
            QUESTION_INCOMPLETE)
REFUSED_NOTE = ("Your request {ref} was refused: {why}. Nothing was created and nothing waits on it. "
                "Continue without it, or end your turn and say what you need.")
QUESTION_MAX = 12000            # characters of a question: with the card's framing, within admind's 16,000
OPEN_STATES = ("creating", "owed", "posted", "answered")     # an ask in one of these: no new round
STEER_HEAD = "Messages for you from wsd, oldest first:"
# Part 1's SteerUncertain: the notes count as delivered, uncertain, and the operator is told once per
# generation (later steers to it are refused). The event is journaled with the mark, so a notice a crash
# cut off is sent at the next pickup.
STEER_UNCERTAIN = "a message to {bead}'s agent may not have arrived"
STEER_UNCERTAIN_BODY = ("wsd pasted pending notes into the agent's session and can't tell whether they "
                        "arrived. They are not sent again, and no later resume brief carries them.")
STEER_UNCERTAIN_EVENT = "steer_uncertain"
# A question (Task 5): the bead it is asked on, the card the operator sees, and the texts the agent's own
# words are replaced by. D4's two choices are the card's whenever the agent's own don't pass admind's rules.
QUESTION_FRAMING = ("\n\n---\nAsked by the agent working on {bead}, in its own words (unverified). wsd "
                    "writes the operator's answer here and closes this bead.")
WITHHELD_TEXT = ("The agent's message is withheld: it contained a {kind}, which is never sent to the "
                 "operator.")
CUT_MARK = "\n[cut: the agent's message was longer than this]"
NO_LINE = "(see the message)"
CARD_TITLE = "{ws}/{bead} asks: {line}"
CARD_HEAD = "The agent working on {bead} ({title}) asks, in its own words (unverified):"
CARD_ROUND = "Branch btq/{bead}, turn {turn}, question round {round} of {limit}."
CARD_REQUEST = "Branch btq/{bead}, turn {turn}; the agent asked this by request."
D4_CHOICES = ("Go ahead with what you proposed", "Stop and explain what is blocking you")
D4_RECOMMENDED = "A"
LETTERS = "ABCDEF"              # admind's asks.LETTERS
MIN_CHOICES, MAX_CHOICES, MAX_CHOICE = 2, 6, 80     # admind's K13 limits
TITLE_MAX = 200                 # admind's asks.MAX_TITLE
BEAD_LINE, CARD_LINE, TITLE_SHOWN = 120, 150, 200   # characters of the first line, and of the task's title
# admind's _TITLE_CONTROLS and _CHOICE_BAD: a title or a choice holding one is refused.
_TITLE_BAD = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_CHOICE_BAD = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u00b7=]")
TURN_KEY, ROUND_KEY, CHOICES_KEY, RECOMMENDED_KEY = "wsd_turn", "wsd_round", "wsd_choices", "wsd_recommended"
ROUNDS_DETAIL = "question rounds without progress"
ROUNDS_TITLE = "{bead} asked {rounds} questions without progress"
ROUNDS_BODY = ("The agent working on {bead} ended {rounds} turns in a row with a question and no new commit "
               "on btq/{bead}. wsd sent no further question and marked the task needs-human; its session "
               "was left as it is.")
UNPOSTABLE_TITLE = "{bead}'s question could not be posted"
UNPOSTABLE_BODY = ("admind refused the question the agent working on {bead} asked, so wsd marked the task "
                   "needs-human. The question is on bead {child}.")
UNKNOWN_BODY = ("wsd can't tell whether admind took the question the agent working on {bead} asked: a "
                "reply was lost, and admind's list no longer shows it. wsd will not post it again, so it "
                "marked the task needs-human. The question is on bead {child}. Ask the admin agent to check "
                "its asks for it, then release the task.")
TITLE_WITHHELD = "its title withheld: it contained a {kind}"
TASK_KEY = "wsd_task"            # the question bead's task: how a rebuild after a journal loss finds it
# Task 6: the answer, as the agent's note, on the question bead, and after a journal loss. Every note is at
# most NOTE_BYTES of UTF-8, so one fits part 1's NOTES_MAX beside the brief's own text.
ANSWER_CHOICE = "The operator answered your question (ask {ask_id}): {line}"
ANSWER_FREE = "The operator answered your question (ask {ask_id}), in their words: {text}"
ANSWER_CUT = " (cut; the full answer is with the operator)"
NOTE_BYTES = 12000
ANSWER_WITHHELD = "answer withheld from beads ({kind})"
# A bead comment can be written by anyone with beads write access: a rebuilt note says it is not verified.
REBUILT_MARK = ("Recovered from the question bead's record after a journal loss, and not verified as the "
                "operator's")
REBUILT_NOTE = REBUILT_MARK + ": the answer to your question (ask {ask_id}) reads: {text}"
REBUILT_NONE = REBUILT_MARK + ": your question was answered but the answer is not on record; ask again."
_ANSWERED = re.compile(r"The operator answered your question \(ask ([A-Za-z0-9_-]{1,64})\)"
                       r"(?:: |, in their words: )(.*)", re.DOTALL)
ENDED_STATUSES = ("stale", "superseded", "cancelled", "blocked")     # an ask that ended unanswered
REPOST = ":repost"
ASK_REPOSTED = "the question's ask ended without an answer; it was posted again"
ASK_ENDED = "the question's ask ended without an answer, twice"
ENDED_BODY = ("The question the agent working on {bead} asked ended in admind twice without an answer, so "
              "wsd marked the task needs-human. The question is on bead {child}.")
NO_ANSWER = "a question's answer could not be read this pickup (%s); it is read again at the next"
NO_CANCEL = "a delivered question's ask could not be cancelled (%s); it is tried again later"
OWED_BACKOFF_MAX = 1800         # seconds between an owed question's attempts, at most
RECOVER_FAILED = "an ask could not be continued this pickup (%s); it is tried again at the next"
NOT_WAITING = "an ask's task is not waiting on its question bead; it is not posted"


def steer_text(notes: Sequence[str]) -> str:
    """One steer's text: the notes, oldest first, numbered as a brief numbers them, and shown escaped (a
    steer refuses terminal controls)."""
    lines = [STEER_HEAD]
    for i, note in enumerate(notes):
        lines += [f"--- {i + 1} ---", escape_controls(note)]
    return "\n".join(lines)


def _batch(rows: Sequence[tuple[int, str, str]]) -> list[tuple[str, str]]:
    """The oldest notes, as (ref, text), whose one steer stays within STEER_MAX. A note too long to steer
    even alone is passed over; it waits for the next resume brief."""
    taken: list[tuple[str, str]] = []
    for _, ref, text in rows:
        if len(steer_text([*(t for _, t in taken), text]).encode("utf-8")) <= STEER_MAX:
            taken.append((ref, text))
        elif taken:
            break
    return taken


def _uncertain_notice(notices: Notices, ws: str, bead: str, ref: str) -> None:
    notices.send(ws, bead, "stuck", STEER_UNCERTAIN.format(bead=bead), STEER_UNCERTAIN_BODY, ref)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - len(CUT_MARK)] + CUT_MARK


def shown_text(text: str | None) -> str:
    """What the operator, the question bead and the journal get of the agent's message (step 5): NO_TEXT
    for none, WITHHELD_TEXT if it (or what is shown of it) holds a secret or a public identifier, otherwise
    the text with its controls escaped, cut to QUESTION_MAX with a marker."""
    if text is None or not text.strip():
        return NO_TEXT
    shown = _cut(escape_controls(text), QUESTION_MAX)
    kind = sensitive_kind(text) or sensitive_kind(shown)
    return shown if kind is None else WITHHELD_TEXT.format(kind=kind)


def reshown(text: str) -> str:
    """A question bead's text read back (untrusted: anyone may have edited it) as step 5 would show it:
    through `shown_text` if escaping changes more than its backslashes (a control, a CR or a lone
    surrogate: not step 5's), else the same scan and cut without escaping again, since `escape_controls`
    doubles a backslash every time it runs."""
    if escape_controls(text) != text.replace("\\", "\\\\"):
        return shown_text(text)
    if not text.strip():
        return NO_TEXT
    shown = _cut(text, QUESTION_MAX)
    kind = sensitive_kind(text) or sensitive_kind(shown)
    return shown if kind is None else WITHHELD_TEXT.format(kind=kind)


def shown_title(title: str) -> str:
    """The task's title as the card shows it: one line, controls escaped, cut to TITLE_SHOWN; withheld if it
    (or the cut shown) holds a secret or a public identifier, as the agent's text is."""
    shown = " ".join(escape_controls(title).split())[:TITLE_SHOWN]
    kind = sensitive_kind(title) or sensitive_kind(shown)
    return shown if kind is None else TITLE_WITHHELD.format(kind=kind)


def first_line(shown: str, limit: int) -> str:
    """The first line of `shown` with words, its whitespace runs made single spaces, cut to `limit`: one
    line admind takes as a title. NO_LINE if there is none, or if the cut holds something sensitive."""
    for raw in shown.split("\n"):
        line = " ".join(raw.split())[:limit].strip()
        if line:
            return line if sensitive_kind(line) is None and not _TITLE_BAD.search(line) else NO_LINE
    return NO_LINE


def _choice_ok(text: str) -> bool:
    return (1 <= len(text) <= MAX_CHOICE and bool(text.strip()) and not _CHOICE_BAD.search(text)
            and sensitive_kind(text) is None)


def card_choices(choices: object, recommended: object) -> tuple[list[str], str]:
    """The agent's choices and recommendation if they pass admind's K13 rules (mirrored here: wsd never
    imports admind), else D4's two, recommending A. admind also refuses a choice it would redact: a
    secret or public identifier is refused here; a login directory's path is not known to wsd."""
    if isinstance(choices, list) and isinstance(recommended, str):
        given = cast(list[object], choices)
        texts = [c for c in given if isinstance(c, str)]
        if (MIN_CHOICES <= len(given) <= MAX_CHOICES and len(texts) == len(given) and len(recommended) == 1
                and recommended in LETTERS[:len(given)] and all(map(_choice_ok, texts))):
            return texts, recommended
    return list(D4_CHOICES), D4_RECOMMENDED


def cut_note(text: str) -> str:
    """`text` if it is at most NOTE_BYTES of UTF-8, else cut on a character boundary, with ANSWER_CUT."""
    if len(text.encode("utf-8")) <= NOTE_BYTES:
        return text
    room = NOTE_BYTES - len(ANSWER_CUT.encode("utf-8"))
    return text.encode("utf-8")[:room].decode("utf-8", "ignore") + ANSWER_CUT


def answer_note(ask_id: str, text: str, choice: str | None, choices: Sequence[str]) -> str:
    """The agent's note of the operator's answer: the choice's line if `choice` is a letter of the card's
    `choices`, else the operator's own words."""
    if choice is not None and len(choice) == 1 and choice in LETTERS[:len(choices)]:
        line = f"{choice}: {choices[LETTERS.index(choice)]}"
        return cut_note(ANSWER_CHOICE.format(ask_id=ask_id, line=line))
    return cut_note(ANSWER_FREE.format(ask_id=ask_id, text=text))


def rebuilt_note(comment: str, mark: str) -> str | None:
    """The note a lost journal had, from the question bead's answer comment (`mark`, then the note wsd
    wrote): marked unverified, since anyone with beads write access can write a comment. REBUILT_NONE if
    the answer was withheld, or the comment is not one wsd wrote; None without `mark`."""
    head, _, rest = comment.partition("\n")
    if head != mark:
        return None
    found = _ANSWERED.fullmatch(rest)
    if found is None or sensitive_kind(rest) is not None:
        return REBUILT_NONE
    return cut_note(REBUILT_NOTE.format(ask_id=found.group(1), text=found.group(2)))


def question_card(ws: str, bead: str, bead_title: str, child: str, shown: str, turn: int, round_: int,
                  limit: int, choices: Sequence[str], recommended: str) -> AskPost:
    """The question's ask (step 9): its fixed head and tail carry admind's minimum of 80 non-space
    characters, and `shown` (at most QUESTION_MAX) keeps it within admind's 16,000. A request's question
    (`round_` 0) is not a round."""
    title = CARD_TITLE.format(ws=ws, bead=bead, line=first_line(shown, CARD_LINE))
    tail = (CARD_ROUND.format(bead=bead, turn=turn, round=round_, limit=limit) if round_
            else CARD_REQUEST.format(bead=bead, turn=turn))
    head = CARD_HEAD.format(bead=bead, title=shown_title(bead_title))
    return AskPost("question", title=title[:TITLE_MAX], body="\n\n".join((head, shown, tail)), bead=child,
                   poster=relay.poster_for(ws), form="choices", choices=list(choices),
                   recommended=recommended)


def _choices_of(metadata: dict[str, object]) -> tuple[list[str], str]:
    """The card's choices and recommendation, read back from the question bead's metadata (untrusted)."""
    try:
        choices: object = json.loads(str(metadata.get(CHOICES_KEY, "")))
    except ValueError:
        choices = None
    return card_choices(choices, metadata.get(RECOMMENDED_KEY))


class AsksPins:
    """BeadsAdapter's ref pins (`beads.RefPins`) on the journal's `asks` rows: a question's `child`. Only a
    ref an asks row holds has a pin store; any other (an approval's, until Task 7 gives it one) fails
    closed with a fixed text, logged, which the adapter makes BeadsUnavailable."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def pinned(self, ws: str, ref: str) -> str | None:
        try:
            return self.journal.ask_pinned(ws, ref)
        except AskPinRefused as exc:
            log.error("%s", exc)
            raise

    def pin(self, ws: str, ref: str, bead: str) -> None:
        try:
            self.journal.ask_pin(ws, ref, bead)
        except AskPinRefused as exc:
            log.error("%s", exc)
            raise


class AskBridge:
    """One per workstream (`ws`): its session handlers, its resume notes (part 1's ResumeNotes) and what
    each of its pickups advances (`Deps.asks`)."""

    def __init__(self, journal: Journal, beads: BeadsAdapter, runtime: AgentRuntime, notices: Notices | None,
                 relay_settings: RelaySettings, clock: Callable[[], int], *, ws: str,
                 cp: Checkpoint = nothing) -> None:
        self.journal = journal
        self.beads = beads
        self.runtime = runtime
        self.notices = notices
        self.relay_settings = relay_settings
        self.clock = clock
        self.ws = ws
        self.cp = cp
        self.parker: Parker | None = None

    def bind(self, parker: "Parker") -> None:
        """The workstream's Parker, for what `advance` continues outside a handler (a post's escalation)."""
        self.parker = parker

    def handlers(self) -> dict[str, SessionHandler]:
        return {"stop": self.on_stop, "question": self.on_question}

    # --- part 1's ResumeNotes ---

    def notes_for(self, ws: str, bead: str) -> NoteBatch:
        self._reconcile(ws, bead)       # startup recovery may compose a brief before any pickup
        return NoteBatch(tuple(self.journal.notes(ws, bead)))

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        self.journal.notes_delivered(ws, bead, upto)

    # --- the session handlers ---

    def on_stop(self, parker: "Parker", event: SessionEvent) -> None:
        if event.close_requested or not self._running_on(event):
            return              # step 1: a close is part 3's, and a Stop of another generation is stale
        ws, bead, key, gen = event.ws, event.bead, event.session_key, event.generation
        if self.journal.refused_in_turn(ws, bead, key, gen, event.turn):
            return              # step 1b: the turn the agent was told to end; its note is steered or briefed
        ref = f"q:{ws}:{bead}:{gen}:{event.turn}"
        row = self.journal.ask(ref)
        if row is None:         # a replayed Stop goes on from its row (step 6)
            if self.journal.ask_open(ws, bead, OPEN_STATES):
                return          # step 2: a Stop while an ask is open is not a new round
            rounds, last = self.journal.nudge(ws, bead)
            head = self._tree(ws, bead)
            if head is not None and head != last:
                rounds = 0      # step 3: progress
            kept = head if head is not None else last
            if rounds >= self.relay_settings.question_rounds_before_human:
                self._too_many_rounds(parker, bead, rounds, kept)
                return
            with self.journal.transaction():     # D3: the round counts once its answer is delivered
                self.journal.ask_add(ref, ws, bead, "question", rounds + 1)
                self.journal.nudge_set(ws, bead, rounds, kept)
            self.cp("asks.row")
            row = self._row(ref)
        self._ask(parker, row, shown_text(event.text), event.turn, list(D4_CHOICES), D4_RECOMMENDED)

    def on_question(self, parker: "Parker", event: SessionEvent) -> None:
        ref = f"q:{event.event_id}"
        row = self.journal.ask(ref)
        if row is not None:     # a replayed request goes on from its row
            text = event.body.get("text")
            choices, recommended = card_choices(event.body.get("choices"), event.body.get("recommended"))
            self._ask(parker, row, shown_text(text if isinstance(text, str) else None), event.turn, choices,
                      recommended)
            return
        if not self._running_on(event) or self.journal.ask_open(event.ws, event.bead, OPEN_STATES):
            self._refuse(event, OPEN_ASK)
            return
        text = event.body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > QUESTION_MAX:
            self._refuse(event, QUESTION_INCOMPLETE)
            return
        if sensitive_kind(text) is not None:
            self._refuse(event, ASK_WITHHELD)
            return
        choices, recommended = card_choices(event.body.get("choices"), event.body.get("recommended"))
        with self.journal.transaction():
            self.journal.ask_add(ref, event.ws, event.bead, "question", 0)
        self.cp("asks.row")
        row = self._row(ref)
        self._ask(parker, row, shown_text(text), event.turn, choices, recommended)

    # --- a question: its bead, its park and its post (steps 7-9) ---

    def _ask(self, parker: "Parker", row: AskRow, shown: str, turn: int, choices: Sequence[str],
             recommended: str) -> None:
        """Steps 7-9 for a row still `creating` whose task runs: the question bead (found again if an
        earlier attempt made it), the park on it, then the post once the task waits on it. A row already
        further on, or a task no longer running, is `recover`'s."""
        if row.state != "creating" or self._state(row.ws, row.bead) is not BeadState.RUNNING:
            return
        metadata = {TURN_KEY: str(turn), ROUND_KEY: str(row.round), CHOICES_KEY: json.dumps(list(choices)),
                    RECOMMENDED_KEY: recommended, TASK_KEY: row.bead}
        child = self.beads.create(row.ws, ref=row.ref, title=QUESTION_TITLE.format(
            bead=row.bead, line=first_line(shown, BEAD_LINE)), description=shown + QUESTION_FRAMING.format(
            bead=row.bead), labels=(QUESTION,), metadata=metadata)
        self.cp("asks.created!")
        state = parker.park(row.bead, (child,), why=f"question {row.ref}")
        self.cp("asks.parked")
        if state is BeadState.WAITING_INPUT:
            self._post(parker, self._row(row.ref), self.clock())

    def _post(self, parker: "Parker", row: AskRow, now: int) -> None:
        """Post the question's card, rebuilt from its bead (read through `holder`), at most once: the row is
        marked uncertain, committed, before the post, so a post whose outcome is unknown (a lost reply, or
        wsd dying) is looked for in admind's list, and never posted again. The list holds admind's active
        asks but only its 20 newest finished ones, and admind has no lookup by bead or ref, so absence
        proves nothing: the ask fails closed (`_unpostable`, ASK_POST_UNKNOWN)."""
        ws, bead = row.ws, row.bead
        # A repost asks on its original's bead, pinned from the original's row: no bead holds its own ref.
        child = row.child if row.ref.endswith(REPOST) else self.beads.holder(ws, row.ref)
        if child is None:
            log.warning(NOT_WAITING)
            return
        self._refresh_head(ws, bead)
        sock = self.relay_settings.ask_socket
        if row.uncertain:
            try:
                listed = relay.list_asks(sock)
            except (RelayUnavailable, RelayUncertain, RelayRefused):
                self._owed(row, now, uncertain=1)
                return
            found = [a.ask_id for a in listed if a.kind == "question" and a.bead == child]
            if found:
                with self.journal.transaction():
                    self.journal.ask_set(row.ref, state="posted", ask_id=found[-1], uncertain=0, next_at=None)
            else:
                self._unpostable(parker, row, child, why=ASK_POST_UNKNOWN, body=UNKNOWN_BODY)
            return
        card = self._card(row, child)
        with self.journal.transaction():
            self.journal.ask_set(row.ref, uncertain=1)
        self.cp("asks.posting")
        try:
            ask_id = relay.post_ask(sock, card)
        except RelayUnavailable:
            self._owed(row, now, uncertain=0)
            return
        except RelayUncertain:
            self._owed(row, now, uncertain=1)
            return
        except RelayRefused as exc:
            if exc.retryable:
                self._owed(row, now, uncertain=0)
            else:
                self._unpostable(parker, row, child)
            return
        self.cp("asks.posted!")
        with self.journal.transaction():
            self.journal.ask_set(row.ref, state="posted", ask_id=ask_id, uncertain=0, next_at=None)

    def _card(self, row: AskRow, child: str) -> AskPost:
        """The card, from the question bead wsd made and the task's own title. The bead's text, framing
        removed, is read back untrusted (`reshown`), as is its metadata (`card_choices`)."""
        made = self.beads.show(row.ws, child)
        description = str(made.raw.get("description", ""))
        shown = reshown(description.removesuffix(QUESTION_FRAMING.format(bead=row.bead)))
        picked, recommended = _choices_of(made.metadata)
        turn = str(made.metadata.get(TURN_KEY, ""))
        return question_card(row.ws, row.bead, self.beads.show(row.ws, row.bead).title, child, shown,
                             int(turn) if turn.isdigit() else 0, row.round,
                             self.relay_settings.question_rounds_before_human, picked, recommended)

    def _owed(self, row: AskRow, now: int, *, uncertain: int) -> None:
        wait = min(self.relay_settings.notice_backoff_seconds * 2 ** row.attempts, OWED_BACKOFF_MAX)
        with self.journal.transaction():
            self.journal.ask_set(row.ref, state="owed", uncertain=uncertain, attempts=row.attempts + 1,
                                 next_at=now + int(wait))

    def _unpostable(self, parker: "Parker", row: AskRow, child: str, *, why: str = ASK_UNPOSTABLE,
                    body: str = UNPOSTABLE_BODY, state: str = "refused") -> None:
        """A permanent refusal, a post whose outcome can't be known, or a repost that ended unanswered too
        (`state` "ended"): the row is in `state`, the task escalated needs-human, the operator told."""
        if self.notices is not None:
            self.notices.send(row.ws, row.bead, "stuck", UNPOSTABLE_TITLE.format(bead=row.bead),
                              body.format(bead=row.bead, child=child), f"unpostable:{row.ref}")
        with self.journal.transaction():
            self.journal.ask_set(row.ref, state=state, why=why, uncertain=0, next_at=None)
            op = parker.escalation_for(row.bead, Reason.NEEDS_HUMAN, why)
        parker.replay_escalate(op)

    def _too_many_rounds(self, parker: "Parker", bead: str, rounds: int, head: str | None) -> None:
        """Step 4: no question; the operator is told (first, so a crash before the escalation sends it once),
        then the task is escalated needs-human. Its session is left as it is."""
        if self.notices is not None:
            self.notices.send(self.ws, bead, "stuck", ROUNDS_TITLE.format(bead=bead, rounds=rounds),
                              ROUNDS_BODY.format(bead=bead, rounds=rounds),
                              f"stuck:{self.ws}:{bead}:{rounds}")
        with self.journal.transaction():
            self.journal.nudge_set(self.ws, bead, rounds, head)
            op = parker.escalation_for(bead, Reason.NEEDS_HUMAN, ROUNDS_DETAIL)
        parker.replay_escalate(op)

    def _tree(self, ws: str, bead: str) -> str | None:
        """The tree of btq/<bead>'s HEAD in the task's worktree (D3's progress), read only; None if it
        can't be read, which counts as no progress."""
        try:
            rec = self.beads.show(ws, bead).record()
            if rec is None:
                return None
            return gitwip.tree(gitwip.pin(Path(rec.repo), Path(rec.worktree), f"btq/{bead}"))
        except (RecordUnreadable, gitwip.GitFailed, OSError, UnicodeDecodeError):
            return None

    def _refresh_head(self, ws: str, bead: str) -> None:
        """Once the task is parked on its question, its tree (the park's WIP commit included) is where the
        next round counts progress from: uncommitted work the park committed is not progress."""
        head = self._tree(ws, bead)
        if head is not None:
            rounds, _ = self.journal.nudge(ws, bead)
            with self.journal.transaction():
                self.journal.nudge_set(ws, bead, rounds, head)

    def _row(self, ref: str) -> AskRow:
        found = self.journal.ask(ref)
        if found is None:
            raise KeyError("the ask's row is gone")
        return found

    def _state(self, ws: str, bead: str) -> BeadState | None:
        row = self.journal.state(ws, bead)
        return None if row is None else row.state

    def recover(self, now: int) -> None:
        """At each pickup: post every question that is due, `creating` (a post a crash or a failed park
        cut off) or `owed` (its next attempt due), once its task waits on its question bead. A row
        without a bead is its event's: a replay continues it. A failure leaves the row for the next pickup."""
        parker = self.parker
        if parker is None:
            return
        for row in self.journal.asks_in(self.ws, ("creating", "owed")):
            if row.kind == "question" and row.child is None and row.ref.endswith(REPOST):
                row = self._pin_repost(row)
            if row.kind != "question" or row.child is None:
                continue
            if row.state == "owed" and row.next_at is not None and row.next_at > now:
                continue
            try:
                if self._waiting_on(row.ws, row.bead, row.child):
                    self._post(parker, row, now)
            except (BeadsUnavailable, CreateUnverified, OpConflict) as exc:
                log.warning(RECOVER_FAILED, type(exc).__name__)

    def _waiting_on(self, ws: str, bead: str, child: str) -> bool:
        if self._state(ws, bead) is not BeadState.WAITING_INPUT:
            return False
        if child in {d.id for d in self.beads.show(ws, bead).open_blockers()}:
            return True
        log.warning(NOT_WAITING)
        return False

    def _refuse(self, ev: SessionEvent, why: str) -> None:
        """Record what the agent is owed for a request it was told was accepted: the refusal and its note,
        once per event, in one transaction, and nothing else. `why` is one of REFUSALS."""
        text = REFUSED_NOTE.format(ref=ev.event_id, why=why)
        with self.journal.transaction():
            self.journal.refusal_add(ev.ws, ev.bead, ev.event_id, ev.session_key, ev.generation, ev.turn,
                                     text)
            self.journal.note_add(ev.ws, ev.bead, f"refused:{ev.event_id}", text)

    def _running_on(self, ev: SessionEvent) -> bool:
        current = self._current(ev.ws, ev.bead)
        return current is not None and current.ident == (ev.session_key, ev.generation)

    def _current(self, ws: str, bead: str) -> LaunchEntry | None:
        """The bead's running launch: the bead is RUNNING in the journal and its latest generation's launch
        is journaled `launched` (so its receipt, and its brief's notes_delivered, are committed)."""
        row = self.journal.state(ws, bead)
        if row is None or row.state is not BeadState.RUNNING:
            return None
        entries = self.journal.launches_of(ws, bead)
        if not entries:
            return None
        latest = max(entries, key=lambda e: (e.journaled_at, e.generation))
        return latest if latest.outcome == LAUNCHED else None

    # --- each pickup ---

    def advance(self, now: int) -> None:
        """At each pickup, after the replays (and part 3's review settle) and before the resume pass: the
        delivered answers finished, the posted questions' answers read, what is owed posted, then the
        pending notes of the beads that run. A steer a crash cut off is uncertain first."""
        self._reconcile(self.ws)
        self._delivered(now)
        self._answers(now)
        self.recover(now)
        if self.notices is not None:
            for bead, ref in self.journal.events_without_notice(self.ws, STEER_UNCERTAIN_EVENT):
                _uncertain_notice(self.notices, self.ws, bead, ref)
        for row in self.journal.states(self.ws):
            current = self._current(self.ws, row.bead)
            if current is not None:
                self._steer_notes(current)

    def flush(self, now: int) -> None:
        """Last in each pickup, after the resume pass: the owed notices."""
        if self.notices is not None:
            self.notices.retry_owed(now)

    def resume_gate(self, ws: str, bead: str) -> bool:
        """False while a question of the bead has no note yet: one being asked or posted, or one that ended
        unanswered and is not reposted yet. An answered row's note was written with its answer."""
        rows = self.journal.asks_in(ws, ("creating", "owed", "posted", "ended"))
        return not any(r.bead == bead and (r.state != "ended" or (
            not r.ref.endswith(REPOST) and self.journal.ask(r.ref + REPOST) is None)) for r in rows)

    # --- answers (Task 6) ---

    def _answers(self, now: int) -> None:
        """Each posted question's answer, read from admind: stored with its note in one transaction, then put
        on the question bead (withheld if it holds a secret), which is closed. An answered row whose bead a
        crash left open is closed here too. A question that ended unanswered is reposted once (`_ended`)."""
        sock = self.relay_settings.ask_socket
        for row in self.journal.asks_in(self.ws, ("posted",)):
            if row.kind != "question" or row.ask_id is None or row.child is None:
                continue
            try:
                view = relay.get_ask(sock, row.ask_id)
            except (RelayUnavailable, RelayUncertain, RelayRefused) as exc:
                log.warning(NO_ANSWER, type(exc).__name__)
                continue
            answers = [a for a in view.answers if a.kind == "answer"]
            try:
                if answers:
                    self._answered(row, row.child, row.ask_id, answers[0].text, answers[0].choice)
                elif view.summary.status in ENDED_STATUSES:
                    self._ended(row, row.child)
            except (BeadsUnavailable, CreateUnverified, OpConflict) as exc:
                log.warning(RECOVER_FAILED, type(exc).__name__)
        for row in self.journal.asks_in(self.ws, ("answered",)):
            if row.kind == "question" and row.child is not None and row.answer is not None:
                try:
                    self._close(row, row.child, row.answer)
                except BeadsUnavailable as exc:
                    log.warning(RECOVER_FAILED, type(exc).__name__)

    def _answered(self, row: AskRow, child: str, ask_id: str, text: str, choice: str | None) -> None:
        choices, _ = _choices_of(self.beads.show(row.ws, child).metadata)
        note = answer_note(ask_id, text, choice, choices)
        with self.journal.transaction():
            self.journal.ask_answer(row.ref, text[:ANSWER_MAX], choice)
            self.journal.note_add(row.ws, row.bead, _note_ref(row.ref), note)
        self.cp("asks.answered")

    def _close(self, row: AskRow, child: str, answer: str) -> None:
        """The answer on the question bead, then the bead closed: both read back, so a replay writes
        neither twice. The comment is the note itself, or ANSWER_WITHHELD."""
        kind = sensitive_kind(answer)
        if kind is not None:
            comment = ANSWER_WITHHELD.format(kind=kind)
        else:
            choices, _ = _choices_of(self.beads.show(row.ws, child).metadata)
            comment = answer_note(row.ask_id or "", answer, row.choice, choices)
        self.beads.close_question(row.ws, child, comment)
        self.cp("asks.closed!")

    def _ended(self, row: AskRow, child: str) -> None:
        """An ask that ended unanswered: the first is posted again once, on the same bead, as `<ref>:repost`;
        the repost's end escalates the task needs-human, its bead left open for the operator's release."""
        if row.ref.endswith(REPOST):
            if self.parker is not None:
                self._unpostable(self.parker, row, child, why=ASK_ENDED, body=ENDED_BODY, state="ended")
            return
        with self.journal.transaction():
            self.journal.ask_set(row.ref, state="ended", why=ASK_REPOSTED, next_at=None)
            self.journal.ask_add(row.ref + REPOST, row.ws, row.bead, "question", row.round)
        self.cp("asks.ended")
        self._pin_repost(self._row(row.ref + REPOST))

    def _pin_repost(self, row: AskRow) -> AskRow:
        original = self.journal.ask(row.ref.removesuffix(REPOST))
        if original is None or original.child is None:
            return row
        self.journal.ask_pin(row.ws, row.ref, original.child)
        return self._row(row.ref)

    def _delivered(self, now: int) -> None:
        """Each answered question whose note reads back delivered is `delivered`, with its round counted
        (D3), in one transaction; its admind ask is then cancelled, so it no longer counts against admind's
        open asks. A cancel that fails is tried again later (`next_at`); a refused one (the ask already
        ended) is not."""
        for row in self.journal.asks_in(self.ws, ("answered",)):
            if row.kind != "question" or self.journal.note_is_delivered(_note_ref(row.ref)) is not True:
                continue
            rounds, head = self.journal.nudge(row.ws, row.bead)
            with self.journal.transaction():
                self.journal.ask_set(row.ref, state="delivered", next_at=None if row.ask_id is None else now)
                self.journal.nudge_set(row.ws, row.bead, rounds + 1, head)
            self.cp("asks.delivered")
        for row in self.journal.asks_in(self.ws, ("delivered",)):
            if row.ask_id is not None and row.next_at is not None and row.next_at <= now:
                self._cancel(row, row.ask_id, now)

    def _cancel(self, row: AskRow, ask_id: str, now: int) -> None:
        try:
            relay.cancel_ask(self.relay_settings.ask_socket, ask_id)
        except RelayRefused as exc:
            if exc.retryable:
                self._cancel_later(row, now)
                return
        except (RelayUnavailable, RelayUncertain) as exc:
            log.warning(NO_CANCEL, type(exc).__name__)
            self._cancel_later(row, now)
            return
        with self.journal.transaction():
            self.journal.ask_set(row.ref, next_at=None)

    def _cancel_later(self, row: AskRow, now: int) -> None:
        wait = min(self.relay_settings.notice_backoff_seconds * 2 ** row.attempts, OWED_BACKOFF_MAX)
        with self.journal.transaction():
            self.journal.ask_set(row.ref, attempts=row.attempts + 1, next_at=now + int(wait))

    def rebuild(self) -> None:
        """Startup recovery, after a journal loss: each closed question bead of a task of ours whose ref
        the journal has no row for, with wsd's answer comment, gets its row (`answered`, no ask ID, so
        nothing is cancelled) and its note again, marked unverified (`rebuilt_note`). A question still open
        has no answer to rebuild; the operator's release is its way out."""
        ours = {b.id for b in self.beads.ours(self.ws)}
        for made in self.beads.questions(self.ws):
            ref, task = made.metadata.get(REF_KEY), made.metadata.get(TASK_KEY)
            if (made.status != "closed" or not isinstance(ref, str) or not isinstance(task, str)
                    or task not in ours or self.journal.ask(ref) is not None):
                continue
            mark = ANSWER_MARK.format(ref=ref)
            notes = [n for n in map(lambda c: rebuilt_note(c, mark), self.beads.comments(self.ws, made.id))
                     if n is not None]
            if not notes:
                continue
            with self.journal.transaction():
                self.journal.ask_add(ref, self.ws, task, "question", 0)
                self.journal.ask_set(ref, state="answered")
                self.journal.note_add(self.ws, task, _note_ref(ref), notes[0])
            self.journal.ask_pin(self.ws, ref, made.id)

    def _reconcile(self, ws: str, bead: str | None = None) -> None:
        """Every steer whose outcome was never journaled (a crash cut it off) may have arrived: its notes
        are delivered-uncertain, as SteerUncertain's are, and its event is the one stuck notice `advance`
        owes (events_without_notice). Never steered again, and never on a brief."""
        for steer in self.journal.steers_unended(ws, bead):
            log.warning("a steer of %d notes to %s was cut off and may have arrived", len(steer.refs),
                        steer.bead)
            with self.journal.transaction():
                self._delivered_uncertain(steer.ws, steer.bead, steer.session_key, steer.generation,
                                          steer.refs)

    def _delivered_uncertain(self, ws: str, bead: str, session_key: str, generation: int,
                             refs: Sequence[str]) -> str:
        """The notes delivered, the steer ended and its steer_uncertain event, in the caller's transaction."""
        ref = f"steer-uncertain:{session_key}:g{generation}"
        for each in refs:
            self.journal.note_delivered(ws, bead, each)
        self.journal.steer_ended(ws, bead)
        self.journal.emit_once(ws, bead, STEER_UNCERTAIN_EVENT, STEER_UNCERTAIN.format(bead=bead), ref)
        return ref

    def _steer_notes(self, launch: LaunchEntry) -> None:
        """One steer of the bead's oldest pending notes. It is journaled as started, in one transaction with
        its attempt, before the steer; its outcome ends it. One a crash cut off is reconciled as uncertain
        (`_reconcile`), so it is never resent. A refused steer delivered nothing and gives its attempt
        back. One steer per pickup: a delivered steer starts a turn, and the Stop that ends it wakes the
        next pickup."""
        ws, bead = launch.ws, launch.bead
        limit = STEER_MAX - len(STEER_HEAD.encode("utf-8")) - NOTE_HEADER
        batch = _batch(self.journal.steerable_notes(ws, bead, limit))
        if not batch:
            return
        refs = [ref for ref, _ in batch]
        with self.journal.transaction():
            self.journal.note_steer_attempt(refs, 1)
            self.journal.steer_started(ws, bead, launch.session_key, launch.generation, refs)
        try:
            self.runtime.steer(launch.session_key, launch.generation, steer_text([t for _, t in batch]))
        except SteerRefused:
            with self.journal.transaction():
                self.journal.note_steer_attempt(refs, -1)
                self.journal.steer_ended(ws, bead)
            return
        except SteerUncertain:
            # Delivered, uncertain (part 1): never steered again and never on a brief.
            log.warning("a steer of %d notes to %s may not have arrived", len(refs), bead)
            with self.journal.transaction():
                ref = self._delivered_uncertain(ws, bead, launch.session_key, launch.generation, refs)
            self.cp("asks.uncertain")
            if self.notices is not None:
                _uncertain_notice(self.notices, ws, bead, ref)
            return
        self.cp("asks.steered!")
        with self.journal.transaction():
            for ref in refs:
                self.journal.note_delivered(ws, bead, ref)
            self.journal.steer_ended(ws, bead)


def _note_ref(ref: str) -> str:
    """The answer note's ref: the question's own, a repost's included, so a rebuild finds the same one."""
    return f"answer:{ref.removesuffix(REPOST)}"
