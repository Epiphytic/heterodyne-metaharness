"""Startup recovery for one workstream, in the ADR's order (ADR 0001 §3.3, §4.3, §10). Recovery never
launches anything: it stops what must not run, rebuilds the journal from beads and the runtime, and
leaves every launch to pickup's launch guard.

1. journal integrity check: done when the Journal is opened (a corrupt journal never gets this far);
2. read beads and sessions: every bead held by one of this workstream's per-bead workers, found by
   assignee whatever its labels say, and every session the runtime may still be running;
3. reconcile actions: any action not settled (closed beads included) holds pickup until plan 5 checks
   its target;
4. resume park journals: replay every open park, release and escalation, and read back any claim a
   pickup left uncertain. Pickup and resume operations that would launch are left to pickup. An
   operation whose bead bd confirms no longer exists ends, as in pickup. A LAUNCH_UNCERTAIN or
   CLAIM_UNCERTAIN hold whose bead has no open operation left (a journal restored from a backup, or
   edited by hand: an operation that ends settles its own) is settled: the sweep reads that bead's claim
   and sessions from beads and the runtime, and holds whatever disagrees;
   4a. settle adoptions (AU-3 §3.2): an adopted legacy session's entry goes on its bead, and a held one's
   bead is escalated UNEXPECTED_STATE, taking over its open pickup or resume;
5. reconcile sessions (`sweep`, which every pickup also runs): a session whose bead is not ours
   (closed, claimed by another worker, gone) is stopped; a recorded running bead whose session is gone
   gets a resume operation that pickup will carry through the guard; anything beads and the journal
   disagree on is held as STUCK, never relaunched;
6. only then does the caller accept events.

A failed recovery opens nothing: BeadsUnavailable or RuntimeUnavailable anywhere fails it (`ok=False`)
with the matching hold, including a failure a step only records (a claim that can't be read back is
CLAIM_UNCERTAIN; a stop the runtime can't confirm is STOP_UNCONFIRMED), and the resume operations the
sweep finds are opened only once the whole pass has succeeded. Progress on journals already open, and the
sweep's per-bead records and escalations (which only ever hold), stay.

Doubt always holds: a bead the journal has no row for (a lost journal), a missing or unreadable session
record, `v2:held` without `v2:parked`, a parked bead with a session, a routing change, or a stop the
runtime can't confirm. Rows that are STUCK or HELD stay so until the operator's release.
"""

from dataclasses import dataclass

from heterodyne.wsd.beads import BeadsUnavailable, LaunchConflict, NotOurs
from heterodyne.wsd.journal import Op, OpKind
from heterodyne.wsd.launches import LaunchesUnreadable
from heterodyne.wsd.runtime import RuntimeUnavailable
from heterodyne.wsd.scheduler import Scheduler
from heterodyne.wsd.states import Reason
from heterodyne.wsd.sweep import open_resumes, sweep

RECOVERY_POINTS = ("lock.waiting", "recovery.read", "recovery.actions", "recovery.journals",
                   "recovery.sessions")


@dataclass(frozen=True)
class Recovered:
    ws: str
    ok: bool                 # False: the workstream stays held until a later recovery succeeds
    replayed: int = 0        # open journals replayed
    resumes: int = 0         # dead sessions handed to pickup as resume operations
    held: int = 0            # beads recovery escalated because beads and the journal disagree
    stopped: int = 0         # sessions stopped because their bead is not ours


def recover(sched: Scheduler) -> Recovered:
    j, name = sched.d.journal, sched.ws.name
    with sched.parker.entry():
        try:
            result = _Recovery(sched).run()
        except BeadsUnavailable as exc:
            j.hold(name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
            result = Recovered(name, ok=False)
        except RuntimeUnavailable as exc:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE, type(exc).__name__)
            result = Recovered(name, ok=False)
        else:
            j.unhold(name, Reason.BEADS_UNREACHABLE)
        sched.publish()
        return result


