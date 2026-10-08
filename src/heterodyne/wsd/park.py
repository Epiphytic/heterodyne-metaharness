"""Every journaled bead operation but the claim (ADR 0001 §3.3, §4.3), and the one launch path.

- Park: intent, stop every session of the bead, WIP commit (SHA recorded), blocking edges, labels, bead
  comment. Blocking edges go on before `v2:parked`, and `v2:held` before `v2:parked`, so the bead is
  never `v2:parked` without what keeps it from being resumed.
- Resume: intent, remove `v2:parked`, then the launch guard. A replay that finds `v2:parked` already
  gone is its own completed unlabel, not a cancellation.
- Release (the operator's, plan 6): remove `v2:held` and `needs-human`, then either back to parked or
  on to a resume. It is the only way out of HELD or STUCK.
- Escalate: STUCK in the journal, then `needs-human` on the bead, replayed until it reads back.
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
from enum import StrEnum
from pathlib import Path

from heterodyne.config import ConfigError
from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.accounts import DEFAULT, AccountChanged, Accounts
from heterodyne.wsd.beads import (
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
from heterodyne.wsd.journal import EntryConflict, Op, OpKind, OpStatus, now
from heterodyne.wsd.launches import ABANDONED, LAUNCHED, LaunchEntry, LaunchesUnreadable, Receipt
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable, Session
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.upgrade import (
    CODEX_THREAD_FIELD,
    adopted_entry,
    adoption_adapter,
    check_agreement,
    configured_at_upgrade,
)
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, label, place, record

PARK_POINTS = ("lock.waiting", "park.intent", "park.stopped!", "park.stopped", "park.committed!",
               "park.committed", "park.blocked!", "park.blocked", "park.labelled!", "park.labelled",
               "park.commented!", "park.done")
# The launch guard's points after its plan 3 checks, as `<kind>.<step>` (AU-3 §4.2). A crash at one of
# UNRECEIPTED leaves a dispatched generation with no receipt, which holds and is never dispatched again.
GUARD_STEPS = ("entry", "entry!", "dispatched", "dispatched!", "launched!", "receipt", "outcome", "outcome!",
               "done")
UNRECEIPTED = ("dispatched", "dispatched!", "launched!")
RESUME_POINTS = ("resume.intent", "resume.unlabelled!", "resume.unlabelled",
                 *(f"resume.{step}" for step in GUARD_STEPS))
RELEASE_POINTS = ("lock.waiting", "release.intent", "release.unlabelled!", "release.unlabelled",
                  "release.done")
ADOPTED_RELEASE_POINTS = ("release.adopted", "release.adopted!")
ESCALATE_POINTS = ("escalate.intent", "escalate.labelled!", "escalate.done")
PARK_MARK = "wsd-park: "
REASONS = {BeadState.HELD: Reason.HELD_BY_OPERATOR, BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR,
           BeadState.PARKED: Reason.BLOCKED_ON_BEAD}
RELEASABLE = frozenset({BeadState.HELD, BeadState.STUCK})
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
    """Release applies only to a HELD or STUCK bead with no operation open on it."""


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
                sha = gitwip.wip_commit(worktree, op.op_id, f"parked {bead}")
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
                self._refuse_unverifiable(bead)
                op = j.op_open(OpKind.RELEASE, ws, bead, {"ref": ref or ""})
            self.d.cp("release.intent")
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
                self._abandon_pinned(op, rec)
                self._finish(op, OpStatus.DONE, BeadState.RUNNING)
                self.d.cp(f"{kind}.done")
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

    def _abandon_pinned(self, op: Op, rec: SessionRecord) -> None:
        """The op's pinned generation never reached the runtime: abandoned, journal then bead."""
        generation = op.data.get("generation")
        if not generation:
            return
        j, kind = self.d.journal, op.kind.value
        entry = j.launch_set(rec.session_key, int(generation), "outcome", ABANDONED)
        self.d.cp(f"{kind}.abandoned")
        self._bead_write(entry, f"{kind}.abandoned!")

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
            if self._pin_holds(accounts, adapter, rec.profile, entry):  # step 3
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
        # step 4: dispatch
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
                          model=entry.model_passed)
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

    def _pin(self, op: Op, rec: SessionRecord, accounts: Accounts) -> tuple[Op, LaunchEntry] | Launch:
        """Step 2: choose the account and journal generation n+1 with the op's step, then the bead copy."""
        j, kind = self.d.journal, op.kind.value
        entries = j.launches(rec.session_key)
        launched = [e for e in entries if e.outcome == LAUNCHED]
        try:
            chosen = accounts.choose(rec.profile, launched[-1].credential_key if launched else None)
        except ConfigError as exc:
            self.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
            return Launch.ENDED
        if isinstance(chosen, AccountChanged):
            self.escalate_from(op, Reason.ACCOUNT_CHANGED, chosen.detail)
            return Launch.ENDED
        n = 1 + max((e.generation for e in entries), default=0)
        entry = LaunchEntry(rec.session_key, n, self.ws.name, op.bead, rec.role, rec.profile, chosen.account,
                            chosen.key, self.ws.models.get(rec.profile, ""), False, now())
        with j.transaction():
            j.launch_insert(entry)
            op = j.op_step(op.op_id, "entry", {"generation": str(n)})
        self.d.cp(f"{kind}.entry")
        self._bead_write(entry, f"{kind}.entry!")
        return op, entry

    @staticmethod
    def _pin_holds(accounts: Accounts, adapter: str, profile: str, entry: LaunchEntry) -> bool:
        """Step 3, immediately before dispatch: the pinned account's key is unchanged and it is still
        eligible. A key that can't be resolved has changed."""
        try:
            same = accounts.current_key(adapter, entry.account) == entry.credential_key
        except ConfigError:
            same = False
        return same and accounts.eligible(profile, entry.account)

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
        """Continue any open operation but a pickup (the scheduler owns those)."""
        if op.kind is OpKind.PARK:
            self.replay_park(op)
        elif op.kind is OpKind.RESUME:
            self.replay_resume(op)
        elif op.kind is OpKind.RELEASE:
            self.replay_release(op)
        elif op.kind is OpKind.ESCALATE:
            self.replay_escalate(op)
        else:
            raise ValueError("pickup operations are replayed by the scheduler")
