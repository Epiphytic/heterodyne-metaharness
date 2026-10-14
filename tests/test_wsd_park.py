import os
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fakes.checkpoints import CrashAt, PauseAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, Worker, finish, git_repo, make_rig

from heterodyne.sandbox.spec import short_id
from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsUnavailable
from heterodyne.wsd.daemon import Wsd
from heterodyne.wsd.journal import JournalBusy, Op, OpKind, ResolvedEnd
from heterodyne.wsd.park import (
    DEFER_STEPS,
    ESCALATE_POINTS,
    PARK_MARK,
    PARK_POINTS,
    QUOTA,
    RELEASE_POINTS,
    RESUME_POINTS,
    UNRECEIPTED,
    Launch,
    NotReleasable,
    resumable,
)
from heterodyne.wsd.runtime import EndCause, Ended, LaunchSpec, Liveness, Started
from heterodyne.wsd.states import Reason
from heterodyne.wsd.workstream import REPO_KEY, ConfigInvalid, Limits, place


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig's queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def running(tmp_path: Path, cp: Recorder | None = None, limits: Limits | None = None) -> Rig:
    rig = make_rig(tmp_path, cp=cp, limits=limits)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")       # the bead btq-1 will wait on
    rig.start("btq-1")
    (rig.worktree("btq-1") / "work.txt").write_text("half done")
    return rig


def parked(tmp_path: Path, limits: Limits | None = None) -> Rig:
    """btq-1 parked on btq-2, and btq-2 since closed: resumable."""
    rig = running(tmp_path, limits=limits)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    return rig


def wip_commits(rig: Rig) -> list[str]:
    out = gitwip.git(rig.worktree("btq-1"), "log", "--format=%H", f"--grep={PARK_MARK}")
    return out.split()


def resume(rig: Rig, ref: str | None = None) -> Launch:
    with rig.parker.entry():
        return rig.parker.resume(rig.beads.show(WS, "btq-1"), ref=ref)


def reason(rig: Rig, bead: str = "btq-1") -> Reason | None:
    row = rig.journal.state(WS, bead)
    assert row is not None
    return row.reason


# --- park ---

def test_park_sequence(tmp_path: Path) -> None:
    rig = running(tmp_path)
    key = rig.key("btq-1")
    rig.restart(Recorder())
    assert rig.parker.park("btq-1", ("btq-2",), why="needs the schema", ref="msg-7").value == "parked"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in PARK_POINTS] == list(PARK_POINTS)
    bead = rig.world.beads["btq-1"]
    assert bead.status == "in_progress"                            # never unclaimed
    assert PARKED in bead.labels and ("btq-2", "blocks") in bead.deps
    assert len(wip_commits(rig)) == 1
    assert gitwip.git(rig.worktree("btq-1"), "status", "--porcelain") == ""
    [comment] = bead.comments
    assert "btq-2" in comment and "needs the schema" in comment
    assert rig.runtime.stops == [key]
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "parked" and row.reason is Reason.BLOCKED_ON_BEAD
    assert row.detail == "btq-2"
    progress = [(e.kind, e.ref) for e in rig.journal.events_since(0) if e.bead == "btq-1"]
    assert ("state:parking", "msg-7") in progress and ("state:parked", "msg-7") in progress
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("point", PARK_POINTS)
def test_crash_at_every_park_point_completes_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",), why="w")
    rig.restart()
    rig.replay_open()
    if point == "lock.waiting":                                     # died at the door: nothing happened
        assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
        return
    bead = rig.world.beads["btq-1"]
    assert PARKED in bead.labels and bead.deps == [("btq-2", "blocks")]
    assert len(bead.comments) == 1
    assert len(wip_commits(rig)) == 1
    assert rig.state("btq-1") == "parked"
    assert rig.journal.ops_open() == []
    assert rig.runtime.live() == [] and len(rig.runtime.launches) == 1     # never relaunched while parked


def test_park_stops_every_session_of_the_bead(tmp_path: Path) -> None:
    """A second, unrecorded session of the bead (one listed as UNKNOWN, say) is stopped too: a park never
    commits under a session that may still write."""
    rig = running(tmp_path)
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "other-key", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.park("btq-1", ("btq-2",))
    assert sorted(rig.runtime.stops) == sorted([first.session_key, "other-key"])
    assert rig.state("btq-1") == "parked"


def test_unconfirmed_stop_holds_and_never_spends_budget(tmp_path: Path) -> None:
    """Finding 10/11: a stop the runtime can't confirm is a hold, not a failure. No WIP commit, no label,
    no budget, no `needs-human`, however often it repeats; it completes once the stop is confirmed."""
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.runtime.stop_failures = 5
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    for _ in range(4):
        rig.replay_open()
    assert reason(rig) is Reason.STOP_UNCONFIRMED
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    bead = rig.world.beads["btq-1"]
    assert PARKED not in bead.labels and NEEDS_HUMAN not in bead.labels and wip_commits(rig) == []
    [op] = rig.journal.ops_open()
    assert op.attempts == 0 and op.step == "intent"
    rig.replay_open()
    assert rig.state("btq-1") == "parked"


def test_git_failure_retries_then_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=2))
    gitdir = Path(gitwip.git(rig.worktree("btq-1"), "rev-parse", "--absolute-git-dir").strip())
    (gitdir / "index.lock").write_text("")                          # every `git add` fails
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    assert reason(rig) is Reason.PARK_FAILED
    assert PARKED not in rig.world.beads["btq-1"].labels           # no label before the WIP commit
    rig.replay_open()                                               # second failure
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.PARK_FAILED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.journal.ops_open() == []


def test_park_without_a_session_record_escalates(tmp_path: Path) -> None:
    """Finding 11: the worktree comes from the record, never from the current placement. No record: no
    commit anywhere, a human looks."""
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.LAUNCH_UNRECORDED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and wip_commits(rig) == []


def test_park_with_an_unreadable_record_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.beads["btq-1"].metadata[RECORD_KEY] = "{not json"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.LAUNCH_UNRECORDED and wip_commits(rig) == []


