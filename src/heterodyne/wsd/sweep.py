"""Reconcile beads, sessions and the journal (ADR 0001 §4.3, §10): recovery's step 5, and the first
thing every pickup does. The sweep never launches; it stops what must not run, records what beads say,
and hands a dead recorded session to the launch guard as a resume operation.

Only beads with no open operation are swept: an open operation's replay owns its bead.

- A listed session whose bead is not ours (closed, claimed by another worker, gone) is stopped. If the
  stop can't be confirmed the bead is STUCK with STOP_UNCONFIRMED and the next sweep tries again; the
  session keeps the coder role until it is gone.
- A bead of ours is checked against its row: `needs-human` is STUCK; no row (a lost journal) is held as
  JOURNAL_LOST; STUCK and HELD rows stay so until the operator's release; a status, label or row that
  doesn't fit is escalated as UNEXPECTED_STATE; btq's post-claim checks must still pass.
- A STUCK or HELD row of a bead that is not ours stays, unless bd confirms the bead no longer exists: then
  there is nothing to release, and it is dropped.
- A `v2:deferred` bead (AU-4 §7) is kept while its row is DEFERRED with a current deferral record and no
  session is listed; anything else is UNEXPECTED_STATE, as is a DEFERRED row whose bead lost the label.
- Migration only: a PARKED/QUOTA row (AU-5's quota shelve, which recovery converts) is kept, reason and
  detail, while the bead is still only `v2:parked`; `needs-human`, a hold or a blocker replace it as for
  any waiting row. The sweep never writes QUOTA itself.
- A RUNNING bead with no session and a readable record gets a resume operation at `unlabelled` (it was
  never parked). With no readable record, or with a session under another key, it is escalated.

Recovery sweeps with `defer=True`: the resume operations are only collected, and recovery opens them
(`open_resumes`, one transaction) once the whole pass has read beads and the runtime and every stop was
confirmed, so a failed recovery opens nothing. Pickup opens them as it goes: a resume opened before a later
failure is carried on by the next pickup's launch guard, which waits on every hold and listed session.
"""

from collections import defaultdict
from dataclasses import dataclass

from heterodyne.wsd.beads import (
    DEFERRED,
    HELD,
    NEEDS_HUMAN,
    PARKED,
    Bead,
    NotOurs,
    RecordUnreadable,
    RoutingChanged,
)
from heterodyne.wsd.journal import OpKind
from heterodyne.wsd.park import REASONS, Parker, blocker_detail, parked_state
from heterodyne.wsd.runtime import RuntimeUnavailable, Session
from heterodyne.wsd.states import TERMINAL, WAITING, BeadState, Reason

KEEP = frozenset({BeadState.STUCK, BeadState.HELD})     # only the operator's release moves these on


@dataclass(frozen=True)
class Swept:
    resumes: int = 0         # dead sessions handed to the guard as resume operations
    held: int = 0            # beads escalated because beads and the journal disagree
    stopped: int = 0         # sessions stopped because their bead is not ours
    unconfirmed: int = 0     # stops the runtime could not confirm (each bead is STUCK/STOP_UNCONFIRMED)
    deferred: tuple[str, ...] = ()      # with defer=True: the beads that need a resume operation


def sweep(parker: Parker, defer: bool = False) -> Swept:
    """Run under `parker.entry()`. BeadsUnavailable and RuntimeUnavailable propagate: the caller holds.
    With `defer`, no resume operation is opened; their beads are returned in `deferred`."""
    return _Sweep(parker, defer).run()


def open_resumes(parker: Parker, beads: tuple[str, ...]) -> int:
    """Open the resume operations a deferred sweep collected, all in one transaction. Run under
    `parker.entry()`, with nothing read or changed since that sweep."""
    j, name = parker.d.journal, parker.ws.name
    with j.transaction():
        for bead in beads:
            op = j.op_open(OpKind.RESUME, name, bead, {"ref": ""})
            j.op_step(op.op_id, "unlabelled")      # never parked: nothing to unlabel
            j.set_state(name, bead, BeadState.RESUMING, Reason.SESSION_DEAD)
    return len(beads)


