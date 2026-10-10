"""Every journaled bead operation but the claim (ADR 0001 §3.3, §4.3), and the one launch path.

- Park: intent, stop every session of the bead, WIP commit (SHA recorded), blocking edges, labels, bead
  comment. Blocking edges go on before `v2:parked`, and `v2:held` before `v2:parked`, so the bead is
  never `v2:parked` without what keeps it from being resumed.
- Resume: intent, remove `v2:parked`, then the launch guard. A replay that finds `v2:parked` already
  gone is its own completed unlabel, not a cancellation.
- Release (the operator's, plan 6): remove `v2:held` and `needs-human`, then either back to parked or
  on to a resume. It is the only way out of HELD or STUCK.
- Escalate: STUCK in the journal, then `needs-human` on the bead, replayed until it reads back.
- Defer (AU-4, D5): the deferral record is the intent; then stop every session, WIP commit, `v2:deferred`
  and the `wsd-defer` comment, with no blocking edge. The tail runs inside whichever op deferred the bead
  (a standalone PARK, a pickup's or resume's guard, an undefer, a re-gate or a release), found by its
  `defer.*` step names. Undefer gates the bead again, removes `v2:deferred` and launches through the guard.
- `launch`, the guard: the only call to `AgentRuntime.launch`, used by pickup, resume and their replays.

Every external effect is followed by a `<op>.<step>!` checkpoint, and every journal write by
`<op>.<step>`, so a test can crash on either side of each write. `entry()` is the workstream's one
lock: park, release, pickup and recovery all take it, so no two operations interleave.

wsd never unclaims: a parked or stuck bead stays `in_progress` under its per-bead worker.
BeadsUnavailable is never caught here: it propagates, the operation stays open, and the caller holds
the workstream until beads answer again (§10). RuntimeUnavailable is a hold too: it uses no failure
budget and never escalates.
"""

