"""Deterministic pickup (ADR 0001 §5.2) for one workstream, and the pickup journal (§4.3).

On every trigger (a turn ends, a bead closes or parks, an approval resolves, the 60s backstop), under
the workstream's operation lock (`Parker.entry`):
1. the runtime must be available and list the workstream's sessions, or the workstream holds (no
   failure budget, no `needs-human`); once it does, pickup clears that hold, whoever set it;
2. unsettled actions are found (closed beads included); any hold the launch guard respects;
3. the sweep (`sweep.py`) reconciles beads, sessions and the journal: sessions of beads no longer ours
   are stopped, and a running bead whose session is gone gets a resume operation;
4. every open operation is replayed: park, release and escalation finish; pickup and resume reach the
   launch guard, which refuses while any hold applies;
5. any hold left: pickup reports HELD;
6. the coder role is taken if the runtime lists any session of the workstream, live or unknown, under
   whatever role name it was launched (wsd launches one role per workstream);
7. otherwise resumable parked beads first, then new ready work, trying each candidate in turn until one
   starts. A refused claim, a lost claim or a confirmed launch failure moves on to the next candidate
   (the "never idle while an unblocked bead exists" rule); an uncertain launch keeps the role and holds.

New work is journaled: intent (with the coder role it is for), claim (inside the claim gate), worktree,
then the launch guard, which writes the launched-session record before launching. A replay launches the
role, profile, session key and worktree its journal recorded; a coder role renamed in the configuration
before the worktree step escalates the pickup rather than mix the two. A claim with an uncertain outcome is
read back before anything else; while it can't be read back, the workstream is held.
"""

from dataclasses import dataclass
from enum import StrEnum

from heterodyne.wsd.beads import (
    Bead,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.gate import Paused
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.park import Launch, Parker, resumable
from heterodyne.wsd.runtime import RuntimeUnavailable
from heterodyne.wsd.states import BeadState, Reason, ws_state
from heterodyne.wsd.sweep import sweep
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, place, record

POINTS = ("lock.waiting", "pickup.intent", "gate.checked", "pickup.claimed!", "pickup.claimed",
          "pickup.worktree!", "pickup.worktree", "pickup.recorded!", "pickup.launched!", "pickup.done")
RESUMABLE_ROWS = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT})


class TriggerKind(StrEnum):
    TURN_ENDED = "turn_ended"
    BEAD_CLOSED = "bead_closed"
    BEAD_PARKED = "bead_parked"
    APPROVAL_RESOLVED = "approval_resolved"
    BACKSTOP = "backstop"
    OPERATOR = "operator"
    STARTUP = "startup"


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    ref: str | None = None      # the message or event behind the trigger, for progress reactions


class Outcome(StrEnum):
    STARTED = "started"          # new work claimed and launched
    RESUMED = "resumed"          # a parked bead resumed
    BUSY = "busy"                # the coder role already has a session
    NOTHING = "nothing"          # nothing is ready and nothing is resumable
    HELD = "held"                # pickup is held (see the workstream's holds)