class _Sweep:
    def __init__(self, parker: Parker, defer: bool) -> None:
        self.p = parker
        self.defer = defer
        self.deferred: list[str] = []
        self.unconfirmed = 0
        self.d = parker.d
        self.j = parker.d.journal
        self.name = parker.ws.name
        self.held = 0
        self.stopped = 0
        self.resumes = 0

    def run(self) -> Swept:
        d, j, name = self.d, self.j, self.name
        ours = {b.id: b for b in d.beads.ours(name)}
        by_bead: dict[str, list[Session]] = defaultdict(list)
        for session in d.runtime.sessions(name):
            by_bead[session.bead].append(session)
        for bead, sessions in sorted(by_bead.items()):
            if bead not in ours and j.op_for(name, bead) is None:
                self._not_ours(bead, sessions)
        for row in j.states(name):
            if (row.state not in TERMINAL and row.bead not in ours and row.bead not in by_bead
                    and j.op_for(name, row.bead) is None):
                if row.state not in KEEP:
                    self._not_ours(row.bead, [])
                elif not d.beads.exists(name, row.bead):     # nothing is left for a release to move on
                    j.adopt(name, row.bead, BeadState.DROPPED, Reason.CLAIM_LOST, "the bead no longer exists")
        for bead in sorted(ours.values(), key=lambda b: b.id):
            if j.op_for(name, bead.id) is None:
                self._ours(bead, by_bead.get(bead.id, []))
        return Swept(self.resumes, self.held, self.stopped, self.unconfirmed, tuple(self.deferred))

    def _not_ours(self, bead: str, sessions: list[Session]) -> None:
        """Nothing runs without our claim: stop the bead's sessions, then record what beads say."""
        confirmed = True
        for session in sessions:
            try:
                self.d.runtime.stop(session.key)
                self.stopped += 1
            except RuntimeUnavailable:
                confirmed = False
        if not confirmed:
            self.unconfirmed += 1
            self.j.adopt(self.name, bead, BeadState.STUCK, Reason.STOP_UNCONFIRMED)
        elif not self.d.beads.exists(self.name, bead):
            self.j.adopt(self.name, bead, BeadState.DROPPED, Reason.CLAIM_LOST, "the bead no longer exists")
        elif self.d.beads.show(self.name, bead).status == "closed":
            self.j.adopt(self.name, bead, BeadState.CLOSED)
        elif self.j.state(self.name, bead) is not None:
            self.j.adopt(self.name, bead, BeadState.STUCK, Reason.CLAIM_LOST)

    def _hold(self, bead: Bead, reason: Reason, detail: str = "") -> None:
        self.held += 1
        self.p.escalate(bead.id, reason, detail)

    def _ours(self, bead: Bead, sessions: list[Session]) -> None:
        j, name = self.j, self.name
        row = j.state(name, bead.id)
        if NEEDS_HUMAN in bead.labels:
            if row is None or row.state is not BeadState.STUCK:
                j.adopt(name, bead.id, BeadState.STUCK, Reason.NEEDS_HUMAN)
            return
        if row is None:
            self._hold(bead, Reason.JOURNAL_LOST, "claimed by this workstream, with no journal record")
            return
        if row.state in KEEP:
            return
        if bead.status != "in_progress":
            self._hold(bead, Reason.UNEXPECTED_STATE, f"claimed with status {bead.status}")
            return
        try:
            self.d.beads.validate(name, bead.id)
        except RoutingChanged:
            self._hold(bead, Reason.ROUTING_CHANGED)
            return
        except NotOurs:
            j.adopt(name, bead.id, BeadState.STUCK, Reason.CLAIM_LOST)
            return
        if DEFERRED in bead.labels:
            self._deferred(bead, row.state, sessions)
        elif row.state is BeadState.DEFERRED:
            self._hold(bead, Reason.UNEXPECTED_STATE,
                       "the journal says deferred, but the bead isn't labelled")
        elif HELD in bead.labels and PARKED not in bead.labels:
            self._hold(bead, Reason.UNEXPECTED_STATE, "v2:held without v2:parked")
        elif PARKED in bead.labels:
            if sessions:
                self._hold(bead, Reason.UNEXPECTED_STATE, "parked, but a session is listed")
            elif row.state in WAITING:
                state = parked_state(bead)
                if (row.state is BeadState.PARKED and row.reason is Reason.QUOTA and state is BeadState.PARKED
                        and not bead.open_blockers()):
                    return      # migration only: an AU-5 quota shelve recovery has not converted yet
                if (row.state, row.detail) != (state, blocker_detail(bead)):
                    j.adopt(name, bead.id, state, REASONS[state], blocker_detail(bead))
            else:
                self._hold(bead, Reason.UNEXPECTED_STATE, f"parked, but the journal says {row.state.value}")
        elif row.state is not BeadState.RUNNING:
            self._hold(bead, Reason.UNEXPECTED_STATE, f"running, but the journal says {row.state.value}")
        else:
            self._running(bead, sessions)

    def _deferred(self, bead: Bead, state: BeadState, sessions: list[Session]) -> None:
        """A `v2:deferred` bead (HELD and STUCK rows were kept before this): kept while its row is DEFERRED
        with a current record and no session; `v2:parked` beside it changes nothing."""
        if sessions:
            self._hold(bead, Reason.UNEXPECTED_STATE, "deferred, but a session is listed")
        elif state is not BeadState.DEFERRED:
            self._hold(bead, Reason.UNEXPECTED_STATE, f"deferred, but the journal says {state.value}")
        elif self.p.current_deferral(bead) is None:
            self._hold(bead, Reason.UNEXPECTED_STATE, "deferred without a deferral record")

    def _running(self, bead: Bead, sessions: list[Session]) -> None:
        """A recorded running bead: its session runs on, or it gets a resume operation for the guard."""
        try:
            rec = bead.record()
        except RecordUnreadable:
            rec = None
        if rec is None:
            self._hold(bead, Reason.LAUNCH_UNRECORDED)
        elif any(s.key != rec.session_key for s in sessions):
            self._hold(bead, Reason.UNEXPECTED_STATE, "a session not in its record is listed")
        elif not sessions:
            if self.defer:
                self.deferred.append(bead.id)
            else:
                self.resumes += open_resumes(self.p, (bead.id,))
