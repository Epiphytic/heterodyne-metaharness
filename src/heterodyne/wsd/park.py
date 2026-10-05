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

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import (
    HELD,
    NEEDS_HUMAN,
    PARKED,
    Bead,
    NotOurs,
    RecordConflict,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable, Session
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, label, place, record

PARK_POINTS = ("lock.waiting", "park.intent", "park.stopped!", "park.stopped", "park.committed!",
               "park.committed", "park.blocked!", "park.blocked", "park.labelled!", "park.labelled",
               "park.commented!", "park.done")
RESUME_POINTS = ("resume.intent", "resume.unlabelled!", "resume.unlabelled", "resume.launched!",
                 "resume.done")
RELEASE_POINTS = ("lock.waiting", "release.intent", "release.unlabelled!", "release.unlabelled",
                  "release.done")
ESCALATE_POINTS = ("escalate.intent", "escalate.labelled!", "escalate.done")
PARK_MARK = "wsd-park: "
REASONS = {BeadState.HELD: Reason.HELD_BY_OPERATOR, BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR,
           BeadState.PARKED: Reason.BLOCKED_ON_BEAD}
RELEASABLE = frozenset({BeadState.HELD, BeadState.STUCK})
# Holds the guard settles itself from the session list instead of waiting on.
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
        j = self.d.journal
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.STUCK)
            self._settle_uncertain(op)
            esc = j.op_open(OpKind.ESCALATE, self.ws.name, op.bead, {"reason": reason.value})
            j.set_state(self.ws.name, op.bead, BeadState.STUCK, reason, detail, op.data.get("ref") or None)
        self.d.cp("escalate.intent")
        self.replay_escalate(esc)

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
        try:
            if op.step == "intent":
                for session in self._sessions(bead):
                    self.d.runtime.stop(session.key)
                self.d.cp("park.stopped!")
                op = j.op_step(op.op_id, "stopped")
                self.d.cp("park.stopped")
            if op.step == "stopped":
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
            if j.op_failed(op.op_id) >= self.ws.limits.park_attempts_before_human:
                self.escalate_from(op, Reason.PARK_FAILED, str(exc))
                return BeadState.STUCK
            j.set_state(ws, bead, BeadState.PARKING, Reason.PARK_FAILED, str(exc))
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
                op = j.op_open(OpKind.RELEASE, ws, bead, {"ref": ref or ""})
            self.d.cp("release.intent")
            return self.replay_release(op)

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
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.DONE)
            nxt = j.op_open(OpKind.RESUME, ws, bead, {"ref": op.data.get("ref", "")})
            j.op_step(nxt.op_id, "unlabelled")
            j.set_state(ws, bead, BeadState.RESUMING, ref=op.data.get("ref") or None)
        self.d.cp("release.done")
        return BeadState.RESUMING

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
        if not self.d.runtime.available():
            j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
            return Launch.WAIT
        if set(j.holds(ws)) - GUARD_SETTLES:
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
        if any(s.liveness is not Liveness.LIVE or s.key != rec.session_key for s in own):
            return self._uncertain(op, "a session of this bead is listed but not confirmed live")
        if not own:
            spec = LaunchSpec(ws, bead, rec.role, rec.profile, rec.session_key, label(shown, rec.role),
                              worktree, resume=op.kind is OpKind.RESUME, ref=op.data.get("ref") or None)
            try:
                self.d.runtime.launch(spec)
            except RuntimeUnavailable:
                j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
                return Launch.WAIT
            except LaunchFailed as exc:
                if j.op_failed(op.op_id) >= self.ws.limits.launch_failures_before_human:
                    self.escalate_from(op, Reason.LAUNCH_FAILED, str(exc))
                    return Launch.ENDED
                row = j.state(ws, bead)
                j.set_state(ws, bead, row.state if row else BeadState.STUCK, Reason.LAUNCH_FAILED, str(exc))
                return Launch.FAILED
            except Exception as exc:  # noqa: BLE001 - any other outcome is uncertain, never a failure
                return self._uncertain(op, type(exc).__name__)
            self.d.cp(f"{kind}.launched!")
        self._finish(op, OpStatus.DONE, BeadState.RUNNING)
        self.d.cp(f"{kind}.done")
        return Launch.LIVE if own else Launch.STARTED

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
