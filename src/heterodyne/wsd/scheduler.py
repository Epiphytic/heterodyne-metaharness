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

Discovery and claiming agree: btq's claim refuses whatever its per-bead worker's own `ready()` would not
list, so a listed bead that worker can't claim (a `session:` pin on the workstream session), or one still
carrying wsd's park labels, is never claimed: it is recorded STUCK as UNCLAIMABLE, and pickup reports STUCK
rather than NOTHING while only such beads are ready. A claim refused for a bead that is still free is
recorded the same way. Such a row is dropped once the bead is no longer listed, and a bead that becomes
claimable is tried again.

A ready bead is open and unassigned: whoever held it gave it back to the queue, and claims are beads'
(§3.3). So a row the journal kept for it (STUCK after a lost claim, HELD, any other) is dropped as
"returned to the queue" in the transaction that opens the new claim. The operator's release can't apply
to it: it moves on only beads wsd still holds.

New work is journaled: intent (with the coder role it is for), claim (inside the claim gate), placement
(repository, worktree, profile and session key, recorded before any effect), worktree, then the launch
guard, which writes the launched-session record before launching. A replay creates the worktree and
launches exactly what the placement recorded, whatever the configuration says since; a coder role renamed
in the configuration before the placement escalates the pickup rather than mix the two. A claim with an
uncertain outcome is read back before anything else; while it can't be read back, the workstream is held.

An open operation whose bead bd confirms no longer exists can never finish: its sessions are stopped (an
unconfirmed stop holds, and the operation stays open), then it ends, with any hold it set, and the bead is
dropped.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from heterodyne.config import ConfigError
from heterodyne.wsd.accounts import AccountChanged, Chosen
from heterodyne.wsd.beads import (
    HELD,
    NEEDS_HUMAN,
    PARKED,
    Bead,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.gate import Paused
from heterodyne.wsd.headroom import Deadline, clamp_bound, epoch, wake_time
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.launches import LAUNCHED
from heterodyne.wsd.park import GUARD_STEPS, Launch, Parker, resumable
from heterodyne.wsd.runtime import RuntimeUnavailable
from heterodyne.wsd.states import TERMINAL, BeadState, Reason, allowed, ws_state
from heterodyne.wsd.sweep import sweep
from heterodyne.wsd.usage import decide
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, place, record

POINTS = ("lock.waiting", "pickup.intent", "gate.checked", "pickup.claimed!", "pickup.claimed",
          "pickup.placed", "pickup.worktree!", "pickup.worktree", "pickup.recorded!",
          *(f"pickup.{step}" for step in GUARD_STEPS))
RESUMABLE_ROWS = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT})
# Rows of unclaimed ready beads, dropped once the bead is no longer listed.
UNLISTED = frozenset({Reason.UNCLAIMABLE, Reason.ACCOUNT_REPOINTED})
KEPT = frozenset({BeadState.HELD, BeadState.STUCK})     # never counted in the wake time


class TriggerKind(StrEnum):
    TURN_ENDED = "turn_ended"
    BEAD_CLOSED = "bead_closed"
    BEAD_PARKED = "bead_parked"
    APPROVAL_RESOLVED = "approval_resolved"
    BACKSTOP = "backstop"
    OPERATOR = "operator"
    STARTUP = "startup"
    QUOTA_WAKE = "quota_wake"     # the wake time a pickup armed for quota waiters (AU-5 §3.6)


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    ref: str | None = None      # the message or event behind the trigger, for progress reactions


class Outcome(StrEnum):
    STARTED = "started"          # new work claimed and launched
    RESUMED = "resumed"          # a parked bead resumed
    BUSY = "busy"                # the coder role already has a session
    NOTHING = "nothing"          # nothing is ready and nothing is resumable
    STUCK = "stuck"              # only beads btq's claim can't take are ready; their rows say why
    HELD = "held"                # pickup is held (see the workstream's holds)
    DEFERRED = "deferred"        # nothing started, and only quota waits: a wake time is armed (`wake_at`)


type Verdict = Deadline | AccountChanged | None     # None: go on (an account, or not gated here)


@dataclass
class _Scan:
    """One pickup's quota bookkeeping: the deadline of every candidate it skipped."""
    now: int
    deadlines: list[int] = field(default_factory=list[int])