def test_park_into_a_foreign_worktree_escalates(tmp_path: Path) -> None:
    """Finding 15: a record naming a worktree of another repository (same path layout, same branch name)
    fails the common-dir check, and nothing is committed there."""
    rig = running(tmp_path)
    other = git_repo(tmp_path / "elsewhere" / "proj")
    foreign = other.parent / "proj-btq-btq-1"
    gitwip.git(other, "worktree", "add", "-q", "-b", "btq/btq-1", str(foreign))
    bead = rig.world.beads["btq-1"]
    bead.metadata[RECORD_KEY] = bead.metadata[RECORD_KEY].replace(str(rig.worktree("btq-1")), str(foreign))
    base = gitwip.git(other, "rev-parse", "HEAD").strip()
    bead.notes += f"\nworker={bead.assignee}; repository={rig.repo}; base={base}; worktree={foreign}"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.WORKTREE_FAILED
    assert gitwip.git(foreign, "log", "--format=%H", f"--grep={PARK_MARK}").split() == []


def test_beads_outage_during_park_never_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.fault("dep", RuntimeError("dolt down"), times=2)
    for _ in range(2):
        with pytest.raises(BeadsUnavailable):
            rig.replay_open() if rig.journal.ops_open() else rig.parker.park("btq-1", ("btq-2",))
        assert rig.state("btq-1") == "parking"
        assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.replay_open()
    assert rig.state("btq-1") == "parked"


def test_park_of_a_bead_not_ours_is_abandoned(tmp_path: Path) -> None:
    rig = running(tmp_path)
    head = gitwip.git(rig.worktree("btq-1"), "rev-parse", "HEAD")
    rig.world.beads["btq-1"].assignee = "someone-else"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.CLAIM_LOST
    assert PARKED not in rig.world.beads["btq-1"].labels
    # review r1: the worktree of a bead that is no longer ours is never committed into
    assert gitwip.git(rig.worktree("btq-1"), "rev-parse", "HEAD") == head and wip_commits(rig) == []
    assert gitwip.git(rig.worktree("btq-1"), "status", "--porcelain") == "?? work.txt"