import contextlib
import threading
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from heterodyne.config import ConfigError
from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.accounts import DEFAULT, AccountChanged, Accounts
from heterodyne.wsd.beads import (
    DEFERRED,
    HELD,
    NEEDS_HUMAN,
    PARKED,
    Bead,
    ClaimView,
    LaunchConflict,
    NotOurs,
    RecordConflict,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.headroom import Deadline, quota_detail
from heterodyne.wsd.journal import DeferralRow, EntryConflict, Op, OpKind, OpStatus, now
from heterodyne.wsd.launches import (
    ABANDONED,
    LAUNCHED,
    MAX_GENERATION,
    LaunchEntry,
    LaunchesUnreadable,
    Receipt,
)
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable, Session
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.upgrade import (
    CODEX_THREAD_FIELD,
    adopted_entry,
    adoption_adapter,
    check_agreement,
    configured_at_upgrade,
)
from heterodyne.wsd.usage import admitted, decide, untrusted_defer_until
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, label, place, record

PARK_POINTS = ("lock.waiting", "park.intent", "park.stopped!", "park.stopped", "park.committed!",
               "park.committed", "park.blocked!", "park.blocked", "park.labelled!", "park.labelled",
               "park.commented!", "park.done")
# The launch guard's points after its plan 3 checks, as `<kind>.<step>` (AU-3 §4.2). A crash at one of
# UNRECEIPTED leaves a dispatched generation with no receipt, which holds and is never dispatched again.
GUARD_STEPS = ("entry", "entry!", "dispatched", "dispatched!", "launched!", "receipt", "outcome", "outcome!",
               "done")
UNRECEIPTED = ("dispatched", "dispatched!", "launched!")
# The defer tail's points (AU-4 §3.1), as `<kind>.<step>` for the op that contains it.
DEFER_STEPS = ("defer.recorded", "defer.stopped!", "defer.stopped", "defer.committed!", "defer.committed",
               "defer.labelled!", "defer.labelled", "defer.commented!", "defer.done")
UNDEFER_POINTS = ("undefer.intent", "undefer.gated!", "undefer.gated", "undefer.unlabelled!",
                  "undefer.unlabelled")
RESUME_POINTS = ("resume.intent", "resume.unlabelled!", "resume.unlabelled",
                 *(f"resume.{step}" for step in GUARD_STEPS))
RELEASE_POINTS = ("lock.waiting", "release.intent", "release.unlabelled!", "release.unlabelled",
                  "release.done")
# A deferred bead's release (AU-4 §3.5): the `regate` step, the over-mark's finish, then the tail's points.
RELEASE_REGATE_POINTS = ("release.regate", "release.regate.over!", *(f"release.{s}" for s in DEFER_STEPS))
ADOPTED_RELEASE_POINTS = ("release.adopted", "release.adopted!")
ESCALATE_POINTS = ("escalate.intent", "escalate.labelled!", "escalate.done")
PARK_MARK = "wsd-park: "
REASONS = {BeadState.HELD: Reason.HELD_BY_OPERATOR, BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR,
           BeadState.PARKED: Reason.BLOCKED_ON_BEAD}
RELEASABLE = frozenset({BeadState.HELD, BeadState.STUCK, BeadState.DEFERRED})   # DEFERRED: account_changed
# The deferral reasons and trusts (D5) as stored in `deferrals`.
QUOTA = Reason.QUOTA.value
ACCOUNT_CHANGED = Reason.ACCOUNT_CHANGED.value
TRUSTED = "trusted"
UNTRUSTED = "untrusted"
DEFER_MARK = "wsd-defer "
ACCOUNT_CHANGED_EVENT = "deferral_account_changed"
# Holds the guard settles itself from the session list instead of waiting on, but only an operation's
# own: the hold's detail names the bead whose launch was uncertain, and every other operation waits (D17).
GUARD_SETTLES = frozenset({Reason.LAUNCH_UNCERTAIN})


class Launch(StrEnum):
    """What the launch guard did."""
    STARTED = "started"        # launched now
    LIVE = "live"              # the recorded session was already running
    WAIT = "wait"              # not attempted: a hold applies or the role is taken; the operation stays open
    UNCERTAIN = "uncertain"    # attempted, outcome unknown: the role stays taken and the workstream holds
    FAILED = "failed"          # the runtime confirmed nothing started; the operation is retried later
    ENDED = "ended"            # the operation finished without a launch (shelved, stuck or claim lost)


class NotReleasable(Exception):
    """Release applies only to a HELD or STUCK bead, or a DEFERRED `account_changed` one, with no operation
    open on it."""


class NotDeferrable(Exception):
    """A standalone defer needs the bead's launched-session record and no operation open on it."""


class Regated(StrEnum):
    """What a re-gate of an `account_changed` wait did (AU-4 §3.4)."""
    OVER = "over"              # the gate gives an account: marked over, the next pickup undefers it
    REQUOTA = "requota"        # the gate gives a deadline: the next number, as a quota deferral
    UNCHANGED = "unchanged"    # still `account_changed`, or skipped: nothing written


@dataclass(frozen=True)
class RegateCounts:
    over: int = 0
    requota: int = 0
    unchanged: int = 0


def defer_mark(key: str, number: int) -> str:
    """The `wsd-defer` comment's mark: unique per (session, number); the trailing space ends the number."""
    return f"{DEFER_MARK}session={key} n={number} "


def defer_comment(rec: DeferralRow) -> str:
    """D5's one line, written once per deferral number."""
    until = "none" if rec.defer_until is None else \
        datetime.fromtimestamp(int(rec.defer_until), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{defer_mark(rec.session_key, rec.number)}role={rec.role} until={until} reason={rec.reason}"


def deferred_reason(rec: DeferralRow) -> tuple[Reason, str]:
    """A DEFERRED row's reason and detail for its current record."""
    if rec.reason == QUOTA and rec.defer_until is not None:
        return Reason.QUOTA, quota_detail(int(rec.defer_until))
    return Reason.ACCOUNT_CHANGED, "account changed"


def park_comment(op: Op) -> str:
    blockers = op.data.get("blockers") or "none"
    why = op.data.get("why") or "no reason given"
    return (f"Parked by wsd ({PARK_MARK}{op.op_id}). WIP commit {op.data.get('sha', '?')}. "
            f"Waiting on: {blockers}. Why: {why}.")


def parked_state(bead: Bead) -> BeadState:
    """What a `v2:parked` bead is waiting for, from its labels and open blockers."""
    if HELD in bead.labels:
        return BeadState.HELD
    if bead.waits_on_operator():
        return BeadState.WAITING_INPUT
    return BeadState.PARKED


def blocker_detail(bead: Bead) -> str:
    """The open blockers, for the state's detail: plan 6 counts "N beads on M approvals" from it."""
    return ",".join(sorted(d.id for d in bead.open_blockers()))


def resumable(bead: Bead) -> bool:
    """§4.3: claimed by wsd (the caller checked), `v2:parked`, every blocking edge closed. A bead held by
    the operator or escalated to a human is not resumable."""
    return (PARKED in bead.labels and HELD not in bead.labels and NEEDS_HUMAN not in bead.labels
            and not bead.open_blockers())


class Parker:
    def __init__(self, ws: WorkstreamSettings, deps: Deps) -> None:
        self.ws = ws
        self.d = deps
        self.lock = threading.RLock()

    @contextlib.contextmanager
    def entry(self) -> Generator[None]:
        """The workstream's operation lock. Every public entry point (park, release, pickup, recovery)
        takes it; the checkpoint before it lets a test hold one caller at the door."""
        self.d.cp("lock.waiting")
        with self.lock:
            yield

    # --- shared ---

    def _finish(self, op: Op, status: OpStatus, state: BeadState | None, reason: Reason | None = None,
                detail: str = "") -> None:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self._settle_uncertain(op)
            if state is not None:
                self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail,
                                         op.data.get("ref") or None)

    def _settle_uncertain(self, op: Op) -> None:
        """An operation that ends, however it ends, settles its own LAUNCH_UNCERTAIN hold: any session it
        may have started is listed by the runtime, and the sweep and the role check take it from there."""
        if self.d.journal.holds(self.ws.name).get(Reason.LAUNCH_UNCERTAIN) == op.bead:
            self.d.journal.unhold(self.ws.name, Reason.LAUNCH_UNCERTAIN)

    def _sessions(self, bead: str | None = None) -> list[Session]:
        found = self.d.runtime.sessions(self.ws.name)
        return [s for s in found if bead is None or s.bead == bead]

    def _runtime_hold(self, op: Op, reason: Reason = Reason.RUNTIME_UNAVAILABLE) -> BeadState:
        """Hold the workstream; the operation stays open at its step and uses no failure budget."""
        j = self.d.journal
        j.hold(self.ws.name, Reason.RUNTIME_UNAVAILABLE)
        row = j.state(self.ws.name, op.bead)
        if row is None:
            return BeadState.STUCK
        j.set_state(self.ws.name, op.bead, row.state, reason)
        return row.state

    # --- escalate ---

    def escalate_from(self, op: Op, reason: Reason, detail: str = "") -> None:
        """End `op` as STUCK and open the escalation in the same transaction, so a crash can't leave a
        stuck bead without its pending `needs-human`."""
        self._handoff(self._stuck(op, reason, detail))

    def _stuck(self, op: Op, reason: Reason, detail: str) -> Op:
        """The journal half of `escalate_from`; inside a caller's transaction it joins that one."""
        j = self.d.journal
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.STUCK)
            self._settle_uncertain(op)
            esc = j.op_open(OpKind.ESCALATE, self.ws.name, op.bead, {"reason": reason.value})
            j.set_state(self.ws.name, op.bead, BeadState.STUCK, reason, detail, op.data.get("ref") or None)
        return esc

    def _handoff(self, esc: Op) -> None:
        self.d.cp("escalate.intent")
        self.replay_escalate(esc)

    def _spend(self, op: Op, limit: int, reason: Reason, detail: str, forget: tuple[str, ...] = ()) -> bool:
        """Count one failed attempt of `op`. The attempt that exhausts the budget ends the operation as
        STUCK and opens its escalation in the same transaction as the count, so no restart can find an
        open operation with a spent budget and try its effect again. `forget` drops op data keys in the
        same transaction (a refused generation). True if it escalated."""
        j, ws = self.d.journal, self.ws.name
        with j.transaction():
            if forget:
                op = j.op_forget(op.op_id, *forget)
            if j.op_failed(op.op_id) >= limit:
                esc = self._stuck(op, reason, detail)
            else:
                esc = None
                row = j.state(ws, op.bead)
                j.set_state(ws, op.bead, row.state if row else BeadState.STUCK, reason, detail)
        if esc is None:
            return False
        self._handoff(esc)
        return True

    def escalate(self, bead: str, reason: Reason, detail: str = "") -> None:
        """Recovery's escalation: the journal row is rebuilt as STUCK whatever it said (beads are the
        truth, and they contradict it), then `needs-human` goes on the bead."""
        j = self.d.journal
        with j.transaction():
            esc = j.op_open(OpKind.ESCALATE, self.ws.name, bead, {"reason": reason.value})
            j.adopt(self.ws.name, bead, BeadState.STUCK, reason, detail)
        self.d.cp("escalate.intent")
        self.replay_escalate(esc)

    def escalation_for(self, bead: str, reason: Reason, detail: str) -> Op:
        """The journal half of escalating `bead` whatever is open on it, inside the caller's transaction
        (AU-3 §3.2): its open operation ends STUCK and hands off to a new escalation, an open escalation
        is kept as it is, and with nothing open one is opened. Never a second open operation."""
        j, ws = self.d.journal, self.ws.name
        with j.transaction():
            op = j.op_for(ws, bead)
            if op is None:
                esc = j.op_open(OpKind.ESCALATE, ws, bead, {"reason": reason.value})
                j.adopt(ws, bead, BeadState.STUCK, reason, detail)
            elif op.kind is OpKind.ESCALATE:
                esc = op
            else:
                esc = self._stuck(op, reason, detail)
        return esc

    def replay_escalate(self, op: Op) -> None:
        try:
            self.d.beads.ensure_label(self.ws.name, op.bead, NEEDS_HUMAN)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, None)      # the row already says STUCK
            return
        self.d.cp("escalate.labelled!")
        self._finish(op, OpStatus.DONE, None)
        self.d.cp("escalate.done")

    # --- park ---

    def park(self, bead: str, blockers: tuple[str, ...], why: str = "", hold: bool = False,
             ref: str | None = None) -> BeadState:
        """Park a bead wsd runs. `blockers` are the beads it waits on; `hold` parks it for the operator
        (/stop) until they release it. Returns the bead's state afterwards (PARKING if a step must be
        retried). Raises OpConflict if another operation is open on the bead: the caller retries after
        the next pickup, it never runs beside it."""
        if not blockers and not hold:
            raise ValueError("a park needs a blocker or an operator hold")
        with self.entry():
            j = self.d.journal
            with j.transaction():
                op = j.op_open(OpKind.PARK, self.ws.name, bead,
                               {"blockers": ",".join(blockers), "why": why, "hold": "1" if hold else "",
                                "ref": ref or ""})
                j.set_state(self.ws.name, bead, BeadState.PARKING, ref=ref)
            self.d.cp("park.intent")
            return self.replay_park(op)

    def replay_park(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        if op.attempts >= self.ws.limits.park_attempts_before_human:      # a budget spent before a crash
            self.escalate_from(op, Reason.PARK_FAILED, "the park's attempts are spent")
            return BeadState.STUCK
        try:
            if op.step == "intent":
                for session in self._sessions(bead):
                    self.d.runtime.stop(session.key)
                self.d.cp("park.stopped!")
                op = j.op_step(op.op_id, "stopped")
                self.d.cp("park.stopped")
            if op.step == "stopped":
                # The worktree's provenance says nothing about who holds the bead now: never commit into
                # the worktree of a bead that is no longer ours.
                if self.d.beads.read_claim(ws, bead) is not ClaimView.OURS:
                    raise NotOurs(bead)
                rec = self.d.beads.show(ws, bead).record()
                if rec is None:
                    self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "no session record names the worktree")
                    return BeadState.STUCK
                worktree = self.d.beads.verify_worktree(ws, bead, Path(rec.repo), Path(rec.worktree))
                sha = gitwip.wip_commit(gitwip.pin(Path(rec.repo), worktree, f"btq/{bead}"), op.op_id,
                                       f"parked {bead}")
                self.d.cp("park.committed!")
                op = j.op_step(op.op_id, "committed", {"sha": sha})
                self.d.cp("park.committed")
            if op.step == "committed":
                for blocker in filter(None, op.data.get("blockers", "").split(",")):
                    self.d.beads.ensure_blocker(ws, bead, blocker)
                self.d.cp("park.blocked!")
                op = j.op_step(op.op_id, "blocked")
                self.d.cp("park.blocked")
            if op.step == "blocked":
                if op.data.get("hold"):
                    self.d.beads.ensure_label(ws, bead, HELD)
                self.d.beads.ensure_label(ws, bead, PARKED)
                self.d.cp("park.labelled!")
                op = j.op_step(op.op_id, "labelled")
                self.d.cp("park.labelled")
            self.d.beads.ensure_comment(ws, bead, f"{PARK_MARK}{op.op_id}", park_comment(op))
            self.d.cp("park.commented!")
            shown = self.d.beads.show(ws, bead)
            final = parked_state(shown)
            self._finish(op, OpStatus.DONE, final, REASONS[final], blocker_detail(shown))
            self.d.cp("park.done")
            return final
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return BeadState.STUCK
        except RecordUnreadable:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "the session record does not parse")
            return BeadState.STUCK
        except WorktreeConflict as exc:
            self.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
            return BeadState.STUCK
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        except gitwip.GitFailed as exc:
            if self._spend(op, self.ws.limits.park_attempts_before_human, Reason.PARK_FAILED, str(exc)):
                return BeadState.STUCK
            return BeadState.PARKING

    # --- defer (AU-4 §3.1, §3.2) ---

    def defer(self, bead: str, reason: str, until: int | None, trust: str = TRUSTED,
              ref: str | None = None) -> BeadState:
        """The standalone defer (entry 1): the session-reported path's entry point, and AU-7's later. An
        `untrusted` until is clamped by D5's rule before it is recorded. Raises NotDeferrable."""
        with self.entry():
            return self.open_defer(bead, reason, until, trust, ref)

    def open_defer(self, bead: str, reason: str, until: int | None, trust: str = TRUSTED,
                   ref: str | None = None) -> BeadState:
        """`defer` for a caller that already holds `entry()` (pickup's due resume, recovery's conversion):
        a PARK op opened at `defer.recorded` with the next record, then the tail."""
        j, ws = self.d.journal, self.ws.name
        if reason not in (QUOTA, ACCOUNT_CHANGED) or (reason == QUOTA) != (until is not None or
                                                                         trust == UNTRUSTED):
            raise ValueError(f"no such deferral: {reason} until {until}")
        if trust == UNTRUSTED:
            until = untrusted_defer_until(until, self.d.clock(), self.ws.usage)
        try:
            rec = self.d.beads.show(ws, bead).record()
        except RecordUnreadable:
            rec = None
        if rec is None:
            raise NotDeferrable(f"{bead} has no readable launched-session record")
        with j.transaction():
            if j.op_for(ws, bead) is not None:
                raise NotDeferrable(f"{bead} has an operation open")
            op = j.op_open(OpKind.PARK, ws, bead, {"ref": ref or ""})
            op = self._record_defer(op, rec, reason, until, trust)
        self.d.cp("park.defer.recorded")
        return self.defer_tail(op)

    def _record_defer(self, op: Op, rec: SessionRecord | DeferralRow, reason: str, until: int | None,
                      trust: str = TRUSTED) -> Op:
        """Recording is the intent, in one transaction (joined by the caller's): the next record of `rec`'s
        session (its key, role and profile), the op's `defer.recorded` step naming it, and the row in
        PARKING. A replay continues with this number."""
        j = self.d.journal
        with j.transaction():
            n = j.deferral_next(rec.session_key)
            j.deferral_insert(DeferralRow(rec.session_key, n, op.bead, rec.role, rec.profile, reason,
                                          None if until is None else str(until), trust))
            op = j.op_step(op.op_id, "defer.recorded", {"defer": str(n), "defer_key": rec.session_key})
            j.set_state(self.ws.name, op.bead, BeadState.PARKING, ref=op.data.get("ref") or None)
        return op

    def _defer_within(self, op: Op, rec: SessionRecord | DeferralRow, reason: str,
                      until: int | None) -> BeadState:
        """Move `op` to the tail with the next record (the guard, an undefer, a re-gate in a release)."""
        op = self._record_defer(op, rec, reason, until)
        self.d.cp(f"{op.kind.value}.defer.recorded")
        return self.defer_tail(op)

    def defer_tail(self, op: Op) -> BeadState:
        """The steps after the intent, and their replay from any `defer.*` step (§3.1). The op ends DONE
        for a PARK or RELEASE op and ABANDONED for a pickup, resume or undefer."""
        ws, bead, j, kind = self.ws.name, op.bead, self.d.journal, op.kind.value
        key, n = op.data["defer_key"], int(op.data["defer"])
        if op.attempts >= self.ws.limits.park_attempts_before_human:      # a budget spent before a crash
            self.escalate_from(op, Reason.PARK_FAILED, "the defer's attempts are spent")
            return BeadState.STUCK
        try:
            if op.step == "defer.recorded":
                for session in self._sessions(bead):
                    self.d.runtime.stop(session.key)
                if self._sessions(bead):
                    return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
                self.d.cp(f"{kind}.defer.stopped!")
                op = j.op_step(op.op_id, "defer.stopped")
                self.d.cp(f"{kind}.defer.stopped")
            if op.step == "defer.stopped":
                if self.d.beads.read_claim(ws, bead) is not ClaimView.OURS:
                    raise NotOurs(bead)
                rec = self.d.beads.show(ws, bead).record()
                if rec is None:
                    self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "no session record names the worktree")
                    return BeadState.STUCK
                worktree = self.d.beads.verify_worktree(ws, bead, Path(rec.repo), Path(rec.worktree))
                sha = gitwip.wip_commit(gitwip.pin(Path(rec.repo), worktree, f"btq/{bead}"),
                                       f"defer:{key}:{n}", f"deferred {bead}")
                self.d.cp(f"{kind}.defer.committed!")
                op = j.op_step(op.op_id, "defer.committed", {"sha": sha})
                self.d.cp(f"{kind}.defer.committed")
            if op.step == "defer.committed":
                self.d.beads.ensure_label(ws, bead, DEFERRED)
                self.d.cp(f"{kind}.defer.labelled!")
                op = j.op_step(op.op_id, "defer.labelled")
                self.d.cp(f"{kind}.defer.labelled")
            current = j.deferral_current(key)
            if current is None or current.number != n:
                self.escalate_from(op, Reason.UNEXPECTED_STATE, f"deferral {n} is not the session's current")
                return BeadState.STUCK
            self.d.beads.ensure_comment(ws, bead, defer_mark(key, n), defer_comment(current))
            self.d.cp(f"{kind}.defer.commented!")
            final = self._finish_defer(op, current, self.d.beads.show(ws, bead))
            self.d.cp(f"{kind}.defer.done")
            return final
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return BeadState.STUCK
        except RecordUnreadable:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "the session record does not parse")
            return BeadState.STUCK
        except WorktreeConflict as exc:
            self.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
            return BeadState.STUCK
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        except gitwip.GitFailed as exc:
            if self._spend(op, self.ws.limits.park_attempts_before_human, Reason.PARK_FAILED, str(exc)):
                return BeadState.STUCK
            return BeadState.PARKING

    def _finish_defer(self, op: Op, rec: DeferralRow, shown: Bead) -> BeadState:
        """One transaction: the stale over-mark goes, the op ends, the row takes its final state (by
        precedence: `needs-human`, `v2:held`, then DEFERRED) and an `account_changed` record's one alert."""
        j = self.d.journal
        if NEEDS_HUMAN in shown.labels:
            final, reason, detail = BeadState.STUCK, Reason.NEEDS_HUMAN, ""
        elif HELD in shown.labels:
            final, reason, detail = BeadState.HELD, REASONS[BeadState.HELD], ""
        else:
            final = BeadState.DEFERRED
            reason, detail = deferred_reason(rec)
        done = op.kind in (OpKind.PARK, OpKind.RELEASE)
        with j.transaction():
            if rec.number > 1:
                j.drop_over(rec.session_key, rec.number - 1)
            self._finish(op, OpStatus.DONE if done else OpStatus.ABANDONED, final, reason, detail)
            if rec.reason == ACCOUNT_CHANGED:
                j.emit(self.ws.name, op.bead, ACCOUNT_CHANGED_EVENT, ref=f"{rec.session_key}:{rec.number}")
        return final

    def convert_quota(self, op: Op) -> Launch:
        """§6: an op left at AU-5's `quota` step becomes the guard's defer, with the record's `until` from
        the step's data, in one transaction with its `defer.recorded` step; then the tail."""
        try:
            rec = self.d.beads.show(self.ws.name, op.bead).record()
        except RecordUnreadable:
            rec = None
        if rec is None:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "no session record to defer")
            return Launch.ENDED
        self._defer_within(op, rec, QUOTA, int(op.data["until"]))
        return Launch.ENDED

    def current_deferral(self, shown: Bead) -> DeferralRow | None:
        """The current deferral record of the bead's recorded session."""
        try:
            rec = shown.record()
        except RecordUnreadable:
            return None
        return None if rec is None else self.d.journal.deferral_current(rec.session_key)

    # --- undefer (AU-4 §3.3) ---

    def undefer(self, bead: str, ref: str | None = None) -> Launch:
        """Undefer a DEFERRED bead whose current deferral is over (pickup holds `entry()`): a RESUME op that
        names the number, then the gate, `v2:deferred` removed and the guard."""
        j, ws = self.d.journal, self.ws.name
        current = self.current_deferral(self.d.beads.show(ws, bead))
        if current is None:
            raise NotDeferrable(f"{bead} has no deferral record")
        with j.transaction():
            op = j.op_open(OpKind.RESUME, ws, bead, {"ref": ref or "", "undefer": str(current.number),
                                                     "defer_key": current.session_key})
            j.set_state(ws, bead, BeadState.RESUMING, ref=ref)
        self.d.cp("undefer.intent")
        return self.replay_undefer(op)

    def replay_undefer(self, op: Op) -> Launch:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        try:
            if op.step == "intent":
                ended = self._undefer_gate(op)
                if ended is not None:
                    return ended
                op = j.op_step(op.op_id, "gated")
                self.d.cp("undefer.gated")
            if op.step == "gated":
                self.d.beads.ensure_label(ws, bead, DEFERRED, present=False)
                self.d.cp("undefer.unlabelled!")
                op = j.op_step(op.op_id, "unlabelled")
                self.d.cp("undefer.unlabelled")
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return Launch.ENDED
        shown = self.d.beads.show(ws, bead)
        if PARKED in shown.labels:           # plan 3's parked resume takes it once its blockers close
            final = parked_state(shown)
            self._finish(op, OpStatus.DONE, final, REASONS[final], blocker_detail(shown))
            return Launch.ENDED
        return self.launch(op)

    def _undefer_gate(self, op: Op) -> Launch | None:
        """The intent's checks, in order, then the gate. None: an account, go on to `gated`."""
        j, ws, bead = self.d.journal, self.ws.name, op.bead
        key, n = op.data["defer_key"], int(op.data["undefer"])
        current = j.deferral_current(key)
        shown = self.d.beads.show(ws, bead)
        if current is None or current.number != n:          # superseded meanwhile
            self._finish_deferred(op, current)
            return Launch.ENDED
        if NEEDS_HUMAN in shown.labels:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.NEEDS_HUMAN)
            return Launch.ENDED
        if HELD in shown.labels:
            self._finish(op, OpStatus.ABANDONED, BeadState.HELD, REASONS[BeadState.HELD])
            return Launch.ENDED
        if shown.open_blockers() and PARKED not in shown.labels:
            self._finish_deferred(op, current)
            return Launch.ENDED
        verdict = self._regate_verdict(current)
        if isinstance(verdict, ConfigError):
            self.escalate_from(op, Reason.CONFIG_INVALID, str(verdict))
            return Launch.ENDED
        if verdict is None:
            self.d.cp("undefer.gated!")
            return None
        if isinstance(verdict, Deadline):
            self._defer_within(op, current, QUOTA, verdict.at)
        elif current.reason == QUOTA:
            self._defer_within(op, current, ACCOUNT_CHANGED, None)
        else:                                               # still `account_changed`: its mark was stale
            with j.transaction():
                j.drop_over(key, n)
                self._finish_deferred(op, current)
        return Launch.ENDED

    def _finish_deferred(self, op: Op, current: DeferralRow | None) -> None:
        """End `op` ABANDONED with the row back in DEFERRED, still waiting on its current record."""
        reason, detail = (Reason.ACCOUNT_CHANGED, "account changed") if current is None else \
            deferred_reason(current)
        self._finish(op, OpStatus.ABANDONED, BeadState.DEFERRED, reason, detail)

    def _regate_verdict(self, current: DeferralRow) -> Deadline | AccountChanged | ConfigError | None:
        """The gate on the record's profile and the session's previous key: None for an account. An
        unresolvable profile is the guard's CONFIG_INVALID (returned, for the caller to place)."""
        accounts = self.ws.accounts
        if accounts is None:
            return ConfigError("no account can be pinned for the profile")
        try:
            previous = self._previous_key(current.session_key)
            verdict = decide(accounts, self.d.journal, current.profile, previous, self.d.clock(),
                             self.ws.usage)
        except ConfigError as exc:
            return exc
        return verdict if isinstance(verdict, Deadline | AccountChanged) else None

    # --- re-gate (AU-4 §3.4) ---

    def regate(self, bead: str, within: Op | None = None) -> Regated:
        """Re-gate the bead's current `account_changed` wait. Without `within` (process start, reload) a
        bead already marked over, with an open op, or held, needing a human, HELD or STUCK is skipped
        without gating; within a release the outcome finishes that RELEASE op (§3.5)."""
        j, ws = self.d.journal, self.ws.name
        shown = self.d.beads.show(ws, bead)
        current = self.current_deferral(shown)
        if current is None or current.reason != ACCOUNT_CHANGED:
            return Regated.UNCHANGED
        if within is None:
            row = j.state(ws, bead)
            if (j.is_over(current.session_key, current.number) or j.op_for(ws, bead) is not None
                    or HELD in shown.labels or NEEDS_HUMAN in shown.labels
                    or row is None or row.state is not BeadState.DEFERRED):
                return Regated.UNCHANGED
        verdict = self._regate_verdict(current)
        if verdict is None:
            with j.transaction():
                j.mark_over(current.session_key, current.number)
                if within is not None:
                    self._finish(within, OpStatus.DONE, BeadState.DEFERRED, *deferred_reason(current))
            if within is not None:
                self.d.cp("release.regate.over!")
            return Regated.OVER
        if isinstance(verdict, Deadline):
            if within is not None:
                self._defer_within(within, current, QUOTA, verdict.at)
            else:
                with j.transaction():
                    op = j.op_open(OpKind.PARK, ws, bead, {"ref": ""})
                    op = self._record_defer(op, current, QUOTA, verdict.at)
                self.d.cp("park.defer.recorded")
                self.defer_tail(op)
            return Regated.REQUOTA
        if within is not None:                  # still `account_changed`, or a profile that can't resolve
            self._finish(within, OpStatus.DONE, BeadState.DEFERRED, *deferred_reason(current))
        return Regated.UNCHANGED

    # --- resume ---

    def resume(self, bead: Bead, ref: str | None = None) -> Launch:
        """Resume a resumable parked bead as its recorded session in its recorded worktree. The caller
        (pickup) holds `entry()`."""
        j = self.d.journal
        with j.transaction():
            op = j.op_open(OpKind.RESUME, self.ws.name, bead.id, {"ref": ref or ""})
            j.set_state(self.ws.name, bead.id, BeadState.RESUMING, ref=ref)
        self.d.cp("resume.intent")
        return self.replay_resume(op)

    def replay_resume(self, op: Op) -> Launch:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        if op.step.startswith("defer."):
            self.defer_tail(op)
            return Launch.ENDED
        if op.step == "quota":
            return self.convert_quota(op)
        if "undefer" in op.data:
            return self.replay_undefer(op)
        if op.step == "intent":
            shown = self.d.beads.show(ws, bead)
            # `v2:parked` already gone is this operation's own unlabel, done before a crash: go on. The
            # guard below re-checks everything that would make launching wrong.
            if PARKED in shown.labels:
                if NEEDS_HUMAN in shown.labels:
                    self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.NEEDS_HUMAN)
                    return Launch.ENDED
                if not resumable(shown):         # it stopped being resumable meanwhile
                    final = parked_state(shown)
                    self._finish(op, OpStatus.ABANDONED, final, REASONS[final], blocker_detail(shown))
                    return Launch.ENDED
                try:
                    self.d.beads.ensure_label(ws, bead, PARKED, present=False)
                except NotOurs:
                    self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
                    return Launch.ENDED
            self.d.cp("resume.unlabelled!")
            op = j.op_step(op.op_id, "unlabelled")
            self.d.cp("resume.unlabelled")
        return self.launch(op)

    # --- release ---

    def release(self, bead: str, ref: str | None = None) -> BeadState:
        """The operator's release of a HELD or STUCK bead (plan 6). Removes `v2:held` and `needs-human`;
        a parked bead goes back to waiting on its blockers, any other goes on to a resume through the
        launch guard at the next pickup. An external removal of either label never does this: the
        journal keeps the bead HELD or STUCK until release runs."""
        with self.entry():
            j, ws = self.d.journal, self.ws.name
            with j.transaction():
                row = j.state(ws, bead)
                if row is None or row.state not in RELEASABLE or j.op_for(ws, bead) is not None:
                    raise NotReleasable(bead)
                if row.state is BeadState.DEFERRED and row.reason is not Reason.ACCOUNT_CHANGED:
                    raise NotReleasable(f"{bead} waits on quota")      # nothing for the operator to release
                self._refuse_unverifiable(bead)
                op = j.op_open(OpKind.RELEASE, ws, bead, {"ref": ref or ""})
                if row.state is BeadState.DEFERRED:       # nothing to unlabel: straight to `regate`
                    op = j.op_step(op.op_id, "regate")
            self.d.cp("release.regate" if op.step == "regate" else "release.intent")
            return self.replay_release(op)

    def _refuse_unverifiable(self, bead: str) -> None:
        """Holds no AU-3 release can resolve (§3.3, §4.3): a dispatched generation with no receipt, and an
        adoption held while accounts were configured for the session's adapter at the upgrade."""
        j, ws = self.d.journal, self.ws.name
        for entry in j.launches_of(ws, bead):
            if entry.dispatched_at is not None and entry.outcome is None and j.receipt(*entry.ident) is None:
                raise NotReleasable(f"generation {entry.generation} has no launch receipt")
        adoption = j.adoption(ws, bead)
        if adoption is not None and adoption.unresolved_hold:
            adapter = adoption_adapter(adoption)
            if configured_at_upgrade(adoption.facts, adapter):
                raise NotReleasable(f"accounts were configured for {adapter or 'an adapter'} at the upgrade")

    def replay_release(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        if op.step.startswith("defer."):
            return self.defer_tail(op)
        if op.step == "regate":
            return self._release_regate(op)
        try:
            if op.step == "intent":
                self.d.beads.ensure_label(ws, bead, HELD, present=False)
                self.d.beads.ensure_label(ws, bead, NEEDS_HUMAN, present=False)
                self.d.cp("release.unlabelled!")
                op = j.op_step(op.op_id, "unlabelled")
                self.d.cp("release.unlabelled")
            shown = self.d.beads.show(ws, bead)
            failed = self._resolve_adoption(op, shown)
            if failed is not None:
                self.escalate_from(op, Reason.UNEXPECTED_STATE, f"adoption still unverified: {failed}")
                return BeadState.STUCK
            if DEFERRED in shown.labels:          # never removed by a release: re-gated instead (§3.5)
                current = self.current_deferral(shown)
                if current is None:
                    self.escalate_from(op, Reason.UNEXPECTED_STATE, "deferred without a deferral record")
                    return BeadState.STUCK
                with j.transaction():
                    op = j.op_step(op.op_id, "regate")
                    j.set_state(ws, bead, BeadState.DEFERRED, *deferred_reason(current),
                                ref=op.data.get("ref") or None)
                self.d.cp("release.regate")
                return self._release_regate(op)
            if PARKED in shown.labels:
                final = parked_state(shown)
                self._finish(op, OpStatus.DONE, final, REASONS[final], blocker_detail(shown))
                self.d.cp("release.done")
                return final
            self._record_for_release(shown)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return BeadState.STUCK
        except ConfigInvalid as exc:
            self.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
            return BeadState.STUCK
        except RecordConflict:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, "the session record names another launch")
            return BeadState.STUCK
        except (LaunchConflict, LaunchesUnreadable, EntryConflict) as exc:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, f"adoption still unverified: {exc}")
            return BeadState.STUCK
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.DONE)
            nxt = j.op_open(OpKind.RESUME, ws, bead, {"ref": op.data.get("ref", "")})
            j.op_step(nxt.op_id, "unlabelled")
            j.set_state(ws, bead, BeadState.RESUMING, ref=op.data.get("ref") or None)
        self.d.cp("release.done")
        return BeadState.RESUMING

    def _release_regate(self, op: Op) -> BeadState:
        """The `regate` step (§3.5): a quota wait finishes as it is; an `account_changed` wait is re-gated
        within this op, which settles over-marked, with the next quota record, or unchanged. Never a
        launch, and `v2:deferred` stays."""
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        current = self.current_deferral(self.d.beads.show(ws, bead))
        if current is None:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, "deferred without a deferral record")
            return BeadState.STUCK
        if current.reason == ACCOUNT_CHANGED:
            self.regate(bead, within=op)
        else:
            self._finish(op, OpStatus.DONE, BeadState.DEFERRED, *deferred_reason(current))
        self.d.cp("release.done")
        row = j.state(ws, bead)
        return BeadState.STUCK if row is None else row.state

    def _resolve_adoption(self, op: Op, shown: Bead) -> str | None:
        """§3.3: before plan 3's parked/unparked branch, a held adoption is re-verified with today's facts
        and, if it passes, resolved: the adopted entry and the resolution in one transaction, then the
        bead copy. A replay finds the row resolved by this op and re-runs only the bead write. Returns
        the failed condition, or None to go on."""
        j, ws, bead = self.d.journal, self.ws.name, op.bead
        adoption = j.adoption(ws, bead)
        if adoption is None or (adoption.resolution is not None and adoption.resolved_by != op.op_id):
            return None
        if adoption.resolution is None:
            if not adoption.unresolved_hold:
                return None
            self._record_for_release(shown)
            shown = self.d.beads.show(ws, bead)
            entry, failed = self._reverify(op, shown)
            if entry is None:
                return failed
            with j.transaction():
                j.launch_insert(entry)
                j.adoption_resolve(ws, bead, op.op_id)
                j.emit(ws, bead, "adoption_resolved", ref=op.op_id)
            self.d.cp("release.adopted")
        [entry] = [e for e in j.launches_of(ws, bead) if e.adopted]
        self._bead_write(entry, "release.adopted!")
        return None

    def _reverify(self, op: Op, shown: Bead) -> tuple[LaunchEntry | None, str | None]:
        """Conditions (a), (b), (d) and (e) of §3 step 2, read now; (c) was checked at intent."""
        accounts, ws = self.ws.accounts, self.ws.name
        try:
            rec = shown.record()
        except RecordUnreadable:
            return None, "the session record does not parse"
        if rec is None:
            return None, "no session record"
        if rec.session_key != ids.role_session(op.bead, rec.role, rec.profile):
            return None, "the record's session key is not the bead's role session"
        adapter = None if accounts is None else accounts.adapter(rec.profile)
        if accounts is None or adapter is None:
            return None, f"profile {rec.profile} no longer exists"
        try:
            key = accounts.current_key(adapter, DEFAULT)
        except ConfigError:
            key = None
        if key is None or not accounts.login_resolves(adapter, DEFAULT):
            return None, f"the {adapter} default login does not resolve"
        others = self.d.journal.op_for(ws, op.bead)
        open_op = None if others is None or others.op_id == op.op_id else (others.kind.value, others.step)
        try:
            launches: tuple[LaunchEntry, ...] | None = shown.launches().entries
        except LaunchesUnreadable:
            launches = None
        entry = adopted_entry(ws, op.bead, rec, adapter, key, now(), shown.record_field(CODEX_THREAD_FIELD))
        return check_agreement(self.d.beads.read_claim(ws, op.bead), open_op, launches, entry)

    def _record_for_release(self, shown: Bead) -> None:
        """An unparked bead resumes through the guard, which needs a launched-session record. One that
        is missing or unreadable is replaced from the current placement, but only after every session
        of the bead under another key is confirmed stopped: nothing unrecorded keeps running."""
        try:
            rec = shown.record()
        except RecordUnreadable:
            rec = None
        own = self._sessions(shown.id)
        if rec is not None and all(s.key == rec.session_key for s in own):
            return
        new = rec if rec is not None else record(self.ws, place(self.ws, shown))
        for session in own:
            if session.key != new.session_key:
                self.d.runtime.stop(session.key)
        self.d.cp("release.stopped!")
        if rec is None:
            self.d.beads.ensure_record(self.ws.name, shown.id, new, replace_unreadable=True)
            self.d.cp("release.recorded!")

    # --- the launch guard ---

    def launch(self, op: Op, new: SessionRecord | None = None) -> Launch:
        """The one way wsd starts or resumes a session. In order: the runtime is up and no workstream
        hold applies; no other bead holds the coder role; the bead has no `needs-human`; btq's own
        post-claim checks pass (ownership, routing, design approval); the launched-session record is on
        the bead; the bead is runnable; its worktree is verified; then the launch. `new` is the record a
        pickup writes (kept as it is if the bead already records the same launch); a resume reads the
        record already on the bead and never recomputes it.

        wsd launches one role per workstream, so every session the runtime lists for the workstream holds
        the coder role, whatever role name it was launched under: renaming the role in the configuration
        never frees the role while an old session may run."""
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        kind = op.kind.value
        if op.attempts >= self.ws.limits.launch_failures_before_human:    # a budget spent before a crash
            self.escalate_from(op, Reason.LAUNCH_FAILED, "the launch's attempts are spent")
            return Launch.ENDED
        if not self.d.runtime.available():
            j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
            return Launch.WAIT
        if any(reason not in GUARD_SETTLES or held != bead for reason, held in j.holds(ws).items()):
            return Launch.WAIT
        try:
            listed = self._sessions()
        except RuntimeUnavailable:
            j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
            return Launch.WAIT
        if any(s.bead != bead for s in listed):
            return Launch.WAIT               # one coder session at a time (§4.3)
        own = listed
        if NEEDS_HUMAN in self.d.beads.show(ws, bead).labels:
            self._finish(op, OpStatus.STUCK, BeadState.STUCK, Reason.NEEDS_HUMAN)
            return Launch.ENDED
        try:
            shown = self.d.beads.validate(ws, bead)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return Launch.ENDED
        except RoutingChanged:
            self.escalate_from(op, Reason.ROUTING_CHANGED)
            return Launch.ENDED
        try:
            if new is not None:
                self.d.beads.ensure_record(ws, bead, new)
                self.d.cp(f"{kind}.recorded!")
            rec = shown.record() if new is None else new
        except RecordUnreadable:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "the session record does not parse")
            return Launch.ENDED
        except RecordConflict:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, "the session record names another launch")
            return Launch.ENDED
        if rec is None:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED)
            return Launch.ENDED
        if HELD in shown.labels or PARKED in shown.labels or shown.open_blockers():
            if own:
                self.escalate_from(op, Reason.UNEXPECTED_STATE, "not runnable, but its session is listed")
                return Launch.ENDED
            return self._shelve(op)
        try:
            worktree = self.d.beads.verify_worktree(ws, bead, Path(rec.repo), Path(rec.worktree))
        except WorktreeConflict as exc:
            self.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
            return Launch.ENDED
        accounts = self.ws.accounts
        if accounts is None or accounts.adapter(rec.profile) is None:
            self.escalate_from(op, Reason.CONFIG_INVALID, "no account can be pinned for the profile")
            return Launch.ENDED
        adoption = j.adoption(ws, bead)
        if adoption is not None and not adoption.settled:
            return Launch.WAIT               # startup has not put its entry on the bead yet (§3.2)
        if adoption is not None and adoption.unresolved_hold:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, adoption.detail)
            return Launch.ENDED
        try:
            done = self._own_dispatch(op, rec)                  # step 0
            if done is None:
                done = self._reconcile(op, rec, shown)          # step 1
            if done is not None:
                return done
            if any(s.liveness is not Liveness.LIVE or s.key != rec.session_key for s in own):
                return self._uncertain(op, "a session of this bead is listed but not confirmed live")
            if own:
                self._finish_live(op, rec)
                return Launch.LIVE
            return self._pin_and_dispatch(op, rec, shown, worktree, accounts)    # steps 2 to 6
        except (LaunchConflict, LaunchesUnreadable, EntryConflict) as exc:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, f"launch entries disagree: {exc}")
            return Launch.ENDED
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return Launch.ENDED

    # --- launch entries (AU-3 §4.2) ---

    def _bead_write(self, entry: LaunchEntry, point: str) -> None:
        self.d.beads.ensure_launch(self.ws.name, entry.bead, entry)
        self.d.cp(point)

    def _no_receipt(self, op: Op, generation: int) -> Launch:
        """A dispatched generation with no receipt: held, never settled by wsd (§4.3). What the runtime
        lists for the bead goes into the detail for the operator, and is not evidence of anything."""
        try:
            seen = f"{len(self._sessions(op.bead))} session(s) of the bead listed"
        except RuntimeUnavailable:
            seen = "the runtime's session list unavailable"
        self.escalate_from(op, Reason.UNEXPECTED_STATE, f"generation {generation} was dispatched with no "
                           f"launch receipt ({seen}; not evidence)")
        return Launch.ENDED

    def _own_dispatch(self, op: Op, rec: SessionRecord) -> Launch | None:
        """Step 0: the operation's own generation, once dispatched, is finished from its receipt and never
        dispatched again. None: no generation of this op has been dispatched."""
        generation = op.data.get("generation")
        if not generation:
            return None
        entry = self.d.journal.launch(rec.session_key, int(generation))
        if entry is None:
            raise EntryConflict(f"generation {generation} of the operation is not journaled")
        if entry.dispatched_at is None:
            return None
        return self._outcome(op, entry)

    def _outcome(self, op: Op, entry: LaunchEntry) -> Launch:
        """Steps 0 and 6: the single path for a dispatched generation of this op, live or replayed."""
        j, kind = self.d.journal, op.kind.value
        receipt = j.receipt(*entry.ident)
        if receipt is None:
            return self._no_receipt(op, entry.generation)
        entry = self._settle(entry, receipt, f"{kind}.outcome")
        if receipt.kind == "started":
            self._finish(op, OpStatus.DONE, BeadState.RUNNING)
            self.d.cp(f"{kind}.done")
            return Launch.STARTED
        if receipt.refusal == "unavailable":
            with j.transaction():
                j.op_forget(op.op_id, "generation")
                self._runtime_hold(op)
            return Launch.WAIT
        if self._spend(op, self.ws.limits.launch_failures_before_human, Reason.LAUNCH_FAILED,
                       receipt.error or "", forget=("generation",)):
            return Launch.ENDED
        return Launch.FAILED

    def _settle(self, entry: LaunchEntry, receipt: Receipt, point: str) -> LaunchEntry:
        """A receipt's outcome (and native ID) on the entry: journal (`point`), then bead (`point!`)."""
        j = self.d.journal
        with j.transaction():
            outcome = LAUNCHED if receipt.kind == "started" else ABANDONED
            entry = j.launch_set(*entry.ident, "outcome", outcome)
            if receipt.native_id:
                entry = j.launch_set(*entry.ident, "native_id", receipt.native_id)
        self.d.cp(point)
        self._bead_write(entry, f"{point}!")
        return entry

    def _reconcile(self, op: Op, rec: SessionRecord, shown: Bead) -> Launch | None:
        """Step 1, before anything chooses: rebuild the journal from the bead's copies, then settle every
        generation of the session but the op's own pinned one, and bring the bead's copies up to date."""
        j, ws, kind = self.d.journal, self.ws.name, op.kind.value
        copies = shown.launches()
        if any(e.ws != ws or e.bead != op.bead for e in copies.entries):
            raise LaunchesUnreadable("wsd_launches names another bead")
        known = {e.ident for e in j.launches_of(ws, op.bead)}
        missing = [e for e in copies.entries if e.ident not in known]
        if missing:
            with j.transaction():
                for e in missing:
                    j.launch_insert(e, rebuilt=True)
            self.d.cp(f"{kind}.rebuilt")
        pinned = op.data.get("generation")
        for entry in j.launches(rec.session_key):
            if entry.outcome is None and str(entry.generation) != pinned:
                if entry.dispatched_at is None:         # it never reached the runtime
                    with j.transaction():
                        entry = j.launch_set(*entry.ident, "outcome", ABANDONED)
                    self.d.cp(f"{kind}.reconciled")
                else:
                    receipt = j.receipt(*entry.ident)
                    if receipt is None:
                        return self._no_receipt(op, entry.generation)
                    entry = self._settle(entry, receipt, f"{kind}.reconciled")
            at = copies.find(entry.ident)
            if at is None or copies.entries[at] != entry:
                self._bead_write(entry, f"{kind}.reconciled!")
        return None

    def _finish_live(self, op: Op, rec: SessionRecord) -> None:
        """The bead's own session is live: the op's pinned generation, if any, never reached the runtime.
        It is abandoned in the transaction that ends the op, so no replay can find the op open with an
        abandoned generation to dispatch; then the bead copy."""
        j, kind = self.d.journal, op.kind.value
        generation = op.data.get("generation")
        entry = None
        with j.transaction():
            if generation:
                entry = j.launch_set(rec.session_key, int(generation), "outcome", ABANDONED)
            self._finish(op, OpStatus.DONE, BeadState.RUNNING)
        self.d.cp(f"{kind}.abandoned")
        if entry is not None:
            self._bead_write(entry, f"{kind}.abandoned!")
        self.d.cp(f"{kind}.done")

    def _pin_and_dispatch(self, op: Op, rec: SessionRecord, shown: Bead, worktree: Path,
                          accounts: Accounts) -> Launch:
        j, ws, kind = self.d.journal, self.ws.name, op.kind.value
        adapter = accounts.adapter(rec.profile) or ""
        for attempt in range(2):
            generation = op.data.get("generation")
            if generation:
                entry = j.launch(rec.session_key, int(generation))
                if entry is None:
                    raise EntryConflict(f"generation {generation} of the operation is not journaled")
                self._bead_write(entry, f"{kind}.entry!")        # a replay: keep, write or conflict
            else:
                pinned = self._pin(op, rec, accounts)            # step 2
                if isinstance(pinned, Launch):
                    return pinned
                op, entry = pinned
            if self._pin_holds(accounts, rec, entry):           # step 3
                break
            with j.transaction():
                entry = j.launch_set(*entry.ident, "outcome", ABANDONED)
                op = j.op_forget(op.op_id, "generation")
            self.d.cp(f"{kind}.abandoned")
            self._bead_write(entry, f"{kind}.abandoned!")
            if attempt:
                return Launch.WAIT              # a second failure in one call: the next pickup tries again
        else:
            return Launch.WAIT
        # step 4: dispatch, only ever of an entry with no outcome
        if entry.outcome is not None or entry.dispatched_at is not None:
            raise EntryConflict(f"generation {entry.generation} already has an outcome or a dispatch")
        with j.transaction():
            entry = j.launch_set(*entry.ident, "dispatched_at", now())
            op = j.op_step(op.op_id, "dispatched")
        self.d.cp(f"{kind}.dispatched")
        self._bead_write(entry, f"{kind}.dispatched!")
        launched = [e for e in j.launches(rec.session_key) if e.outcome == LAUNCHED]
        # A resume continues the latest launched generation's native session; Claude's first launch uses
        # the session key as its native ID, and Codex's is unknown until its thread starts.
        first = rec.session_key if adapter == "claude-code" else None
        native = launched[-1].native_id if launched else first
        spec = LaunchSpec(ws, op.bead, rec.role, rec.profile, rec.session_key, label(shown, rec.role),
                          worktree, resume=op.kind is OpKind.RESUME, ref=op.data.get("ref") or None,
                          generation=entry.generation, native_id=native, account=entry.account,
                          model=entry.model_passed, repo=Path(rec.repo))
        # step 5: the receipt, before anything else is done with the result
        try:
            started = self.d.runtime.launch(spec)
        except LaunchFailed as exc:
            receipt = Receipt(*entry.ident, "refused", refusal="failed", error=str(exc))
        except RuntimeUnavailable as exc:
            receipt = Receipt(*entry.ident, "refused", refusal="unavailable", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - the runtime can't say: no receipt, held at once
            self.escalate_from(op, Reason.UNEXPECTED_STATE, f"the runtime could not say whether generation "
                               f"{entry.generation} started ({type(exc).__name__})")
            return Launch.ENDED
        else:
            self.d.cp(f"{kind}.launched!")
            receipt = Receipt(*entry.ident, "started", tmux_session=started.tmux_session,
                              tmux_pane=started.tmux_pane, pane_pid=started.pane_pid,
                              native_id=started.native_id)
        j.receipt_put(receipt)
        self.d.cp(f"{kind}.receipt")
        return self._outcome(op, entry)                        # step 6

    def _previous_key(self, session_key: str) -> str | None:
        """The key of the session's highest launched generation, read after step 1's reconciliation."""
        launched = [e for e in self.d.journal.launches(session_key) if e.outcome == LAUNCHED]
        return launched[-1].credential_key if launched else None

    def _pin(self, op: Op, rec: SessionRecord, accounts: Accounts) -> tuple[Op, LaunchEntry] | Launch:
        """Step 2: gate the account (AU-5 §3.3) and journal generation n+1 with the op's step, then the bead
        copy. A Deadline or AccountChanged defers the bead inside this op (AU-4 §3.2 entry 3): no session
        was dispatched for it, so the tail's stop only confirms that."""
        j, kind = self.d.journal, op.kind.value
        entries = j.launches(rec.session_key)
        try:
            chosen = decide(accounts, j, rec.profile, self._previous_key(rec.session_key), self.d.clock(),
                            self.ws.usage)
        except ConfigError as exc:
            self.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
            return Launch.ENDED
        if isinstance(chosen, AccountChanged | Deadline):
            until = chosen.at if isinstance(chosen, Deadline) else None
            self._defer_within(op, rec, QUOTA if until is not None else ACCOUNT_CHANGED, until)
            return Launch.ENDED
        last = max((e.generation for e in entries), default=0)
        if last >= MAX_GENERATION:
            self.escalate_from(op, Reason.UNEXPECTED_STATE, f"generations exhausted at {last}")
            return Launch.ENDED
        n = last + 1
        entry = LaunchEntry(rec.session_key, n, self.ws.name, op.bead, rec.role, rec.profile, chosen.account,
                            chosen.key, self.ws.models.get(rec.profile, ""), False, now())
        with j.transaction():
            j.launch_insert(entry)
            op = j.op_step(op.op_id, "entry", {"generation": str(n)})
        self.d.cp(f"{kind}.entry")
        self._bead_write(entry, f"{kind}.entry!")
        return op, entry

    def _pin_holds(self, accounts: Accounts, rec: SessionRecord, entry: LaunchEntry) -> bool:
        """Step 3, immediately before dispatch: the pinned account is still permitted under its pinned key,
        and nothing blocks it (`admits`, through the same view and cache as step 2). A key that can't be
        resolved has changed."""
        return admitted(accounts, self.d.journal, self._previous_key(rec.session_key), entry, self.d.clock(),
                        self.ws.usage)

    def _uncertain(self, op: Op, detail: str) -> Launch:
        """The role stays taken and the workstream holds; the next pickup's replay settles it from the
        session list (live: done; no longer listed: launch again; still unknown: keep holding)."""
        j = self.d.journal
        j.hold(self.ws.name, Reason.LAUNCH_UNCERTAIN, op.bead)
        row = j.state(self.ws.name, op.bead)
        if row is not None:
            j.set_state(self.ws.name, op.bead, row.state, Reason.LAUNCH_UNCERTAIN, detail)
        return Launch.UNCERTAIN

    def _shelve(self, op: Op) -> Launch:
        """The bead stopped being runnable before its launch (a blocker, a hold or `v2:parked` appeared)
        and has no session: it goes back to waiting, parked, with nothing to stop or commit."""
        self.d.beads.ensure_label(self.ws.name, op.bead, PARKED)
        self.d.cp(f"{op.kind.value}.shelved!")
        shown = self.d.beads.show(self.ws.name, op.bead)
        final = parked_state(shown)
        self._finish(op, OpStatus.ABANDONED, final, REASONS[final], blocker_detail(shown))
        return Launch.ENDED

    # --- dispatch ---

    def replay(self, op: Op) -> None:
        """Continue any open operation but a pickup (the scheduler owns those), except a pickup at a step
        that never launches: a `defer.*` step goes to the tail, and AU-5's `quota` step to its conversion,
        before anything else (§3.6)."""
        if op.step.startswith("defer."):
            self.defer_tail(op)
        elif op.step == "quota" and op.kind in (OpKind.PICKUP, OpKind.RESUME):
            self.convert_quota(op)
        elif op.kind is OpKind.PARK:
            self.replay_park(op)
        elif op.kind is OpKind.RESUME:
            self.replay_resume(op)
        elif op.kind is OpKind.RELEASE:
            self.replay_release(op)
        elif op.kind is OpKind.ESCALATE:
            self.replay_escalate(op)
        else:
            raise ValueError("pickup operations are replayed by the scheduler")