class Scheduler:
    def __init__(self, ws: WorkstreamSettings, deps: Deps, parker: Parker | None = None) -> None:
        self.ws = ws
        self.d = deps
        self.parker = parker or Parker(ws, deps)
        # The quota wake time the last pickup computed (§3.6), in UTC epoch seconds; never journaled.
        self.wake_at: int | None = None

    # --- entry point ---

    def pickup(self, trigger: Trigger) -> Outcome:
        with self.parker.entry():
            self.wake_at = None
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
        rows = [b for b in j.states(name) if b.state not in TERMINAL]
        all_quota = bool(rows) and all((b.state, b.reason) == (BeadState.PARKED, Reason.QUOTA) for b in rows)
        j.set_ws_state(name, ws_state(paused, j.holds(name), (b.state for b in rows),
                                      self.wake_at is not None, all_quota))

    def _pickup(self, trigger: Trigger) -> Outcome:
        j, name = self.d.journal, self.ws.name
        now = self.d.clock()
        j.clamp_deadlines(clamp_bound(now, self.ws.usage))       # §3.5: before any gate, the guard's too
        self.d.cp("usage.clamped")
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
        scan = _Scan(now)
        outcome = self._candidates(trigger, scan)
        if outcome not in (Outcome.NOTHING, Outcome.STUCK):
            return outcome
        self.wake_at = wake_time([*scan.deadlines, *self._waiters(now), *self._deferrals(now)], now)
        if outcome is Outcome.STUCK or self.wake_at is None:
            return outcome
        try:            # an uncertain launch above may have left its session listed: the role is taken
            if self.d.runtime.sessions(name):
                return self._stalled()
        except RuntimeUnavailable:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        return Outcome.DEFERRED

    def _candidates(self, trigger: Trigger, scan: _Scan) -> Outcome:
        """§5.2 step 7, each candidate gated on its own profile (AU-5 §3.6): resumable parked beads, then
        new ready work, until one starts. A candidate with no headroom is skipped, never claimed."""
        j, name = self.d.journal, self.ws.name
        for bead in self._resumable():
            verdict = self._gate_resume(bead, scan.now)
            if isinstance(verdict, Deadline):
                scan.deadlines.append(verdict.at)
                continue
            result = self.parker.resume(bead, trigger.ref)
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.RESUMED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
        if self.d.beads.paused(name):
            return Outcome.NOTHING       # pausing stops new claims only (§4.3)
        ready = self.d.beads.ready(name)
        self._forget_unlisted({b.id for b in ready})
        stuck = False
        for bead in ready:
            if j.op_for(name, bead.id) is not None:
                continue
            why = self._unclaimable(bead)
            if why:
                j.adopt(name, bead.id, BeadState.STUCK, Reason.UNCLAIMABLE, why)
                stuck = True
                continue
            verdict = self._gate_new(bead, scan.now)
            if isinstance(verdict, Deadline):
                scan.deadlines.append(verdict.at)
                continue
            if isinstance(verdict, AccountChanged):
                j.adopt(name, bead.id, BeadState.STUCK, Reason.ACCOUNT_REPOINTED, verdict.detail)
                stuck = True
                continue
            try:
                result = self.start_new(bead, trigger.ref)
            except Paused:
                return Outcome.NOTHING
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.STARTED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
            row = j.state(name, bead.id)
            stuck = stuck or (row is not None and row.reason is Reason.UNCLAIMABLE)
        return Outcome.STUCK if stuck else Outcome.NOTHING

    # --- the headroom gate at pickup (AU-5 §3.6) ---

    def _gate(self, bead: str, profile: str, session_key: str, now: int) -> Verdict | Chosen:
        """The gate for one candidate session, or None where pickup must not gate: no accounts, a session
        with an unresolved entry (dispatched, no outcome), an unsettled adoption, or a profile that can't
        be resolved. Those go to the guard, which reconciles first and escalates with its reason."""
        j, accounts = self.d.journal, self.ws.accounts
        adoption = j.adoption(self.ws.name, bead)
        entries = j.launches(session_key)
        if (accounts is None or (adoption is not None and not adoption.settled)
                or any(e.dispatched_at is not None and e.outcome is None for e in entries)):
            return None
        launched = [e for e in entries if e.outcome == LAUNCHED]
        try:
            return decide(accounts, j, profile, launched[-1].credential_key if launched else None, now,
                          self.ws.usage)
        except ConfigError:
            return None

    def _gate_resume(self, bead: Bead, now: int) -> Verdict | Chosen:
        """A resumable bead, gated on its recorded session's profile. AccountChanged goes on: the guard
        escalates a claimed bead (O6)."""
        try:
            rec = bead.record()
        except RecordUnreadable:
            return None
        return None if rec is None else self._gate(bead.id, rec.profile, rec.session_key, now)

    def _gate_new(self, bead: Bead, now: int) -> Verdict:
        """A ready bead, gated on the profile and session its placement gives. Its STUCK/ACCOUNT_REPOINTED
        row is dropped as soon as the gate gives anything else, before a Deadline is handled."""
        j, name = self.d.journal, self.ws.name
        try:
            spot = place(self.ws, bead)
        except ConfigInvalid:
            return None             # start_new escalates it
        verdict = self._gate(bead.id, spot.profile, spot.session_key, now)
        row = j.state(name, bead.id)
        if (row is not None and (row.state, row.reason) == (BeadState.STUCK, Reason.ACCOUNT_REPOINTED)
                and not isinstance(verdict, AccountChanged)):
            j.adopt(name, bead.id, BeadState.DROPPED, Reason.CLAIM_ABANDONED, "account restored")
        return verdict if isinstance(verdict, Deadline | AccountChanged) else None

    def _waiters(self, now: int) -> list[int]:
        """Each runnable PARKED/QUOTA waiter's current gate deadline, or `now + min_recheck_seconds` if it
        gates to an account or to `account_changed`, so the next pickup resumes or escalates it."""
        j, name = self.d.journal, self.ws.name
        rows = {r.bead for r in j.states(name) if (r.state, r.reason) == (BeadState.PARKED, Reason.QUOTA)}
        found: list[int] = []
        for bead in self.d.beads.ours(name):
            if bead.id in rows and resumable(bead) and j.op_for(name, bead.id) is None:
                verdict = self._gate_resume(bead, now)
                found.append(verdict.at if isinstance(verdict, Deadline)
                             else now + self.ws.usage.min_recheck_seconds)
        return found

    def _deferrals(self, now: int) -> list[int]:
        """The `defer_until` of each current coder quota deferral still after `now` (AU-4 writes them),
        except for beads the journal has HELD or STUCK, beads labelled `v2:held` or `needs-human`, and
        beads with open blockers."""
        j, name = self.d.journal, self.ws.name
        rows = {r.bead: r.state for r in j.states(name)}
        found: list[int] = []
        for d in j.deferrals_current(name, self.ws.coder_role):
            at = epoch(d.defer_until)
            if d.reason != "quota" or at is None or at <= now or rows.get(d.bead) in KEPT:
                continue
            bead = self.d.beads.show(name, d.bead)
            if HELD not in bead.labels and NEEDS_HUMAN not in bead.labels and not bead.open_blockers():
                found.append(at)
        return found

    def _unclaimable(self, bead: Bead) -> str:
        """Why btq's claim can't take a listed bead, or "" if it can."""
        if not self.d.beads.claimable(self.ws.name, bead):
            return "pinned to a btq session no per-bead worker has"
        if PARKED in bead.labels or HELD in bead.labels:
            return "unclaimed, but still labelled as parked by wsd"
        return ""

    def _forget_unlisted(self, listed: set[str]) -> None:
        """UNCLAIMABLE and ACCOUNT_REPOINTED rows of beads no longer listed as ready: nothing waits on them
        any more."""
        j, name = self.d.journal, self.ws.name
        for row in j.states(name):
            if (row.state is BeadState.STUCK and row.reason in UNLISTED and row.bead not in listed
                    and j.op_for(name, row.bead) is None):
                j.adopt(name, row.bead, BeadState.DROPPED, Reason.CLAIM_ABANDONED, "no longer ready")

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
            row = j.state(name, bead.id)
            if row is not None and not allowed(row.state, BeadState.CLAIMING):
                # Listed as ready, so open and unassigned: the claim this row describes was given back to
                # the queue, and beads are the truth for claims (§3.3).
                j.adopt(name, bead.id, BeadState.DROPPED, Reason.CLAIM_LOST, "returned to the queue")
            op = j.op_open(OpKind.PICKUP, name, bead.id, {"ref": ref or "", "role": self.ws.coder_role})
            j.set_state(name, bead.id, BeadState.CLAIMING, ref=ref)
        self.d.cp("pickup.intent")
        try:
            self.d.gate.claim(name, bead.id)
        except Paused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            raise
        except ClaimRefused as exc:
            # Nothing was written. Still free means btq won't let this worker take it (taken by someone
            # else is a lost race): recorded, so a bead that stays ready never reads as idle.
            resolved = self.resolve_claim(op, (BeadState.STUCK, Reason.UNCLAIMABLE, str(exc)))
            if resolved is not None:
                return self._start(resolved)
            return Launch.WAIT if Reason.CLAIM_UNCERTAIN in j.holds(name) else Launch.ENDED
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

    def resolve_claim(self, op: Op, free: tuple[BeadState, Reason, str] = (
            BeadState.DROPPED, Reason.CLAIM_ABANDONED, "")) -> Op | None:
        """A pickup journal at `intent`: read the claim back. Returns the operation at `claimed` if the
        claim is ours, or None if it was finished (not ours, or gone) or can't be read (held). A bead
        still free ends as `free` says."""
        j, name = self.d.journal, self.ws.name
        try:
            view = self.d.beads.read_claim(name, op.bead)
        except BeadsUnavailable as exc:
            j.hold(name, Reason.CLAIM_UNCERTAIN, op.bead)
            j.set_state(name, op.bead, BeadState.CLAIMING, Reason.CLAIM_UNCERTAIN, type(exc).__name__)
            return None
        if view is ClaimView.GONE:
            self._vanished(op)
            return None
        j.unhold(name, Reason.CLAIM_UNCERTAIN)
        if view is ClaimView.FREE:
            self._finish(op, OpStatus.ABANDONED, *free)
            return None
        if view is ClaimView.OTHER:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_LOST)
            return None
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return op

    def replay(self, op: Op) -> None:
        """Continue an open journal from the step it reached. A bead bd confirms is gone ends it."""
        try:
            if op.kind is OpKind.PICKUP:
                self.replay_pickup(op)
            else:
                self.parker.replay(op)
        except BeadsUnavailable:
            if self.d.beads.exists(self.ws.name, op.bead):
                raise
            self._vanished(op)

    def _vanished(self, op: Op) -> None:
        """bd confirms `op`'s bead no longer exists, so the operation can never finish. Nothing runs
        without its bead: its sessions are stopped first (RuntimeUnavailable propagates and the operation
        stays open), then the operation ends with the holds it set, and the bead is dropped."""
        j, name = self.d.journal, self.ws.name
        for session in self.d.runtime.sessions(name):
            if session.bead == op.bead:
                self.d.runtime.stop(session.key)
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.ABANDONED)
            holds = j.holds(name)
            for reason in (Reason.LAUNCH_UNCERTAIN, Reason.CLAIM_UNCERTAIN):
                if holds.get(reason) == op.bead:
                    j.unhold(name, reason)
            j.adopt(name, op.bead, BeadState.DROPPED, Reason.CLAIM_LOST, "the bead no longer exists")

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
        if op.step == "quota":
            return self.parker.finish_quota(op)     # a quota shelve's replay, before any other check
        if op.step == "claimed":
            if op.data.get("role") != self.ws.coder_role:
                self.parker.escalate_from(op, Reason.CONFIG_INVALID,
                                          "the coder role changed during the pickup")
                return Launch.ENDED
            try:
                spot = place(self.ws, self.d.beads.show(name, op.bead))
            except ConfigInvalid as exc:
                self.parker.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
                return Launch.ENDED
            rec = record(self.ws, spot)
            with j.transaction():      # the placement is the journal's before any effect depends on it
                op = j.op_step(op.op_id, "placed", {"worktree": rec.worktree, "repo": rec.repo,
                                                    "profile": rec.profile, "session_key": rec.session_key})
                j.set_state(name, op.bead, BeadState.STARTING)
            self.d.cp("pickup.placed")
        if op.step == "placed":
            try:
                self.d.beads.worktree(name, op.bead, Path(op.data["repo"]))
            except NotOurs:
                self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
                return Launch.ENDED
            except WorktreeConflict as exc:
                self.parker.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
                return Launch.ENDED
            self.d.cp("pickup.worktree!")
            op = j.op_step(op.op_id, "worktree")
            self.d.cp("pickup.worktree")
        # The guard verifies the recorded worktree itself before launching into it.
        return self.parker.launch(op, record_from(op))


def record_from(op: Op) -> SessionRecord:
    """The record a pickup decided on at its placement step, from its journal: a replay creates and
    launches what the pickup chose, role included, even if the configuration changed since."""
    data = op.data
    return SessionRecord(data["role"], data["profile"], data["session_key"], data["repo"], data["worktree"])