def test_replayed_park_of_a_bead_lost_meanwhile_commits_nothing(tmp_path: Path) -> None:
    rig = running(tmp_path)
    head = gitwip.git(rig.worktree("btq-1"), "rev-parse", "HEAD")
    rig.restart(CrashAt("park.stopped"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.world.beads["btq-1"].assignee = "someone-else"
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.CLAIM_LOST
    assert gitwip.git(rig.worktree("btq-1"), "rev-parse", "HEAD") == head and wip_commits(rig) == []


def test_operator_hold_is_not_resumable(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    bead = rig.beads.show(WS, "btq-1")
    assert HELD in bead.labels and not resumable(bead)
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "held" and row.reason is Reason.HELD_BY_OPERATOR


def test_question_blocker_is_waiting_input(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.add("btq-q", labels=["kind:question"])
    rig.world.beads["btq-q"].labels.remove("agent:wsd")
    rig.parker.park("btq-1", ("btq-q",), why="which schema?")
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "waiting_input" and row.reason is Reason.WAITING_ON_OPERATOR


def test_park_needs_a_blocker_or_a_hold(tmp_path: Path) -> None:
    rig = running(tmp_path)
    with pytest.raises(ValueError, match="blocker"):
        rig.parker.park("btq-1", ())
    assert rig.journal.ops_open() == []


# --- resume and the launch guard ---

def test_resume_sequence(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    assert resumable(rig.beads.show(WS, "btq-1"))
    rig.restart(Recorder())
    assert resume(rig, ref="msg-9") is Launch.STARTED
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in RESUME_POINTS] == list(RESUME_POINTS)
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.ref) == ("btq-1", True, "msg-9")
    assert spec.worktree == rig.worktree("btq-1")
    assert spec.session_key == rig.runtime.launches[0].session_key
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "running"


@pytest.mark.parametrize("point", [p for p in RESUME_POINTS if p.split(".")[1] not in UNRECEIPTED])
def test_crash_at_every_resume_point_launches_once(tmp_path: Path, point: str) -> None:
    """Finding 7 among them: at `resume.unlabelled!` the label is gone but the step is not journaled. The
    replay reads that as its own unlabel and goes on to launch; it is never a cancellation."""
    rig = parked(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.restart()
    rig.replay_open()
    assert len([s for s in rig.runtime.launches if s.resume]) == 1
    assert rig.runtime.coders() == ["btq-1"]
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("step", UNRECEIPTED)
def test_crash_between_dispatch_and_receipt_holds_the_resume(tmp_path: Path, step: str) -> None:
    """AU-3 §4.3: a generation dispatched with no receipt is never dispatched again, nor settled from the
    session list: the bead is STUCK, whether or not the session started."""
    rig = parked(tmp_path)
    rig.restart(CrashAt(f"resume.{step}"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    calls = rig.runtime.calls
    rig.restart()
    rig.replay_open()
    assert rig.runtime.calls == calls
    assert rig.journal.state(WS, "btq-1").reason is Reason.UNEXPECTED_STATE    # type: ignore[union-attr]
    assert rig.state("btq-1") == "stuck"
    assert [op.kind for op in rig.journal.ops_open()] == []


def test_resume_uses_the_recorded_identity_after_a_config_change(tmp_path: Path) -> None:
    """Finding 4: the operator changed the default profile and repository since the launch. The resume is
    the recorded session in the recorded worktree, never a new placement."""
    rig = parked(tmp_path)
    first = rig.runtime.launches[0]
    other = git_repo(tmp_path / "repos" / "other")
    rig.ws = replace(rig.ws, repos={"default": other}, coder_profile="p-two")
    rig.restart()
    assert resume(rig) is Launch.STARTED
    spec = rig.runtime.launches[-1]
    assert (spec.profile, spec.session_key, spec.worktree) == (first.profile, first.session_key,
                                                               first.worktree)


def test_resume_abandoned_when_blocked_again(tmp_path: Path) -> None:
    """Between the decision to resume and the replay, the bead gained an open blocker: the resume ends
    with the bead still parked, and nothing is launched."""
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    assert resume(rig) is Launch.ENDED
    assert [s.resume for s in rig.runtime.launches] == [False]
    assert PARKED in rig.world.beads["btq-1"].labels and rig.state("btq-1") == "parked"


def test_crash_at_resume_shelved_stays_parked_once(tmp_path: Path) -> None:
    """The point the guard reaches only when it shelves: the replay shelves again, idempotently."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    rig.restart(CrashAt("resume.shelved!"))
    with pytest.raises(SimulatedCrash):
        rig.replay_open()
    rig.restart()
    rig.replay_open()
    assert rig.world.beads["btq-1"].labels.count(PARKED) == 1 and rig.state("btq-1") == "parked"
    assert [s.resume for s in rig.runtime.launches] == [False] and rig.journal.ops_open() == []


def test_hold_added_after_the_unlabel_shelves_the_bead(tmp_path: Path) -> None:
    """The operator held the bead between the unlabel and the replay: the guard puts `v2:parked` back and
    records it as HELD. Nothing is launched."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.world.beads["btq-1"].labels.append(HELD)
    rig.restart()
    rig.replay_open()
    assert PARKED in rig.world.beads["btq-1"].labels and rig.state("btq-1") == "held"
    assert [s.resume for s in rig.runtime.launches] == [False] and rig.journal.ops_open() == []


@pytest.mark.parametrize("change", ["routing", "design"])
def test_guard_revalidates_routing_and_design_approval(tmp_path: Path, change: str) -> None:
    """Findings 5 and 6: btq's post-claim checks run again right before the launch. A bead that moved to
    another workstream, or lost its design approval, is escalated and not launched."""
    rig = parked(tmp_path)
    bead = rig.world.beads["btq-1"]
    if change == "routing":
        bead.labels[bead.labels.index("ws:alpha")] = "ws:beta"
    else:
        del bead.metadata["design_approval"]
    assert resume(rig) is Launch.ENDED
    assert reason(rig) is Reason.ROUTING_CHANGED and NEEDS_HUMAN in bead.labels
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_refuses_a_lost_claim(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.world.beads["btq-1"].assignee = "someone-else"
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.CLAIM_LOST
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_waits_while_another_bead_holds_the_coder_role(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.start("btq-3")
    assert resume(rig) is Launch.WAIT
    assert rig.runtime.coders() == ["btq-3"]
    [op] = rig.journal.ops_open()
    assert op.kind is OpKind.RESUME and op.attempts == 0


@pytest.mark.parametrize("liveness", [Liveness.LIVE, Liveness.UNKNOWN])
def test_guard_counts_an_old_role_session_after_a_role_rename(tmp_path: Path, liveness: Liveness) -> None:
    """r2 finding 1: btq-3 was launched as `coder` and the operator has since renamed the role. Its
    session, live or unknown, still holds the one coder role: the resume waits and nothing launches."""
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.start("btq-3")
    rig.runtime.set(rig.key("btq-3"), liveness)
    rig.ws = replace(rig.ws, coder_role="builder")
    rig.restart()
    assert resume(rig) is Launch.WAIT
    assert rig.runtime.coders() == ["btq-3"] and not any(s.resume for s in rig.runtime.launches)
    [op] = rig.journal.ops_open()
    assert op.kind is OpKind.RESUME and op.attempts == 0


def test_no_runtime_waits_without_budget_or_escalation(tmp_path: Path) -> None:
    """Finding 10: RuntimeUnavailable is a hold. It never counts as a launch failure."""
    rig = parked(tmp_path, limits=Limits(launch_failures_before_human=1))
    rig.runtime.up = False
    for _ in range(3):
        assert (resume(rig) if not rig.journal.ops_open() else rig.parker.launch(rig.journal.ops_open()[0])
                ) is Launch.WAIT
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    [op] = rig.journal.ops_open()
    assert op.attempts == 0


def test_confirmed_launch_failure_retries_then_escalates(tmp_path: Path) -> None:
    rig = parked(tmp_path, limits=Limits(launch_failures_before_human=2))
    rig.runtime.launch_failures = 2
    assert resume(rig) is Launch.FAILED
    assert reason(rig) is Reason.LAUNCH_FAILED and rig.runtime.coders() == []
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_uncertain_launch_holds_and_is_never_retried(tmp_path: Path) -> None:
    """Finding 16, as AU-3 changed it (§4.2 step 5): a launch the runtime can't confirm or deny leaves no
    receipt, so the bead is STUCK `unexpected_state` at once. It is never launched again, and the session
    list never settles it, whether the session is listed live or ended. Release refuses it (§4.3)."""
    rig = parked(tmp_path)
    rig.runtime.launch_uncertain = 1
    assert resume(rig) is Launch.ENDED
    assert reason(rig) is Reason.UNEXPECTED_STATE and rig.state("btq-1") == "stuck"
    assert rig.journal.holds(WS) == {} and rig.journal.ops_open() == []
    calls = rig.runtime.calls
    for liveness in (Liveness.LIVE, None):
        if liveness is None:
            rig.runtime.end(rig.key("btq-1"))
        else:
            rig.runtime.set(rig.key("btq-1"), liveness)
        rig.replay_open()
        rig.pickup()
        assert rig.runtime.calls == calls and rig.state("btq-1") == "stuck"
    with pytest.raises(NotReleasable, match="no launch receipt"):
        rig.parker.release("btq-1")


# --- release and escalate ---

def held(tmp_path: Path) -> Rig:
    rig = running(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    return rig


def test_release_of_a_held_parked_bead_makes_it_resumable(tmp_path: Path) -> None:
    rig = held(tmp_path)
    rig.restart(Recorder())
    assert rig.parker.release("btq-1", ref="msg-r").value == "parked"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in RELEASE_POINTS] == list(RELEASE_POINTS)
    bead = rig.beads.show(WS, "btq-1")
    assert HELD not in bead.labels and resumable(bead)
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("point", RELEASE_POINTS[1:])
def test_crash_at_every_release_point_completes_once(tmp_path: Path, point: str) -> None:
    rig = held(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "parked" and rig.journal.ops_open() == []
    assert HELD not in rig.world.beads["btq-1"].labels


def test_release_of_a_stuck_running_bead_resumes_through_the_guard(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(launch_failures_before_human=1))
    rig.runtime.end(rig.key("btq-1"))
    rig.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.parker.release("btq-1").value == "resuming"
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.runtime.coders() == ["btq-1"]
    assert rig.runtime.launches[-1].session_key == rig.runtime.launches[0].session_key


def test_release_stops_unrecorded_sessions_before_writing_a_record(tmp_path: Path) -> None:
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "stray", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.escalate("btq-1", Reason.LAUNCH_UNRECORDED)
    assert rig.parker.release("btq-1").value == "resuming"
    assert "stray" in rig.runtime.stops and "stray" not in rig.runtime.listed
    assert rig.beads.show(WS, "btq-1").record() is not None


@pytest.mark.parametrize("point", ["release.stopped!", "release.recorded!"])
def test_crash_while_release_replaces_a_record_completes_once(tmp_path: Path, point: str) -> None:
    """The two points release reaches only when it replaces a missing record: a replay stops the stray
    session (again, harmlessly), writes the record once and opens one resume."""
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "stray", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.escalate("btq-1", Reason.LAUNCH_UNRECORDED)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    rig.restart()
    rig.replay_open()
    assert "stray" not in rig.runtime.listed
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.session_key == first.session_key
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
    assert rig.runtime.coders() == ["btq-1"] and len(rig.runtime.launches) == 1


def test_only_held_or_stuck_beads_are_releasable(tmp_path: Path) -> None:
    rig = running(tmp_path)
    with pytest.raises(NotReleasable):
        rig.parker.release("btq-1")
    with pytest.raises(NotReleasable):
        rig.parker.release("btq-404")


@pytest.mark.parametrize("point", ESCALATE_POINTS)
def test_crash_at_every_escalate_point_labels_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.escalate("btq-1", Reason.UNEXPECTED_STATE)
    rig.restart()
    rig.replay_open()
    assert rig.world.beads["btq-1"].labels.count(NEEDS_HUMAN) == 1
    assert rig.state("btq-1") == "stuck" and rig.journal.ops_open() == []


def test_guard_never_launches_beside_an_unknown_session_of_its_bead(tmp_path: Path) -> None:
    """Finding 6: the replay finds the bead's own session listed but not confirmed live. It holds; it
    neither launches a second one nor assumes the first is running."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.runtime.adopt(rig.runtime.launches[0], Liveness.UNKNOWN)
    rig.restart()
    rig.replay_open()
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    assert len(rig.runtime.launches) == 1 and rig.journal.ops_open() != []


# --- additions to the plan's tests: guard checks the plan's tests do not reach on their own ---

def at_unlabelled(tmp_path: Path) -> Rig:
    """btq-1 parked and resumable, its resume journaled at `unlabelled`, wsd restarted before the guard."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.restart()
    return rig


def test_unavailable_runtime_is_a_hold_even_when_it_lists_sessions(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.runtime.available = lambda: False
    assert resume(rig) is Launch.WAIT
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_runtime_down_during_park_holds_without_budget(tmp_path: Path) -> None:
    """Task 5: a down runtime confirms no stop (it raises and keeps the session). The park holds
    RUNTIME_UNAVAILABLE at `intent`, commits nothing and spends nothing, and completes once it is up."""
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    key = rig.key("btq-1")
    rig.runtime.up = False
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    rig.replay_open()
    assert reason(rig) is Reason.STOP_UNCONFIRMED and Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert key in rig.runtime.listed and wip_commits(rig) == []
    [op] = rig.journal.ops_open()
    assert (op.step, op.attempts) == ("intent", 0)
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.runtime.up = True
    rig.replay_open()
    assert rig.state("btq-1") == "parked" and rig.runtime.stops == [key]


def test_a_workstream_hold_stops_the_guard(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.journal.hold(WS, Reason.ACTIONS_UNRECONCILED, "btq-9")
    assert resume(rig) is Launch.WAIT
    assert [s.resume for s in rig.runtime.launches] == [False]
    [op] = rig.journal.ops_open()
    assert op.attempts == 0


def test_resume_of_a_bead_escalated_meanwhile_is_stuck(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    assert resume(rig) is Launch.ENDED
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.NEEDS_HUMAN
    assert PARKED in rig.world.beads["btq-1"].labels and [s.resume for s in rig.runtime.launches] == [False]


def test_guard_ends_a_launch_the_operator_escalated(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.NEEDS_HUMAN
    assert [s.resume for s in rig.runtime.launches] == [False] and rig.journal.ops_open() == []


def test_guard_escalates_a_missing_record(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.LAUNCH_UNRECORDED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_escalates_a_worktree_that_fails_verification(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    rig.world.beads["btq-1"].notes = ""                             # btq's provenance note is gone
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.WORKTREE_FAILED
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_escalates_an_unrunnable_bead_whose_session_is_listed(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    rig.runtime.adopt(rig.runtime.launches[0])
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.UNEXPECTED_STATE
    assert len(rig.runtime.launches) == 1


def test_guard_holds_beside_a_live_session_under_another_key(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "other-key", first.label, first.worktree,
                                  resume=False))
    rig.replay_open()
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    assert len(rig.runtime.launches) == 1 and rig.journal.ops_open() != []


def test_guard_finds_the_recorded_session_already_live(tmp_path: Path) -> None:
    rig = at_unlabelled(tmp_path)
    rig.runtime.adopt(rig.runtime.launches[0])
    calls: list[str] = []
    real = rig.runtime.launch
    rig.runtime.launch = lambda spec: (calls.append(spec.session_key), real(spec))[1]
    [op] = rig.journal.ops_open()
    with rig.parker.entry():
        assert rig.parker.launch(op) is Launch.LIVE
    assert calls == [] and rig.state("btq-1") == "running"         # never launched beside it


def test_resume_of_a_bead_blocked_again_writes_nothing(tmp_path: Path) -> None:
    """The resume itself sees the new blocker: `v2:parked` is never taken off (and put back by the guard)."""
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    assert not resumable(rig.beads.show(WS, "btq-1"))
    before = len(rig.world.calls)
    assert resume(rig) is Launch.ENDED
    assert [call for _, call in rig.world.calls[before:] if call not in ("show", "comments")] == []
    assert rig.state("btq-1") == "parked" and reason(rig) is Reason.BLOCKED_ON_BEAD


def test_crash_right_after_a_stuck_park_still_escalates(tmp_path: Path) -> None:
    """escalate_from ends the park and opens the escalation in one transaction: a crash before the label
    leaves the escalation open, and the replay labels the bead."""
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    rig.restart(CrashAt("escalate.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart()
    [op] = rig.journal.ops_open()
    assert op.kind is OpKind.ESCALATE and rig.state("btq-1") == "stuck"
    rig.replay_open()
    assert rig.world.beads["btq-1"].labels.count(NEEDS_HUMAN) == 1 and rig.journal.ops_open() == []


@pytest.mark.parametrize("outcome", ["started", "failed"])
def test_a_busy_journal_after_the_launch_is_never_an_uncertain_launch(
        tmp_path: Path, outcome: str) -> None:
    """Task 3, as AU-3 changed it: only `runtime.launch` is inside the guard's catch-all. A journal write
    that fails after it propagates as JournalBusy: no LAUNCH_UNCERTAIN hold, no budget spent.
    `started`: the receipt write fails, so the generation was dispatched with no receipt, and its
    replay holds it, never launching again (§4.3). `failed`: the receipt is journaled and the failure
    count fails; the replay takes the receipt's tail once, then the next attempt launches."""
    rig = at_unlabelled(tmp_path)
    if outcome == "failed":
        rig.runtime.launch_failures = 1
    [op] = rig.journal.ops_open()
    name = "receipt_put" if outcome == "started" else "op_failed"
    real = getattr(rig.journal, name)

    def once(*args: object) -> object:
        setattr(rig.journal, name, real)
        raise JournalBusy("SQLITE_BUSY")

    setattr(rig.journal, name, once)
    with rig.parker.entry(), pytest.raises(JournalBusy):
        rig.parker.launch(op)
    assert rig.journal.holds(WS) == {}
    [still] = rig.journal.ops_open()
    assert (still.step, still.attempts) == ("dispatched", 0)
    calls = rig.runtime.calls
    rig.replay_open()
    assert rig.runtime.calls == calls
    if outcome == "started":
        assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.UNEXPECTED_STATE
        assert rig.runtime.coders() == ["btq-1"]
        return
    [still] = rig.journal.ops_open()
    assert still.attempts == 1 and "generation" not in still.data
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
    assert [s.resume for s in rig.runtime.launches] == [False, True] and rig.runtime.coders() == ["btq-1"]
    assert [e.outcome for e in rig.journal.launches(rig.key("btq-1"))] == ["abandoned", "launched"]


def test_release_refuses_a_bead_with_an_open_operation(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.restart(CrashAt("escalate.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.escalate("btq-1", Reason.UNEXPECTED_STATE)
    rig.restart()
    assert rig.state("btq-1") == "stuck"
    with pytest.raises(NotReleasable):
        rig.parker.release("btq-1")
    [op] = rig.journal.ops_open()
    assert op.kind is OpKind.ESCALATE


def test_entry_admits_one_operation_at_a_time(tmp_path: Path) -> None:
    """A park in progress holds the workstream lock; a second caller waits at the door until it ends."""
    held = PauseAt("park.stopped")
    door = threading.Event()

    def cp(name: str) -> None:
        if name == "lock.waiting" and threading.current_thread().name == "second":
            door.set()
        held(name)

    rig = running(tmp_path)
    rig.restart()
    rig.deps = type(rig.deps)(rig.journal, rig.beads, rig.gate, rig.runtime, rig.deps.reconciler, cp)
    rig.parker = type(rig.parker)(rig.ws, rig.deps)
    inside = threading.Event()
    states: list[str | None] = []

    def second() -> None:
        with rig.parker.entry():
            states.append(rig.state("btq-1"))
            inside.set()

    first = Worker(lambda: rig.parker.park("btq-1", ("btq-2",)), "first")
    other = Worker(second, "second")
    try:
        first.start()
        assert held.reached.wait(10)
        other.start()
        assert door.wait(10)
        assert not inside.wait(0.2)              # the park holds the lock
        held.go.set()
        assert inside.wait(10)
        assert states == ["parked"]              # admitted only after the park finished
    finally:
        held.go.set()
        finish(first, other)


@pytest.mark.parametrize("change", ["repo", "profile", "ambiguous"])
def test_place_refuses_what_the_workstream_does_not_have(tmp_path: Path, change: str) -> None:
    rig = make_rig(tmp_path)
    bead = rig.world.add("btq-1")
    if change == "repo":
        bead.metadata[REPO_KEY] = "elsewhere"
    elif change == "profile":
        bead.labels.append("role:coder=p-nine")
    else:
        bead.labels.extend(["role:coder=p-one", "role:coder=p-two"])
    with pytest.raises(ConfigInvalid):
        place(rig.ws, rig.beads.show(WS, "btq-1"))


def test_place_takes_the_profile_override(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", labels=["role:coder=p-two"])
    spot = place(rig.ws, rig.beads.show(WS, "btq-1"))
    assert (spot.profile, spot.worktree) == ("p-two", rig.worktree("btq-1"))
    assert spot.label.startswith("btq-1 · coder · ")


def test_release_that_cannot_place_a_missing_record_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path)
    bead = rig.world.beads["btq-1"]
    del bead.metadata[RECORD_KEY]
    rig.parker.escalate("btq-1", Reason.LAUNCH_UNRECORDED)
    bead.metadata[REPO_KEY] = "elsewhere"
    assert rig.parker.release("btq-1").value == "stuck"
    assert reason(rig) is Reason.CONFIG_INVALID and NEEDS_HUMAN in bead.labels
    assert rig.beads.show(WS, "btq-1").record() is None and rig.journal.ops_open() == []


# --- review r1 ---

def counting(rig: Rig) -> list[str]:
    """Record every runtime.launch call, whatever it does."""
    calls: list[str] = []
    real = rig.runtime.launch

    def launch(spec: LaunchSpec) -> Started:
        calls.append(spec.session_key)
        return real(spec)

    rig.runtime.launch = launch
    return calls


def test_another_beads_uncertain_launch_holds_the_guard(tmp_path: Path) -> None:
    """D17: the guard settles only its own operation's LAUNCH_UNCERTAIN. Another bead's hold makes it
    wait, even with nothing listed."""
    rig = parked(tmp_path)
    rig.journal.hold(WS, Reason.LAUNCH_UNCERTAIN, "btq-3")
    calls = counting(rig)
    assert resume(rig) is Launch.WAIT
    assert calls == [] and rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-3"}
    [op] = rig.journal.ops_open()
    assert op.attempts == 0


def busy_escalation(rig: Rig) -> None:
    """A one-shot JournalBusy when the escalation is opened: a late failure inside the transaction that
    already counted the attempt, which must roll the count back. Another connection can't take the write
    lock there (BEGIN IMMEDIATE already holds it), so the failure is injected."""
    real = rig.journal.op_open

    def op_open(kind: OpKind, ws: str, bead: str, data: dict[str, str] | None = None) -> Op:
        if kind is OpKind.ESCALATE:
            rig.journal.op_open = real
            raise JournalBusy("SQLITE_BUSY")
        return real(kind, ws, bead, data)

    rig.journal.op_open = op_open


def test_the_launch_that_spends_the_budget_escalates_with_its_count(tmp_path: Path) -> None:
    """review r1: the failure count and the escalation commit together. A busy journal at the escalation
    undoes the count too, so the restart neither finds a spent budget on an open operation nor launches:
    the replay fails again and escalates."""
    rig = parked(tmp_path, limits=Limits(launch_failures_before_human=1))
    rig.runtime.failing_beads.add("btq-1")
    busy_escalation(rig)
    with pytest.raises(JournalBusy):
        resume(rig)
    [op] = rig.journal.ops_open()
    assert (op.kind, op.attempts) == (OpKind.RESUME, 0) and rig.state("btq-1") == "resuming"
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.LAUNCH_FAILED
    assert rig.world.beads["btq-1"].labels.count(NEEDS_HUMAN) == 1 and rig.journal.ops_open() == []
    assert [s.resume for s in rig.runtime.launches] == [False] and rig.runtime.coders() == []


def test_the_park_that_spends_the_budget_escalates_with_its_count(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    gitdir = Path(gitwip.git(rig.worktree("btq-1"), "rev-parse", "--absolute-git-dir").strip())
    (gitdir / "index.lock").write_text("")
    busy_escalation(rig)
    with pytest.raises(JournalBusy):
        rig.parker.park("btq-1", ("btq-2",))
    [op] = rig.journal.ops_open()
    assert (op.kind, op.attempts) == (OpKind.PARK, 0)
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.PARK_FAILED
    assert rig.world.beads["btq-1"].labels.count(NEEDS_HUMAN) == 1 and rig.journal.ops_open() == []
    assert wip_commits(rig) == [] and PARKED not in rig.world.beads["btq-1"].labels


def test_a_launch_whose_budget_is_already_spent_is_escalated_not_tried(tmp_path: Path) -> None:
    """A journal that recorded the spent budget without the escalation (written before this fix, or by a
    crash in a path that had no shared transaction) is escalated on replay; the runtime is not called."""
    rig = at_unlabelled(tmp_path)
    [op] = rig.journal.ops_open()
    rig.journal.op_failed(op.op_id)
    rig.journal.op_failed(op.op_id)                                 # the default budget is 2
    calls = counting(rig)
    rig.replay_open()
    assert calls == [] and rig.state("btq-1") == "stuck" and reason(rig) is Reason.LAUNCH_FAILED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and rig.journal.ops_open() == []


def test_a_park_whose_budget_is_already_spent_is_escalated_not_tried(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.restart(CrashAt("park.stopped"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart()
    [op] = rig.journal.ops_open()
    rig.journal.op_failed(op.op_id)
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.PARK_FAILED
    assert wip_commits(rig) == [] and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


# --- g08sd: crash-loop accounting in the launch guard ---

def picked(tmp_path: Path, cp: Recorder | None = None) -> Rig:
    """btq-1 claimed and launched by pickup (its generation journaled), btq-2 someone else's bead."""
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup().value == "started"
    return rig


def dies(rig: Rig) -> None:
    rig.runtime.end(rig.key("btq-1"))


def classes(rig: Rig) -> list[str | None]:
    launched = [e for e in rig.journal.launches_of(WS, "btq-1") if e.outcome == "launched"]
    return [rig.journal.crash_class(e) for e in launched]


def crash_looped(rig: Rig, launches: int = 2) -> None:
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.CRASH_LOOP
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert len(rig.runtime.launches) == launches and rig.runtime.coders() == []
    assert rig.journal.ops_open() == []


def blocker(rig: Rig, name: str) -> str:
    rig.world.add(name)
    rig.world.beads[name].labels.remove("agent:wsd")
    return name


def test_a_session_that_dies_on_every_launch_escalates_before_a_third_launch(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()                                               # the sweep's resume: one death counted
    assert rig.journal.crash_count(WS, "btq-1") == 1 and len(rig.runtime.launches) == 2
    dies(rig)
    rig.pickup()
    crash_looped(rig)
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.detail == "generation 2 died after 60s; 2 in a row"
    assert classes(rig) == ["died", "died"]


def test_a_death_consumed_by_a_park_is_counted_at_resume(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    for gate in ("btq-3", "btq-4"):
        dies(rig)
        rig.parker.park("btq-1", (blocker(rig, gate),))      # its own listing ends the dead session
        assert rig.state("btq-1") == "parked" and rig.runtime.stops == []
        rig.world.close(gate)
        rig.pickup()
    crash_looped(rig)


def test_a_death_consumed_by_a_defer_is_counted_at_undefer(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    for _ in range(2):
        dies(rig)
        rig.parker.defer("btq-1", QUOTA, rig.clock() + 3600)
        assert rig.state("btq-1") == "deferred"
        rig.clock.advance(3600)
        rig.pickup()
    crash_looped(rig)


@pytest.mark.parametrize("point", [*PARK_POINTS[1:], *(f"park.{s}" for s in DEFER_STEPS)])
def test_a_crash_during_park_or_defer_replay_still_counts(tmp_path: Path, point: str) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        if point.startswith("park.defer."):
            rig.parker.defer("btq-1", QUOTA, rig.clock() + 3600)
        else:
            rig.parker.park("btq-1", (blocker(rig, "btq-3"),))
    rig.restart()
    rig.replay_open()
    rig.world.close("btq-3") if "btq-3" in rig.world.beads else rig.clock.advance(3600)
    rig.pickup()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 2
    assert rig.journal.crash_count(WS, "btq-1") == 1 and classes(rig) == ["died", None]


@pytest.mark.parametrize("point", ["inside", "accounted", *ESCALATE_POINTS[:2]])
@pytest.mark.parametrize("kind", ["resume", "undefer"])
def test_crash_at_every_accounting_point_counts_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     kind: str, point: str) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()                                               # generation 2, one death counted
    dies(rig)
    if kind == "undefer":
        rig.parker.defer("btq-1", QUOTA, rig.clock() + 3600)
        rig.clock.advance(3600)
    if point == "inside":
        real = rig.journal.crash_account

        def torn(*args: object) -> int:
            real(*args)  # pyright: ignore[reportArgumentType]
            raise SimulatedCrash("inside the accounting transaction")

        monkeypatch.setattr(rig.journal, "crash_account", torn)
        with pytest.raises(SimulatedCrash):
            rig.pickup()
        monkeypatch.undo()
        assert rig.journal.crash_count(WS, "btq-1") == 1 and classes(rig)[-1] is None     # rolled back
    else:
        rig.restart(CrashAt("resume.accounted" if point == "accounted" else point))   # undefer: a RESUME op
        with pytest.raises(SimulatedCrash):
            rig.pickup()
    rig.restart()
    rig.replay_open()
    rig.pickup()
    crash_looped(rig)
    assert classes(rig) == ["died", "died"] and rig.journal.crash_count(WS, "btq-1") == 2
    escalations = rig.journal.db.execute("SELECT count(*) FROM ops WHERE kind = ? AND bead = 'btq-1'",
                                         (OpKind.ESCALATE.value,)).fetchone()
    assert escalations == (1,)


@pytest.mark.parametrize("point", ["inside", "accounted"])
@pytest.mark.parametrize("kind", ["resume", "undefer"])
def test_crash_at_every_accounting_point_counts_a_resolved_end_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, point: str) -> None:
    """btq-wpyv5: the carried end is read inside the accounting transaction and never changed, so a replay
    of the guard decides the same way: one `lifetime` row, which resets the count."""
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()                                               # generation 2, one death counted
    rig.runtime.lifetime(rig.key("btq-1"))
    resolve(rig)
    if kind == "undefer":
        rig.parker.defer("btq-1", QUOTA, rig.clock() + 3600)
        rig.clock.advance(3600)
    if point == "inside":
        real = rig.journal.crash_account

        def torn(*args: object) -> int:
            real(*args)  # pyright: ignore[reportArgumentType]
            raise SimulatedCrash("inside the accounting transaction")

        monkeypatch.setattr(rig.journal, "crash_account", torn)
        with pytest.raises(SimulatedCrash):
            rig.pickup()
        monkeypatch.undo()
        assert rig.journal.crash_count(WS, "btq-1") == 1 and classes(rig)[-1] is None     # rolled back
    else:
        rig.restart(CrashAt("resume.accounted"))
        with pytest.raises(SimulatedCrash):
            rig.pickup()
    rig.restart()
    rig.replay_open()
    rig.pickup()
    assert classes(rig)[:2] == ["died", "lifetime"] and rig.journal.crash_count(WS, "btq-1") == 0
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 3


def test_a_new_claims_guard_has_nothing_to_account(tmp_path: Path) -> None:
    """The third kind of operation, pickup, never reaches the accounting point: a new claim's intent
    accounts every earlier generation `reset` before its guard runs."""
    rig = picked(tmp_path, cp=Recorder())
    assert isinstance(rig.cp, Recorder)
    assert not any(p.endswith(".accounted") for p in rig.cp.seen)
    assert classes(rig) == [None]


def test_repeated_outage_and_death_cycles_are_bounded(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    for _ in range(2):
        dies(rig)
        rig.runtime.up = False
        assert rig.pickup().value == "held"
        rig.clock.advance(10 * 3600)                           # past the deadline, unseen
        rig.runtime.up = True
        rig.pickup()
    crash_looped(rig)
    assert "lifetime" not in classes(rig)


def test_a_live_lifetime_stop_resets_and_an_intentional_stop_does_not(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()
    assert rig.journal.crash_count(WS, "btq-1") == 1
    rig.runtime.lifetime(rig.key("btq-1"))
    rig.pickup()
    assert rig.journal.crash_count(WS, "btq-1") == 0
    dies(rig)
    rig.pickup()
    assert rig.journal.crash_count(WS, "btq-1") == 1 and rig.state("btq-1") == "running"
    rig.parker.park("btq-1", (blocker(rig, "btq-3"),))       # a live stop: neither counted nor a reset
    assert rig.runtime.stops == [rig.key("btq-1")]
    rig.world.close("btq-3")
    rig.pickup()
    assert rig.journal.crash_count(WS, "btq-1") == 1
    dies(rig)
    rig.pickup()
    crash_looped(rig, launches=5)
    assert classes(rig) == ["died", "lifetime", "died", "stopped", "died"]


def test_release_resets_and_accounts_pending_generations_as_reset(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    for _ in range(2):
        dies(rig)
        rig.pickup()
    crash_looped(rig)
    assert rig.parker.release("btq-1").value == "resuming"
    assert rig.journal.crash_count(WS, "btq-1") == 0
    rig.replay_open()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 3
    dies(rig)
    rig.pickup()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 4
    assert rig.journal.crash_count(WS, "btq-1") == 1 and classes(rig) == ["died", "died", "died", None]

    other = picked(tmp_path / "unaccounted")
    dies(other)
    other.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
    other.parker.release("btq-1")
    assert classes(other) == ["reset"] and other.journal.crash_count(WS, "btq-1") == 0
    other.replay_open()
    assert other.state("btq-1") == "running" and other.journal.crash_count(WS, "btq-1") == 0


def resolve(rig: Rig) -> None:
    """btq-wpyv5: `wsctl resolve --discard` of btq-1's latest generation, through Wsd.resolve_one on the
    rig's own scheduler and journal: the fake's Resolution carries the end, and its record goes."""
    key = rig.key("btq-1")
    generation = rig.runtime.generations[key]
    parts = SimpleNamespace(schedulers={WS: rig.sched}, journal=rig.journal)
    reply = Wsd.resolve_one(cast(Wsd, SimpleNamespace(parts=parts)), WS, short_id(key), generation, "discard",
                            None)
    assert reply.result == "ok", reply.message
    assert rig.runtime.ended(key) is None and rig.runtime.intents == {}


def never_ran(rig: Rig) -> None:
    key = rig.key("btq-1")
    del rig.runtime.listed[key]
    rig.runtime.ends[key] = Ended(rig.runtime.generations[key], EndCause.STOPPED, False, None)


@pytest.mark.parametrize("ending, klass, count", [
    ("stopped", "stopped", 1), ("lifetime", "lifetime", 0), ("died", "died", 2), ("never ran", "died", 2)])
def test_a_resolved_generation_is_accounted_from_its_end(tmp_path: Path, ending: str, klass: str,
                                                         count: int) -> None:
    """A resolve removes the session's record, but carries its end into the journal (docs/wsd.md §5), so
    the next guard accounts the generation as it would have from the record."""
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()                                               # generation 2, one death counted
    key = rig.key("btq-1")
    if ending == "never ran":
        never_ran(rig)
    else:
        rig.runtime.end(key, EndCause(ending))                 # its commits couldn't land
    resolve(rig)
    rig.pickup()
    assert classes(rig)[:2] == ["died", klass] and rig.journal.crash_count(WS, "btq-1") == count
    if count == 2:
        crash_looped(rig)
    else:
        assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 3


def test_a_release_before_the_next_launch_still_resets_a_resolved_generation(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()
    rig.runtime.end(rig.key("btq-1"))
    resolve(rig)
    rig.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
    rig.parker.release("btq-1")
    assert classes(rig) == ["died", "reset"] and rig.journal.crash_count(WS, "btq-1") == 0
    rig.replay_open()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 3
    assert classes(rig) == ["died", "reset", None]


def test_a_resolved_death_at_the_bound_escalates_before_the_next_launch(tmp_path: Path) -> None:
    """The case accounting at resolve time would get wrong: the guard has the death to account, so it
    escalates, and launch 3 never happens."""
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()
    assert rig.journal.crash_count(WS, "btq-1") == 1
    dies(rig)
    resolve(rig)
    rig.pickup()
    crash_looped(rig)
    assert classes(rig) == ["died", "died"]


def test_two_resolved_live_stops_do_not_escalate(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    for _ in range(2):
        rig.runtime.end(rig.key("btq-1"), EndCause.STOPPED)    # a live stop whose land was refused
        resolve(rig)
        rig.pickup()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 3
    assert rig.journal.crash_count(WS, "btq-1") == 0 and classes(rig) == ["stopped", "stopped", None]


LIFETIME_END = ResolvedEnd(cause="lifetime", reached_running=True, ran_seconds=60)


def test_a_resolved_end_is_only_for_its_own_generation(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    key = rig.key("btq-1")
    dies(rig)
    del rig.runtime.ends[key]                                  # no end of its own: removed by hand
    j = rig.journal
    for ws, bead, other, generation in ((WS, "btq-1", key, 2), (WS, "btq-1", "sk-other", 1),
                                        (WS, "btq-2", key, 1), ("beta", "btq-1", key, 1)):
        j.emit_resolved(ws, bead, "action=discard tip=none", f"{ws}-{bead}-{other}-{generation}", other,
                        generation, LIFETIME_END)
    rig.pickup()
    assert classes(rig) == ["died", None] and j.crash_count(WS, "btq-1") == 1

    j.db.execute("DELETE FROM meta WHERE key LIKE 'crash_gen:%' OR key LIKE 'crash_run:%'")   # pre-g08sd
    j.emit_resolved(WS, "btq-1", "action=discard tip=none", "older", key, 1, LIFETIME_END)
    dies(rig)
    rig.pickup()
    assert classes(rig) == ["legacy", "died", None]            # an older generation's row is never read

    other = picked(tmp_path / "rebuilt")
    okey = other.key("btq-1")
    dies(other)
    del other.runtime.ends[okey]
    other.journal.emit_resolved(WS, "btq-1", "action=discard tip=none", "rebuilt", okey, 1, LIFETIME_END)
    other.journal.db.execute("UPDATE launches SET rebuilt_at = '2026-10-14T00:00:00Z' WHERE session_key = ? "
                             "AND generation = 1", (okey,))
    other.pickup()
    assert classes(other)[0] == "reset" and other.journal.crash_count(WS, "btq-1") == 0


def test_an_unknown_carried_cause_is_a_death(tmp_path: Path) -> None:
    """Required by the wpyv5 approval: a carried cause the guard doesn't know is a death, as `ended()`
    maps one, even with reached_running set."""
    rig = picked(tmp_path)
    key = rig.key("btq-1")
    dies(rig)
    del rig.runtime.ends[key]
    rig.journal.emit_resolved(WS, "btq-1", "action=discard tip=none", "unknown", key, 1,
                              ResolvedEnd(cause="healthy", reached_running=True, ran_seconds=60))
    rig.pickup()
    assert classes(rig) == ["died", None] and rig.journal.crash_count(WS, "btq-1") == 1
    assert rig.state("btq-1") == "running"


def test_a_matching_runtime_death_overrides_a_healthy_carried_end(tmp_path: Path) -> None:
    """Required by the wpyv5 approval: the runtime's ended record of the same generation is the evidence;
    a conflicting healthy `crash_end:` row for it is never read."""
    rig = picked(tmp_path)
    key = rig.key("btq-1")
    dies(rig)
    assert rig.runtime.ended(key) == Ended(1, EndCause.DIED, True, 60)
    rig.journal.emit_resolved(WS, "btq-1", "action=discard tip=none", "conflict", key, 1, LIFETIME_END)
    rig.pickup()
    assert classes(rig) == ["died", None] and rig.journal.crash_count(WS, "btq-1") == 1


@pytest.mark.parametrize("released", [False, True])
def test_a_hand_removed_directory_still_counts_one_death(tmp_path: Path, released: bool) -> None:
    """Manual step 4 with wsd not running: nothing carries the end, so the next guard counts a death, even
    after a live stop, unless a release resets first."""
    rig = picked(tmp_path)
    key = rig.key("btq-1")
    rig.runtime.end(key, EndCause.STOPPED)
    del rig.runtime.ends[key]                                  # the directory, record and all, removed
    if released:
        rig.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
        rig.parker.release("btq-1")
        rig.replay_open()
    else:
        rig.pickup()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 2
    assert rig.journal.crash_count(WS, "btq-1") == (0 if released else 1)
    assert classes(rig) == ["reset" if released else "died", None]


def test_refused_launches_keep_their_own_budget(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    rig.parker.park("btq-1", (blocker(rig, "btq-3"),))
    rig.world.close("btq-3")
    rig.runtime.launch_failures = 2
    rig.pickup()
    rig.pickup()
    assert reason(rig) is Reason.LAUNCH_FAILED and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert classes(rig) == ["stopped"] and rig.journal.crash_count(WS, "btq-1") == 0


def test_generations_before_g08sd_are_legacy(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()
    rig.journal.db.execute("DELETE FROM meta WHERE key LIKE 'crash_%'")    # as journaled before g08sd
    assert classes(rig) == [None, None]
    dies(rig)
    rig.pickup()
    assert classes(rig) == ["legacy", "died", None] and rig.journal.crash_count(WS, "btq-1") == 1
    assert rig.state("btq-1") == "running"


def test_the_count_survives_a_restart(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.pickup()
    rig.restart()                                              # a new wsd over the same journal
    dies(rig)
    rig.pickup()
    crash_looped(rig)


def test_an_unreadable_end_holds_and_counts_nothing(tmp_path: Path) -> None:
    rig = picked(tmp_path)
    dies(rig)
    rig.runtime.ended_failures = 1
    rig.pickup()
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS) and len(rig.runtime.launches) == 1
    assert classes(rig) == [None] and NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.pickup()
    assert rig.state("btq-1") == "running" and len(rig.runtime.launches) == 2
    assert classes(rig) == ["died", None]