class Scheduler:
    def __init__(self, ws: WorkstreamSettings, deps: Deps, parker: Parker | None = None) -> None:
        self.ws = ws
        self.d = deps
        self.parker = parker or Parker(ws, deps)

    # --- entry point ---

    def pickup(self, trigger: Trigger) -> Outcome:
        with self.parker.entry():
            try:
                outcome = self._pickup(trigger)
            except BeadsUnavailable as exc:
                self.d.journal.hold(self.ws.name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
                outcome = Outcome.HELD
            self.publish()
            return outcome

    def publish(self) -> None:
        j, name = self.d.journal, self.ws.name
        try:
            paused = self.d.beads.paused(name)
        except BeadsUnavailable:
            paused = True        # can't read the flag: show the workstream as stopped, never as running
        j.set_ws_state(name, ws_state(paused, j.holds(name), (b.state for b in j.states(name))))

    def _pickup(self, trigger: Trigger) -> Outcome:
        j, name = self.d.journal, self.ws.name
        try:
            if not self.d.runtime.available():
                raise RuntimeUnavailable("not available")
            self.d.runtime.sessions(name)
        except RuntimeUnavailable:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        # Only pickup clears this hold (the park stop, release and the guard set it): the runtime has
        # answered both questions every launch needs, so nothing waits on it any more.
        j.unhold(name, Reason.RUNTIME_UNAVAILABLE)
        unresolved = self.d.reconciler.unresolved(name)
        j.unhold(name, Reason.BEADS_UNREACHABLE)      # beads answered
        if unresolved:
            j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
        else:
            j.unhold(name, Reason.ACTIONS_UNRECONCILED)
        try:
            sweep(self.parker)
            for op in j.ops_open(name):
                self.replay(op)
            if j.holds(name):
                return Outcome.HELD
            coder = self.d.runtime.sessions(name)
        except RuntimeUnavailable:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        if coder:
            return Outcome.BUSY
        for bead in self._resumable():
            result = self.parker.resume(bead, trigger.ref)
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.RESUMED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
        if self.d.beads.paused(name):
            return Outcome.NOTHING       # pausing stops new claims only (§4.3)
        for bead in self.d.beads.ready(name):
            if j.op_for(name, bead.id) is not None:
                continue
            try:
                result = self.start_new(bead, trigger.ref)
            except Paused:
                return Outcome.NOTHING
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.STARTED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
        return Outcome.NOTHING

    def _stalled(self) -> Outcome:
        return Outcome.HELD if self.d.journal.holds(self.ws.name) else Outcome.BUSY

    def _resumable(self) -> list[Bead]:
        """Parked beads of ours that are resumable now and that the journal has as waiting on blockers
        or input. A bead the journal has as HELD or STUCK is never resumed here, whatever its labels say:
        only the operator's release moves it on."""
        j, name = self.d.journal, self.ws.name
        rows = {r.bead: r.state for r in j.states(name)}
        return sorted((b for b in self.d.beads.ours(name)
                       if rows.get(b.id) in RESUMABLE_ROWS and resumable(b) and j.op_for(name, b.id) is None),
                      key=lambda b: b.id)

    # --- new work ---

    def start_new(self, bead: Bead, ref: str | None) -> Launch:
        j, name = self.d.journal, self.ws.name
        with j.transaction():
            op = j.op_open(OpKind.PICKUP, name, bead.id, {"ref": ref or "", "role": self.ws.coder_role})
            j.set_state(name, bead.id, BeadState.CLAIMING, ref=ref)
        self.d.cp("pickup.intent")
        try:
            self.d.gate.claim(name, bead.id)
        except Paused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            raise
        except ClaimRefused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            return Launch.ENDED
        except RoutingChanged:
            # btq claimed it but routing or the design gate changed: the claim stays (wsd never
            # unclaims), the bead is never executed, and a human decides.
            self.parker.escalate_from(op, Reason.ROUTING_CHANGED)
            return Launch.ENDED
        except ClaimUncertain:
            result = self.replay_pickup(op)
            row = j.state(name, bead.id)
            if row is not None and (row.state, row.reason) == (BeadState.DROPPED, Reason.CLAIM_ABANDONED):
                # The claim failed without landing: the queue is failing, not the bead. Hold pickup and let
                # the next trigger try again, rather than calling the workstream idle with work ready.
                raise BeadsUnavailable("claim did not land") from None
            return result
        self.d.cp("pickup.claimed!")
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return self._start(op)

    def _finish(self, op: Op, status: OpStatus, state: BeadState, reason: Reason | None = None,
                detail: str = "") -> None:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail, op.data.get("ref") or None)

    def resolve_claim(self, op: Op) -> Op | None:
        """A pickup journal at `intent`: read the claim back. Returns the operation at `claimed` if the
        claim is ours, or None if it was finished (not ours) or can't be read (held)."""
        j, name = self.d.journal, self.ws.name
        try:
            view = self.d.beads.read_claim(name, op.bead)
        except BeadsUnavailable as exc:
            j.hold(name, Reason.CLAIM_UNCERTAIN, op.bead)
            j.set_state(name, op.bead, BeadState.CLAIMING, Reason.CLAIM_UNCERTAIN, type(exc).__name__)
            return None
        j.unhold(name, Reason.CLAIM_UNCERTAIN)
        if view is ClaimView.FREE:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            return None
        if view is ClaimView.OTHER:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_LOST)
            return None
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return op

    def replay(self, op: Op) -> None:
        """Continue an open journal from the step it reached."""
        if op.kind is OpKind.PICKUP:
            self.replay_pickup(op)
        else:
            self.parker.replay(op)

    def replay_pickup(self, op: Op) -> Launch:
        if op.step == "intent":
            resolved = self.resolve_claim(op)
            if resolved is None:
                held = Reason.CLAIM_UNCERTAIN in self.d.journal.holds(self.ws.name)
                return Launch.WAIT if held else Launch.ENDED
            op = resolved
        return self._start(op)

    def _start(self, op: Op) -> Launch:
        j, name = self.d.journal, self.ws.name
        if op.step == "claimed":
            if op.data.get("role") != self.ws.coder_role:
                self.parker.escalate_from(op, Reason.CONFIG_INVALID,
                                          "the coder role changed during the pickup")
                return Launch.ENDED
            try:
                spot = place(self.ws, self.d.beads.show(name, op.bead))
                j.set_state(name, op.bead, BeadState.STARTING)
                path = self.d.beads.worktree(name, op.bead, spot.repo)
            except NotOurs:
                self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
                return Launch.ENDED
            except ConfigInvalid as exc:
                self.parker.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
                return Launch.ENDED
            except WorktreeConflict as exc:
                self.parker.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
                return Launch.ENDED
            self.d.cp("pickup.worktree!")
            rec = record(self.ws, spot)
            op = j.op_step(op.op_id, "worktree", {"worktree": str(path), "repo": rec.repo,
                                                  "profile": rec.profile, "session_key": rec.session_key})
            self.d.cp("pickup.worktree")
        return self.parker.launch(op, record_from(op))


def record_from(op: Op) -> SessionRecord:
    """The record a pickup decided on at its worktree step, from its journal: a replay launches what the
    pickup chose, role included, even if the configuration changed since."""
    data = op.data
    return SessionRecord(data["role"], data["profile"], data["session_key"], data["repo"], data["worktree"])