class _Recovery:
    def __init__(self, sched: Scheduler) -> None:
        self.s = sched
        self.d = sched.d
        self.j = sched.d.journal
        self.name = sched.ws.name

    def run(self) -> Recovered:
        d, j, name = self.d, self.j, self.name
        # 2. read beads and sessions
        d.beads.ours(name)
        d.runtime.sessions(name)
        d.cp("recovery.read")
        # 3. actions
        unresolved = d.reconciler.unresolved(name)
        if unresolved:
            j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
        else:
            j.unhold(name, Reason.ACTIONS_UNRECONCILED)
        d.cp("recovery.actions")
        # 4. journals that never launch
        replayed = 0
        held = {a.bead for a in j.adoptions_unsettled(name) if a.verdict == "held"}
        for op in j.ops_open(name):
            if op.kind is OpKind.ESCALATE and op.bead in held:
                continue            # 4a settles the held adoption and replays this escalation itself
            if op.kind is OpKind.PICKUP and op.step == "intent":
                if self.s.resolve_claim(op) is None and self._still_open(op):
                    raise BeadsUnavailable("the claim could not be read back")    # held CLAIM_UNCERTAIN
            elif op.kind in (OpKind.PARK, OpKind.RELEASE, OpKind.ESCALATE):
                self.s.replay(op)   # the scheduler's dispatch: a bead bd confirms is gone ends its op
                if self._stop_unconfirmed(op):
                    raise RuntimeUnavailable("a stop was not confirmed")
            else:
                continue            # a pickup past its claim, or a resume: pickup's guard carries it on
            replayed += 1
        self._settle_orphans()
        self._settle_adoptions()
        d.cp("recovery.journals")
        # 5. sessions, read again: the replays changed both
        swept = sweep(self.s.parker, defer=True)
        if swept.unconfirmed:
            raise RuntimeUnavailable("a stop was not confirmed")    # each bead is STUCK/STOP_UNCONFIRMED
        resumes = open_resumes(self.s.parker, swept.deferred)
        d.cp("recovery.sessions")
        return Recovered(name, ok=True, replayed=replayed, resumes=resumes, held=swept.held,
                         stopped=swept.stopped)

    def _still_open(self, op: Op) -> bool:
        found = self.j.op_for(self.name, op.bead)
        return found is not None and found.op_id == op.op_id

    def _stop_unconfirmed(self, op: Op) -> bool:
        """A park or release replay that couldn't confirm a stop holds the workstream and leaves its
        operation open with the bead's reason STOP_UNCONFIRMED (no row: nothing to record it on)."""
        if not self._still_open(op):
            return False
        row = self.j.state(self.name, op.bead)
        return row is None or row.reason is Reason.STOP_UNCONFIRMED

    def _settle_adoptions(self) -> None:
        """4a (AU-3 §3.2): each adopted entry goes on its bead, and each held bead is escalated, before
        the sweep can open a resume for it. A bead write that fails raises BeadsUnavailable, so no legacy
        session resumes before its entry is on the bead."""
        j, name = self.j, self.name
        for adoption in j.adoptions_unsettled(name):
            detail = adoption.detail
            if adoption.verdict == "adopted":
                entry = j.launch(adoption.session_key or "", 1)
                if entry is None:
                    detail = "the adopted entry is missing from the journal"
                else:
                    try:
                        self.d.beads.ensure_launch(name, adoption.bead, entry)
                    except NotOurs:
                        j.adoption_settle(name, adoption.bead)    # not ours: the sweep stops its session
                        self.d.cp("adopt.settled")
                        continue
                    except (LaunchConflict, LaunchesUnreadable) as exc:
                        # §3.2: recovery fails and pickup never runs; the row stays unsettled
                        raise BeadsUnavailable(f"the adopted entry can't go on the bead: {exc}") from None
                    else:
                        self.d.cp("adopt.appended!")
                        j.adoption_settle(name, adoption.bead)
                        self.d.cp("adopt.settled")
                        continue
            with j.transaction():
                esc = self.s.parker.escalation_for(adoption.bead, Reason.UNEXPECTED_STATE, detail)
                j.adoption_settle(name, adoption.bead)
            self.d.cp("adopt.settled")
            self.s.parker.replay_escalate(esc)

    def _settle_orphans(self) -> None:
        """End each uncertainty hold whose bead has no open operation left. Its operation would have
        settled it on ending; nothing else ever will, and pickup claims nothing while it is held."""
        j, name = self.j, self.name
        holds = j.holds(name)
        for reason in (Reason.LAUNCH_UNCERTAIN, Reason.CLAIM_UNCERTAIN):
            if reason in holds and j.op_for(name, holds[reason]) is None:
                j.unhold(name, reason)
