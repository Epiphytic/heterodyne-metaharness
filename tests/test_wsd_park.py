import os
import sqlite3
import threading
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, PauseAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, git_repo, make_rig

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsUnavailable
from heterodyne.wsd.journal import JournalBusy, Op, OpKind
from heterodyne.wsd.park import (
    ESCALATE_POINTS,
    PARK_MARK,
    PARK_POINTS,
    RELEASE_POINTS,
    RESUME_POINTS,
    Launch,
    NotReleasable,
    resumable,
)
from heterodyne.wsd.runtime import LaunchSpec, Liveness
from heterodyne.wsd.states import Reason
from heterodyne.wsd.workstream import REPO_KEY, ConfigInvalid, Limits, WorkstreamSettings, place


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


@pytest.mark.parametrize("point", RESUME_POINTS)
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


def test_resume_uses_the_recorded_identity_after_a_config_change(tmp_path: Path) -> None:
    """Finding 4: the operator changed the default profile and repository since the launch. The resume is
    the recorded session in the recorded worktree, never a new placement."""
    rig = parked(tmp_path)
    first = rig.runtime.launches[0]
    other = git_repo(tmp_path / "repos" / "other")
    rig.ws = WorkstreamSettings(WS, {"default": other}, "coder", "p-two", rig.ws.profiles, rig.ws.limits)
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
    rig.ws = WorkstreamSettings(WS, rig.ws.repos, "builder", rig.ws.coder_profile, rig.ws.profiles,
                                rig.ws.limits)
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


def test_uncertain_launch_keeps_the_role_until_the_list_settles_it(tmp_path: Path) -> None:
    """Finding 16: an uncertain launch is never retried blind. The workstream holds LAUNCH_UNCERTAIN; the
    replay waits while the session is listed UNKNOWN and finishes once it is listed live."""
    rig = parked(tmp_path)
    rig.runtime.launch_uncertain = 1
    assert resume(rig) is Launch.UNCERTAIN
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    rig.replay_open()
    assert len(rig.runtime.launches) == 2 and rig.journal.ops_open() != []
    rig.runtime.set(rig.key("btq-1"), Liveness.LIVE)
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}
    assert len(rig.runtime.launches) == 2 and rig.journal.ops_open() == []


def test_uncertain_launch_that_never_started_is_launched_again(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.runtime.launch_uncertain = 1
    assert resume(rig) is Launch.UNCERTAIN
    rig.runtime.end(rig.key("btq-1"))                              # the runtime confirms it is not running
    rig.replay_open()
    assert rig.runtime.coders() == ["btq-1"] and rig.state("btq-1") == "running"
    assert rig.journal.holds(WS) == {}


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


@pytest.fixture
def busy() -> Generator[list[sqlite3.Connection]]:
    held: list[sqlite3.Connection] = []
    try:
        yield held
    finally:
        for db in held:
            db.rollback()
            db.close()


def lock_journal(rig: Rig, held: list[sqlite3.Connection]) -> sqlite3.Connection:
    """Another process holds the journal's write lock, and wsd's connection does not wait for it."""
    other = sqlite3.connect(rig.root / "state" / "wsd.db", isolation_level=None)
    held.append(other)
    other.execute("BEGIN IMMEDIATE")
    rig.journal.db.execute("PRAGMA busy_timeout = 0")
    return other


@pytest.mark.parametrize("outcome", ["started", "failed", "failed-once"])
def test_a_busy_journal_after_the_launch_is_never_an_uncertain_launch(
        tmp_path: Path, busy: list[sqlite3.Connection], outcome: str) -> None:
    """Task 3: only `runtime.launch` is inside the guard's catch-all. A journal write that fails after it
    (the DONE record, or the failure count) propagates as JournalBusy: nothing is recorded, no
    LAUNCH_UNCERTAIN hold, no budget spent, and the replay settles it from the session list.
    `failed-once`: the journal is busy for the failure count only, and free again for anything after."""
    rig = at_unlabelled(tmp_path)
    if outcome != "started":
        rig.runtime.launch_failures = 1
    [op] = rig.journal.ops_open()
    if outcome == "failed-once":
        real = rig.journal.op_failed

        def op_failed(op_id: str) -> int:
            rig.journal.op_failed = real
            raise JournalBusy("SQLITE_BUSY")

        rig.journal.op_failed = op_failed
        with rig.parker.entry(), pytest.raises(JournalBusy):
            rig.parker.launch(op)
    else:
        other = lock_journal(rig, busy)
        with rig.parker.entry(), pytest.raises(JournalBusy):
            rig.parker.launch(op)
        other.rollback()
    assert rig.journal.holds(WS) == {}
    [still] = rig.journal.ops_open()
    assert (still.step, still.attempts) == ("unlabelled", 0)
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
    assert [s.resume for s in rig.runtime.launches] == [False, True] and rig.runtime.coders() == ["btq-1"]


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


class Worker(threading.Thread):
    """A test thread that keeps what its body raised, so `finish` can report it."""

    def __init__(self, body: Callable[[], object], name: str) -> None:
        super().__init__(name=name, daemon=True)
        self.body = body
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.body()
        except BaseException as exc:  # noqa: BLE001 - re-raised by finish() in the test's thread
            self.error = exc


def finish(*threads: Worker) -> None:
    """Join every thread that started, then require each to have ended without raising."""
    started = [t for t in threads if t.ident is not None]
    for t in started:
        t.join(10)
    assert [t.name for t in started if t.is_alive()] == []
    errors = [t.error for t in started if t.error is not None]
    if errors:
        raise errors[0]


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

    def launch(spec: LaunchSpec) -> None:
        calls.append(spec.session_key)
        real(spec)

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
    """The journal is busy exactly when the escalation is opened (once), as if another connection took
    the write lock there."""
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
