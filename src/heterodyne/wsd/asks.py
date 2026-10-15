"""Plan 5i part 2: the ask bridge between a workstream's coder sessions and its operators.

A session's Stop without a close and its `ws-request question` become questions for the operator; its
`ws-request ask`, an approval (Task 7). A request is answered `accepted` by part 1's socket thread before
any handler runs (part 1's D1), so a refused one is owed a note: `_refuse` records the refusal and its note,
and creates nothing else. `advance` steers every bead's pending notes, refusals and an overflowing brief's
remainder alike, into a session that runs, at a turn boundary; a bead that doesn't run gets them in its next
resume brief (`notes_for`). Every text this module writes for the agent is fixed: the agent's own words
never reach a refusal.
"""

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.sandbox.runtime import STEER_MAX
from heterodyne.tmux import escape_controls
from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.brief import NOTE_HEADER, NoteBatch
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.launches import LAUNCHED, LaunchEntry
from heterodyne.wsd.notices import Notices
from heterodyne.wsd.runtime import AgentRuntime, SessionEvent, SteerRefused, SteerUncertain
from heterodyne.wsd.settings import RelaySettings
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import SessionHandler

if TYPE_CHECKING:
    from heterodyne.wsd.park import Parker

log = logging.getLogger(__name__)

QUESTION_TITLE = "Question from {bead}: {line}"
NO_TEXT = "The agent stopped without saying anything and without closing the bead."
ASK_UNPOSTABLE = "the question could not be posted to the operator"
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


class AskBridge:
    """One per workstream (`ws`): its session handlers, its resume notes (part 1's ResumeNotes) and what
    each of its pickups advances (`Deps.asks`)."""

    def __init__(self, journal: Journal, beads: BeadsAdapter, runtime: AgentRuntime, notices: Notices | None,
                 relay_settings: RelaySettings | None, clock: Callable[[], int], *, ws: str,
                 cp: Checkpoint = nothing) -> None:
        self.journal = journal
        self.beads = beads
        self.runtime = runtime
        self.notices = notices
        self.relay_settings = relay_settings
        self.clock = clock
        self.ws = ws
        self.cp = cp

    def handlers(self) -> dict[str, SessionHandler]:
        return {"stop": self.on_stop, "question": self.on_question}

    # --- part 1's ResumeNotes ---

    def notes_for(self, ws: str, bead: str) -> NoteBatch:
        return NoteBatch(tuple(self.journal.notes(ws, bead)))

    def notes_delivered(self, ws: str, bead: str, upto: int) -> None:
        self.journal.notes_delivered(ws, bead, upto)

    # --- the session handlers ---

    def on_stop(self, parker: "Parker", event: SessionEvent) -> None:
        if event.close_requested or not self._running_on(event):
            return              # step 1: a close is part 3's, and a Stop of another generation is stale
        if self.journal.refusal_owed(event.ws, event.bead, event.session_key, event.generation):
            return              # step 1b: the turn the agent was told to end; `advance` steers the refusal
        raise NotImplementedError("a Stop's question needs BeadsAdapter.create (Task 4)")

    def on_question(self, parker: "Parker", event: SessionEvent) -> None:
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
        raise NotImplementedError("a question needs BeadsAdapter.create (Task 4)")

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
        """At each pickup, after part 1's dispatch and before the sweep and the resume pass: the owed
        notices, then the pending notes of the beads that run."""
        if self.notices is not None:
            self.notices.retry_owed(now)
            for bead, ref in self.journal.events_without_notice(self.ws, STEER_UNCERTAIN_EVENT):
                _uncertain_notice(self.notices, self.ws, bead, ref)
        for row in self.journal.states(self.ws):
            current = self._current(self.ws, row.bead)
            if current is not None:
                self._steer_notes(current)

    def _steer_notes(self, launch: LaunchEntry) -> None:
        """One steer of the bead's oldest pending notes. Its attempt is counted, and committed, before the
        steer, so a crash during it counts too; a refused steer delivered nothing and gives it back. One
        steer per pickup: a delivered steer starts a turn, and the Stop that ends it wakes the next pickup."""
        ws, bead = launch.ws, launch.bead
        limit = STEER_MAX - len(STEER_HEAD.encode("utf-8")) - NOTE_HEADER
        batch = _batch(self.journal.steerable_notes(ws, bead, limit))
        if not batch:
            return
        refs = [ref for ref, _ in batch]
        with self.journal.transaction():
            self.journal.note_steer_attempt(refs, 1)
        try:
            self.runtime.steer(launch.session_key, launch.generation, steer_text([t for _, t in batch]))
        except SteerRefused:
            with self.journal.transaction():
                self.journal.note_steer_attempt(refs, -1)
            return
        except SteerUncertain:
            # Delivered, uncertain (part 1): never steered again and never on a brief. Only a crash
            # during the steer leaves the attempt standing, bounded by STEER_ATTEMPTS.
            log.warning("a steer of %d notes to %s may not have arrived", len(refs), bead)
            ref = f"steer-uncertain:{launch.session_key}:g{launch.generation}"
            with self.journal.transaction():
                for each in refs:
                    self.journal.note_delivered(ws, bead, each)
                self.journal.emit_once(ws, bead, STEER_UNCERTAIN_EVENT, STEER_UNCERTAIN.format(bead=bead),
                                       ref)
            self.cp("asks.uncertain")
            if self.notices is not None:
                _uncertain_notice(self.notices, ws, bead, ref)
            return
        self.cp("asks.steered!")
        with self.journal.transaction():
            for ref in refs:
                self.journal.note_delivered(ws, bead, ref)
