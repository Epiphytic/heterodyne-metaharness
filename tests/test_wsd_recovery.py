import os
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, PauseAt, Recorder, SimulatedCrash
from wsd_env import WS, LockAt, Rig, Worker, finish, make_rig, probe_lock

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY
from heterodyne.wsd.journal import JournalBusy, OpKind
from heterodyne.wsd.park import PARK_MARK, PARK_POINTS, RELEASE_POINTS, RESUME_POINTS
from heterodyne.wsd.recovery import RECOVERY_POINTS, Recovered, recover
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import POINTS, Outcome
from heterodyne.wsd.states import BeadState, Reason, WsState


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig's queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def lose_journal(rig: Rig) -> None:
    rig.journal.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{rig.root / 'state' / 'wsd.db'}{suffix}").unlink(missing_ok=True)
    rig.restart()
    assert rig.journal.fresh


def started(tmp_path: Path, *extra: str) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    for bead in extra:
        rig.world.add(bead)
    assert rig.pickup() is Outcome.STARTED
    return rig


def row_of(rig: Rig, bead: str = "btq-1") -> tuple[str | None, Reason | None]:
    row = rig.journal.state(WS, bead)
    return (None, None) if row is None else (row.state.value, row.reason)


def test_recovery_runs_in_adr_order(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    assert recover(rig.sched).ok
    assert isinstance(rig.cp, Recorder)
    assert rig.cp.seen == list(RECOVERY_POINTS)


@pytest.mark.parametrize("point", RECOVERY_POINTS)
def test_crash_during_recovery_is_safe_to_repeat(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(CrashAt("park.blocked"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.state("btq-1") == "parked" and rig.journal.ops_open() == []
    assert len(rig.runtime.launches) == 1


def test_recovery_never_launches(tmp_path: Path) -> None:
    """Findings 2 and 3: a running bead whose session ended gets a resume operation; the launch is the
    guard's, at the next pickup, after every listed session has been seen."""
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.resumes == 1
    assert len(rig.runtime.launches) == 1
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    assert row_of(rig) == ("resuming", Reason.SESSION_DEAD)
    assert rig.pickup() is Outcome.BUSY
    assert [s.resume for s in rig.runtime.launches] == [False, True]


def test_unknown_session_is_reserved_before_any_launch(tmp_path: Path) -> None:
    """Finding 2: the runtime lists the session, but can't say it is live. It keeps the coder role: no
    relaunch of its bead and no new claim."""
    rig = started(tmp_path, "btq-2")
    rig.runtime.set(rig.key("btq-1"), Liveness.UNKNOWN)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.pickup() is Outcome.BUSY
    assert len(rig.runtime.launches) == 1 and rig.world.claims == ["btq-1"]


def test_two_dead_sessions_never_run_together(tmp_path: Path) -> None:
    """Two running beads of ours with no session (possible only after manual intervention): both get
    resume operations, and the guard lets exactly one through."""
    rig = started(tmp_path, "btq-2")
    rig.start("btq-2")                              # the intervention: a second coder, by hand
    for bead in ("btq-1", "btq-2"):
        rig.runtime.end(rig.key(bead))
    rig.restart()
    assert recover(rig.sched).resumes == 2
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.coders() == ["btq-1"]
    assert [op.bead for op in rig.journal.ops_open()] == ["btq-2"]       # waiting on the role


def test_lost_journal_holds_a_running_bead(tmp_path: Path) -> None:
    """Finding 11: no journal row for a bead we hold. Its session keeps the role; nothing is relaunched,
    nothing new is claimed, and a human looks."""
    rig = started(tmp_path, "btq-2")
    lose_journal(rig)
    result = recover(rig.sched)
    assert result.ok and result.held == 1
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"]


def test_lost_journal_never_relaunches_a_dead_session(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_lost_journal_never_finishes_a_cut_short_park(tmp_path: Path) -> None:
    """r1 completed the park from beads alone; r2 holds instead. The edge is on, `v2:parked` is not, and
    nothing says whether the WIP commit happened."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(CrashAt("park.blocked"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert len(rig.runtime.launches) == 1


def test_lost_journal_parked_bead_resumes_only_after_release(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.parker.release("btq-1").value == "parked"
    assert rig.pickup() is Outcome.RESUMED


def test_ownership_is_found_after_a_label_change(tmp_path: Path) -> None:
    """Finding 12: someone moved the claimed bead off this workstream. Recovery finds it by assignee
    and escalates it; its session keeps the role."""
    rig = started(tmp_path, "btq-2")
    rig.world.beads["btq-1"].labels.remove("ws:alpha")
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.ROUTING_CHANGED)
    assert rig.pickup() is Outcome.BUSY and rig.world.claims == ["btq-1"]


def test_needs_human_bead_is_stuck_not_relaunched(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.NEEDS_HUMAN)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_running_bead_without_a_record_is_held(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.LAUNCH_UNRECORDED)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_held_without_parked_is_unexpected(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.append(HELD)
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.UNEXPECTED_STATE)


def test_session_of_a_bead_not_ours_is_stopped(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].assignee = "someone:host:recovery"
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.stopped == 1
    assert row_of(rig) == ("stuck", Reason.CLAIM_LOST) and rig.runtime.coders() == []


def test_unconfirmed_stop_of_a_closed_bead_is_stuck_until_confirmed(tmp_path: Path) -> None:
    """r1 (deviation from the plan's test): a stop the runtime can't confirm fails recovery with the
    runtime hold; the bead is still recorded STUCK/STOP_UNCONFIRMED, and the resume the same sweep found
    for another bead is not opened. The next recovery confirms the stop and opens it."""
    rig = started(tmp_path, "btq-2")
    rig.start("btq-2")
    rig.runtime.end(rig.key("btq-2"))
    rig.world.close("btq-1")
    rig.runtime.stop_failures = 1
    rig.restart()
    result = recover(rig.sched)
    assert not result.ok and Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert row_of(rig) == ("stuck", Reason.STOP_UNCONFIRMED) and rig.runtime.coders() == ["btq-1"]
    assert rig.journal.ops_open() == [] and rig.state("btq-2") == "running"     # opens nothing
    result = recover(rig.sched)
    assert result.ok and result.stopped == 1 and result.resumes == 1
    assert rig.runtime.coders() == []
    [op] = rig.journal.ops_open()
    assert (op.kind, op.bead) == (OpKind.RESUME, "btq-2")
    rig.pickup()                                      # only pickup clears the runtime hold
    assert rig.journal.holds(WS) == {} and rig.runtime.coders() == ["btq-2"]
    assert [s.bead for s in rig.runtime.launches if s.resume] == ["btq-2"]


def test_unconfirmed_stop_in_an_interrupted_park_fails_recovery(tmp_path: Path) -> None:
    """r1: the park's replay can't confirm its stop. Recovery fails with the runtime hold, the park stays
    open at its step with the bead STOP_UNCONFIRMED, and another bead's dead session gets no resume
    until a recovery succeeds."""
    rig = started(tmp_path, "btq-2", "btq-3")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.start("btq-3")
    rig.runtime.end(rig.key("btq-3"))
    rig.restart(CrashAt("park.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart()
    rig.runtime.stop_failures = 1
    result = recover(rig.sched)
    assert not result.ok and Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    [op] = rig.journal.ops_open()
    assert (op.kind, op.bead) == (OpKind.PARK, "btq-1")
    assert row_of(rig) == ("parking", Reason.STOP_UNCONFIRMED) and rig.state("btq-3") == "running"
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1 and result.resumes == 1
    assert rig.state("btq-1") == "parked"
    [op] = rig.journal.ops_open()
    assert (op.kind, op.bead) == (OpKind.RESUME, "btq-3")


def test_park_left_open_by_a_git_failure_is_not_a_failed_recovery(tmp_path: Path) -> None:
    """r1: only a dependency failure fails recovery. A park whose WIP commit fails under its budget stays
    open for the next replay, with no hold."""
    rig = started(tmp_path, "btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    (rig.worktree("btq-1") / "work.txt").write_text("half done")
    rig.restart(CrashAt("park.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart()
    lock = Path(gitwip.git(rig.worktree("btq-1"), "rev-parse", "--absolute-git-dir").strip()) / "index.lock"
    lock.write_text("")                                             # every `git add` fails
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1 and rig.journal.holds(WS) == {}
    assert row_of(rig) == ("parking", Reason.PARK_FAILED) and len(rig.journal.ops_open()) == 1
    lock.unlink()
    assert recover(rig.sched).ok and rig.state("btq-1") == "parked" and rig.journal.ops_open() == []


def test_failed_sweep_opens_no_resume(tmp_path: Path) -> None:
    """r1: the sweep finds btq-1's session dead, then can't validate btq-3's claim. Recovery fails with
    the beads hold, and btq-1's resume, found first, was never opened."""
    rig = started(tmp_path, "btq-3")
    rig.start("btq-3")
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    rig.world.fault("owned", RuntimeError("dolt down"), bead="btq-3")
    result = recover(rig.sched)
    assert not result.ok and Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)
    assert rig.journal.ops_open() == [] and row_of(rig) == ("running", None)
    result = recover(rig.sched)
    assert result.ok and result.resumes == 1 and rig.journal.holds(WS) == {}
    [op] = rig.journal.ops_open()
    assert (op.kind, op.bead) == (OpKind.RESUME, "btq-1")


def test_beads_down_at_startup_holds_and_claims_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    result = recover(rig.sched)
    assert not result.ok
    assert Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)
    assert rig.world.claims == []
    assert rig.journal.snapshot(WS).state is WsState.HELD         # published (deviation: asserted)


def test_runtime_that_cannot_list_holds_recovery(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.list_failures = 1
    rig.restart()
    result = recover(rig.sched)
    assert not result.ok
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels


def test_unsettled_actions_hold_after_recovery(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    rig.world.add("btq-aq", labels=["kind:approval"], status="closed", metadata={"action_state": "bogus"})
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap,btq-aq"}


# --- deviations: tests the plan does not have (Tasks 3, 6 and 7 notes, and guard mutants) ---


def test_recovery_takes_the_operation_lock(tmp_path: Path) -> None:
    """Recovery runs under `Parker.entry()`: while a park holds the lock, recovery asks for it, is
    blocked, and reads nothing until the park is done."""
    pause = PauseAt("park.stopped!")
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(pause)
    lock = probe_lock(rig, "recovery")
    result: list[Recovered] = []
    parker = Worker(lambda: rig.parker.park("btq-1", ("btq-2",)), "park")
    recoverer = Worker(lambda: result.append(recover(rig.sched)), "recovery")
    try:
        parker.start()
        assert pause.reached.wait(10)
        recoverer.start()
        assert lock.asked.wait(10)
        assert lock.probes == ["blocked"]
        assert result == [] and "recovery.read" not in pause.seen
        pause.go.set()
    finally:
        pause.go.set()
        finish(parker, recoverer)
    assert [r.ok for r in result] == [True] and rig.state("btq-1") == "parked"


@pytest.mark.parametrize("point", [p for p in POINTS if p != "lock.waiting"])
def test_recovery_leaves_every_interrupted_pickup_to_pickup(tmp_path: Path, point: str) -> None:
    """Recovery never launches and never makes a worktree: a pickup cut short at any point is read back
    (if at its claim) and left open; the next pickup carries it to exactly one launch."""
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    launches, worktrees = len(rig.runtime.launches), list(rig.world.worktrees)
    assert recover(rig.sched).ok
    assert len(rig.runtime.launches) == launches and rig.world.worktrees == worktrees
    assert rig.journal.holds(WS) == {}
    rig.pickup()
    assert rig.world.claims == ["btq-1"] and rig.world.worktrees == ["btq-1"]
    assert len(rig.runtime.launches) == 1 and rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_uncertain_claim_is_read_back_and_left_at_claimed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, cp=CrashAt("pickup.claimed!"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.PICKUP, "claimed")
    assert rig.runtime.launches == [] and rig.world.worktrees == []


def test_claim_that_never_landed_is_dropped(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, cp=CrashAt("gate.checked"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("dropped", Reason.CLAIM_ABANDONED) and rig.journal.ops_open() == []
    assert rig.pickup() is Outcome.STARTED


def interrupted(tmp_path: Path, kind: str) -> Rig:
    """btq-1 with an open operation of `kind`, cut short where its replay next reads the bead."""
    rig = started(tmp_path)
    if kind == "park":
        rig.world.add("btq-2")
        rig.world.beads["btq-2"].labels.remove("agent:wsd")
        rig.restart(CrashAt("park.blocked"))
        with pytest.raises(SimulatedCrash):
            rig.parker.park("btq-1", ("btq-2",))
    elif kind == "release":
        rig.parker.park("btq-1", (), why="operator /stop", hold=True)
        rig.restart(CrashAt("release.intent"))
        with pytest.raises(SimulatedCrash):
            rig.parker.release("btq-1")
    else:
        rig.restart(CrashAt("escalate.intent"))
        with pytest.raises(SimulatedCrash):
            rig.parker.escalate("btq-1", Reason.UNEXPECTED_STATE, "by hand")
    [op] = rig.journal.ops_open()
    assert op.kind.value == kind
    return rig


@pytest.mark.parametrize("kind", ["park", "release", "escalate"])
def test_deleted_bead_ends_its_interrupted_operation(tmp_path: Path, kind: str) -> None:
    """Task 7 F3: bd confirms the bead is gone, so recovery ends its operation through the scheduler's
    dispatch (sessions stopped first) instead of holding the workstream as unreachable forever."""
    rig = interrupted(tmp_path, kind)
    del rig.world.beads["btq-1"]
    rig.world.add("btq-3")
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}
    assert row_of(rig) == ("dropped", Reason.CLAIM_LOST) and rig.runtime.coders() == []
    assert rig.pickup() is Outcome.STARTED and rig.runtime.coders() == ["btq-3"]


@pytest.mark.parametrize("kind", ["park", "release", "escalate"])
def test_unreachable_queue_under_an_interrupted_operation_holds(tmp_path: Path, kind: str) -> None:
    """The replay's write fails, but the bead exists: never read as a deletion. Recovery fails and
    holds, the operation stays open, and the next recovery finishes it."""
    rig = interrupted(tmp_path, kind)
    rig.restart()
    rig.world.fault("owned", RuntimeError("dolt down"), bead="btq-1")
    result = recover(rig.sched)
    assert not result.ok and Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)
    assert len(rig.journal.ops_open()) == 1
    assert recover(rig.sched).ok and rig.journal.ops_open() == []


def test_pickup_of_a_deleted_bead_at_its_claim_ends(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, cp=CrashAt("pickup.claimed!"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    del rig.world.beads["btq-1"]
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("dropped", Reason.CLAIM_LOST) and rig.journal.ops_open() == []


@pytest.mark.parametrize("reason", [Reason.LAUNCH_UNCERTAIN, Reason.CLAIM_UNCERTAIN])
def test_orphan_uncertainty_hold_is_settled(tmp_path: Path, reason: Reason) -> None:
    """Task 7 note 2 (a deviation): an uncertainty hold whose bead has no open operation (a journal
    restored from a backup, or edited) would hold pickup forever. Recovery settles it after the
    replays; the sweep then reconciles the bead from beads and the runtime."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.journal.hold(WS, reason, "btq-1")
    assert rig.pickup() is Outcome.HELD
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {}
    assert rig.pickup() is Outcome.STARTED and rig.world.claims == ["btq-1"]


def test_orphan_claim_hold_of_a_claim_that_landed_is_escalated(tmp_path: Path) -> None:
    """The settled hold's claim did land, and the journal has the bead CLAIMING with no operation: the
    sweep escalates the disagreement; nothing is launched."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.claim(WS, "btq-1")
    rig.journal.set_state(WS, "btq-1", BeadState.CLAIMING)
    rig.journal.hold(WS, Reason.CLAIM_UNCERTAIN, "btq-1")
    result = recover(rig.sched)
    assert result.ok and result.held == 1 and rig.journal.holds(WS) == {}
    assert row_of(rig) == ("stuck", Reason.UNEXPECTED_STATE)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and rig.runtime.launches == []


def test_uncertain_launch_hold_stays_while_its_operation_is_open(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.HELD
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"} and len(rig.journal.ops_open()) == 1
    assert rig.pickup() is Outcome.HELD and rig.world.claims == ["btq-1"]


def test_unreadable_claim_fails_recovery_and_keeps_its_hold(tmp_path: Path) -> None:
    """r1 (deviation from the plan's test): the claim can't be read back, so recovery fails with the
    beads hold beside the claim's own; the pickup stays open. The next recovery reads it back."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"), bead="btq-1")
    rig.world.fault("show", RuntimeError("dolt down"), times=2, bead="btq-1")
    assert rig.pickup() is Outcome.HELD
    rig.world.fault("show", RuntimeError("dolt down"), times=2, bead="btq-1")
    rig.restart()
    result = recover(rig.sched)
    assert not result.ok and result.replayed == 0
    assert rig.journal.holds(WS) == {Reason.CLAIM_UNCERTAIN: "btq-1",
                                     Reason.BEADS_UNREACHABLE: "BeadsUnavailable"}
    assert len(rig.journal.ops_open()) == 1
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1 and rig.journal.holds(WS) == {}
    assert row_of(rig) == ("dropped", Reason.CLAIM_ABANDONED) and rig.journal.ops_open() == []
    assert rig.pickup() is Outcome.STARTED


def test_successful_recovery_clears_its_own_holds_only(tmp_path: Path) -> None:
    """Recovery clears what it checked (beads answered; no unsettled action). RUNTIME_UNAVAILABLE is
    pickup's to clear, once `available()` and `sessions()` both answer (Task 6 note 1)."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    for reason in (Reason.BEADS_UNREACHABLE, Reason.ACTIONS_UNRECONCILED, Reason.RUNTIME_UNAVAILABLE):
        rig.journal.hold(WS, reason, "x")
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.RUNTIME_UNAVAILABLE: "x"}
    assert rig.pickup() is Outcome.STARTED and rig.journal.holds(WS) == {}


def test_failed_recovery_reports_nothing_recovered(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    rig.world.down = True
    assert recover(rig.sched) == Recovered(WS, ok=False)
    assert rig.journal.ops_open() == []
    rig.world.down = False
    assert recover(rig.sched).resumes == 1


def test_busy_journal_propagates_and_is_never_held(tmp_path: Path) -> None:
    """Task 3: JournalBusy is never caught as a beads or runtime failure. It leaves recovery undone with
    no hold recorded, and the next recovery runs in full."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    lock = LockAt("recovery.read", rig.root / "state" / "wsd.db")
    rig.restart(lock)
    rig.journal.db.execute("PRAGMA busy_timeout = 0")
    try:
        with pytest.raises(JournalBusy):
            recover(rig.sched)
    finally:
        if lock.other is not None:
            lock.other.rollback()
            lock.other.close()
    assert lock.other is not None and rig.journal.holds(WS) == {}
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}


@pytest.mark.parametrize("point", RESUME_POINTS)
def test_interrupted_resume_is_left_to_pickup(tmp_path: Path, point: str) -> None:
    """An open resume is the guard's: recovery neither unlabels nor launches it, and the next pickup
    carries it on. Whatever the point, the bead is resumed exactly once."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    labels, launches = list(rig.world.beads["btq-1"].labels), len(rig.runtime.launches)
    result = recover(rig.sched)
    assert result.ok and result.replayed == 0 and result.resumes == 0
    assert rig.world.beads["btq-1"].labels == labels and len(rig.runtime.launches) == launches
    assert [op.kind for op in rig.journal.ops_open()] == ([] if point == "resume.done" else [OpKind.RESUME])
    rig.pickup()
    assert [s.resume for s in rig.runtime.launches] == [False, True] and rig.journal.ops_open() == []
    assert rig.state("btq-1") == "running" and rig.runtime.coders() == ["btq-1"]
    assert PARKED not in rig.world.beads["btq-1"].labels


def wip_commits(rig: Rig) -> list[str]:
    return gitwip.git(rig.worktree("btq-1"), "log", "--format=%H", f"--grep={PARK_MARK}").split()


@pytest.mark.parametrize("point", [p for p in PARK_POINTS if p != "lock.waiting"])
def test_recovery_completes_a_park_cut_short_anywhere(tmp_path: Path, point: str) -> None:
    """Through recovery's dispatch, not Parker.replay: the park finishes once and nothing relaunches."""
    rig = started(tmp_path, "btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    (rig.worktree("btq-1") / "work.txt").write_text("half done")
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",), why="w")
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.replayed == (0 if point == "park.done" else 1) and result.resumes == 0
    bead = rig.world.beads["btq-1"]
    assert PARKED in bead.labels and bead.deps == [("btq-2", "blocks")] and len(bead.comments) == 1
    assert len(wip_commits(rig)) == 1 and rig.state("btq-1") == "parked"
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}
    assert rig.runtime.live() == [] and len(rig.runtime.launches) == 1


@pytest.mark.parametrize("point", RELEASE_POINTS[1:])
def test_recovery_completes_a_release_cut_short_anywhere(tmp_path: Path, point: str) -> None:
    rig = started(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.replayed == (0 if point == "release.done" else 1)
    assert rig.state("btq-1") == "parked" and rig.journal.ops_open() == []
    assert HELD not in rig.world.beads["btq-1"].labels and len(rig.runtime.launches) == 1


@pytest.mark.parametrize("point", ["release.stopped!", "release.recorded!"])
def test_recovery_completes_a_release_that_replaces_a_record(tmp_path: Path, point: str) -> None:
    """The conditional release points: the replay stops the stray session, writes the record once and
    opens one resume, which recovery leaves to pickup."""
    rig = started(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "stray", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.escalate("btq-1", Reason.LAUNCH_UNRECORDED)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.replayed == 1
    assert "stray" not in rig.runtime.listed
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.session_key == first.session_key
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    rig.pickup()
    assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
    assert rig.runtime.coders() == ["btq-1"] and len(rig.runtime.launches) == 1


def test_claim_hold_left_by_an_ended_operation_is_settled_after_the_replays(tmp_path: Path) -> None:
    """The hold's bead still has an open operation when recovery starts, and its replay ends it without
    settling a CLAIM_UNCERTAIN hold (only a pickup's read-back does). Orphans are settled after the
    replays, so this one is too."""
    rig = interrupted(tmp_path, "escalate")
    rig.journal.hold(WS, Reason.CLAIM_UNCERTAIN, "btq-1")
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}


def test_runtime_that_cannot_list_replays_nothing(tmp_path: Path) -> None:
    """Step 2 reads beads and the runtime before anything is changed: a recovery that can't read both
    leaves every open journal as it was."""
    rig = interrupted(tmp_path, "park")
    rig.restart()
    rig.runtime.list_failures = 1
    assert not recover(rig.sched).ok
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.PARK, "blocked") and PARKED not in rig.world.beads["btq-1"].labels
    assert recover(rig.sched).ok and rig.state("btq-1") == "parked"


def two_dead(tmp_path: Path) -> Rig:
    """Two running beads of ours whose recorded sessions are both gone (the second started by hand)."""
    rig = started(tmp_path, "btq-2")
    rig.start("btq-2")
    for bead in ("btq-1", "btq-2"):
        rig.runtime.end(rig.key(bead))
    return rig


def crash_in_the_batch(rig: Rig) -> None:
    """A crash inside open_resumes' transaction, after the first resume's row is written."""
    real = rig.journal.set_state
    calls: list[str] = []

    def set_state(*args: object, **kwargs: object) -> object:
        calls.append(str(args[1]))
        if len(calls) == 2:
            raise SimulatedCrash("open_resumes")
        return real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    rig.journal.set_state = set_state  # type: ignore[method-assign]


@pytest.mark.parametrize("point", ["recovery.journals", "in-batch", "recovery.sessions"])
def test_crash_around_the_deferred_resume_batch(tmp_path: Path, point: str) -> None:
    """Task 8 r2: recovery opens both dead sessions' resumes in one transaction. A crash before it, inside
    it or after it leaves both open or neither; recovery launches neither, and pickup launches one."""
    rig = two_dead(tmp_path)
    if point == "in-batch":
        rig.restart()
        crash_in_the_batch(rig)
    else:
        rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    opened = sorted(op.bead for op in rig.journal.ops_open())
    assert opened == (["btq-1", "btq-2"] if point == "recovery.sessions" else [])
    assert len(rig.runtime.launches) == 2
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.resumes == (0 if point == "recovery.sessions" else 2)
    assert sorted(op.bead for op in rig.journal.ops_open()) == ["btq-1", "btq-2"]
    assert all(op.kind is OpKind.RESUME for op in rig.journal.ops_open())
    assert len(rig.runtime.launches) == 2                       # recovery launched neither
    assert rig.pickup() is Outcome.BUSY
    assert len(rig.runtime.launches) == 3 and rig.runtime.coders() == ["btq-1"]
    assert [op.bead for op in rig.journal.ops_open()] == ["btq-2"]       # waiting on the role
