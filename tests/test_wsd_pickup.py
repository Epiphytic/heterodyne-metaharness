import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Many, PauseAt, Recorder, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wsd_env import WS, Rig, Worker, finish, make_rig

from heterodyne.wsd import ids
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsUnavailable
from heterodyne.wsd.journal import JournalBusy
from heterodyne.wsd.runtime import LaunchSpec, Liveness
from heterodyne.wsd.scheduler import POINTS, Outcome
from heterodyne.wsd.states import BeadState, Reason, WsState
from heterodyne.wsd.sweep import Swept, sweep
from heterodyne.wsd.workstream import Limits, WorkstreamSettings


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig's queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def test_clean_pickup_passes_every_point_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", title="Do   the\nthing")
    assert rig.pickup(ref="msg-1") is Outcome.STARTED
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in POINTS] == list(POINTS)
    [spec] = rig.runtime.launches
    assert spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    assert spec.label == "btq-1 · coder · Do the thing"
    assert spec.worktree == rig.worktree("btq-1") and spec.ref == "msg-1" and not spec.resume
    assert rig.state("btq-1") == "running"
    assert rig.journal.snapshot(WS).state is WsState.RUNNING


@pytest.mark.parametrize("point", POINTS)
def test_crash_at_every_pickup_point_replays_to_one_start(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.pickup()
    assert rig.world.claims == ["btq-1"]
    assert rig.world.worktrees == ["btq-1"]
    assert len(rig.runtime.launches) == 1
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_uncertain_claim_that_landed_is_used(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_claim_that_did_not_land_holds_and_retries(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"))
    assert rig.pickup() is Outcome.HELD                 # never "idle" with btq-1 still ready
    assert rig.journal.snapshot(WS).state is WsState.HELD
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and rig.journal.holds(WS) == {}


def test_unreadable_claim_holds_the_workstream(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    rig.world.fault("show", RuntimeError("dolt down"))
    assert rig.pickup() is Outcome.HELD
    assert Reason.CLAIM_UNCERTAIN in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]           # nothing else is claimed while it is unknown
    assert rig.pickup() is Outcome.BUSY             # read back as ours on the next trigger, and started
    assert rig.state("btq-1") == "running"
    assert Reason.CLAIM_UNCERTAIN not in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]


def test_lost_race_tries_the_next_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.stolen.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "dropped" and rig.state("btq-2") == "running"


def test_routing_change_is_never_executed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("Routing/design changed during claim; ask Bel"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "stuck"
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.world.beads["btq-1"].status == "in_progress"       # never unclaimed
    assert [s.bead for s in rig.runtime.launches] == ["btq-2"]


def test_unknown_repository_is_stuck_not_guessed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", metadata={"repo": "elsewhere"})
    assert rig.pickup() is Outcome.NOTHING
    assert rig.journal.state(WS, "btq-1") is not None
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CONFIG_INVALID
    assert rig.runtime.launches == []


def test_confirmed_launch_failure_tries_the_next_candidate(tmp_path: Path) -> None:
    """Finding 16: a launch the runtime confirms never started frees the role, so the next ready bead is
    tried in the same pickup. The failed one keeps its operation and is retried by later pickups until its
    budget runs out."""
    rig = make_rig(tmp_path, limits=Limits(launch_failures_before_human=2))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.failing_beads.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.runtime.coders() == ["btq-2"]
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("starting", Reason.LAUNCH_FAILED)
    assert rig.pickup() is Outcome.BUSY             # btq-1's replay waits: btq-2 holds the role
    rig.world.close("btq-2")
    rig.pickup()                                    # second failure: btq-1 escalates
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_uncertain_launch_keeps_the_role_and_holds(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    assert rig.world.claims == ["btq-1"] and rig.runtime.coders() == ["btq-1"]
    assert rig.pickup() is Outcome.HELD             # still listed as unknown: nothing else is tried
    rig.runtime.set(rig.key("btq-1"), Liveness.LIVE)
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_no_runtime_never_spends_launch_budget(tmp_path: Path) -> None:
    """Finding 10: a runtime that went away after the claim is a hold. The pickup waits at its worktree
    step, with no failure counted and no `needs-human`, and launches once the runtime is back."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"), limits=Limits(launch_failures_before_human=1))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.runtime.up = False
    for _ in range(3):
        assert rig.pickup() is Outcome.HELD
    [op] = rig.journal.ops_open()
    assert op.attempts == 0 and NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.runtime.up = True
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}


@pytest.mark.parametrize("change", ["routing", "design"])
def test_replayed_pickup_revalidates_before_launch(tmp_path: Path, change: str) -> None:
    """Findings 5 and 6: between the claim and a replayed launch the bead moved to another workstream or
    lost its design approval. The guard's re-run of btq's checks refuses, and a human decides."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    bead = rig.world.beads["btq-1"]
    if change == "routing":
        bead.labels.remove("ws:alpha")
    else:
        bead.metadata["design_approval"] = "approval-revoked"
    rig.restart()
    rig.pickup()
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in bead.labels
    assert rig.runtime.launches == []


def test_replayed_pickup_launches_what_it_chose(tmp_path: Path) -> None:
    """Finding 4: the default profile changed between the worktree step and the replay. The launch is the
    one the pickup journaled, recorded on the bead before it starts."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.ws = WorkstreamSettings(WS, rig.ws.repos, "coder", "p-two", rig.ws.profiles, rig.ws.limits)
    rig.restart()
    rig.pickup()
    [spec] = rig.runtime.launches
    assert spec.profile == "p-one" and spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.session_key == spec.session_key


def renamed(rig: Rig, role: str = "builder") -> None:
    """The operator renamed the coder role in the configuration, and wsd restarted."""
    rig.ws = WorkstreamSettings(WS, rig.ws.repos, role, rig.ws.coder_profile, rig.ws.profiles, rig.ws.limits)
    rig.restart()


@pytest.mark.parametrize("liveness", [Liveness.LIVE, Liveness.UNKNOWN])
def test_role_rename_never_frees_the_role_beside_an_old_session(tmp_path: Path, liveness: Liveness) -> None:
    """r2 finding 1: btq-1's session was launched as `coder`; the role is now `builder`. Live or unknown,
    that session still holds the one coder role: nothing new is claimed or launched."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.runtime.set(rig.key("btq-1"), liveness)
    renamed(rig)
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_crash_at_pickup_worktree_then_role_change_launches_the_recorded_role(tmp_path: Path) -> None:
    """r2 finding 1: the pickup chose its role at the intent. A rename after the worktree step changes
    nothing about this launch, and its session then holds the role under the new name too."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    renamed(rig)
    assert rig.pickup() is Outcome.BUSY             # the replay started it; the role is taken
    [spec] = rig.runtime.launches
    assert (spec.role, spec.session_key) == ("coder", ids.role_session("btq-1", "coder", "p-one"))
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and (rec.role, rec.session_key) == ("coder", spec.session_key)
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.BUSY and rig.world.claims == ["btq-1"]


def test_role_rename_before_the_worktree_step_escalates(tmp_path: Path) -> None:
    """The claim landed under `coder` but no worktree or launch was chosen yet: the pickup can't tell which
    role it is for, so a human decides. Nothing is launched."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.claimed"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    renamed(rig)
    rig.pickup()
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("stuck", Reason.CONFIG_INVALID)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and rig.runtime.launches == []


def test_replay_after_the_launch_keeps_every_field_the_launch_added(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """r2 finding 2: the launch enriched the record (as plan 4's runtime will, with a thread id and the
    model it reports) and wsd died before journaling it. The replay leaves the record byte for byte."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.launched!"))
    rig.world.add("btq-1")
    real = rig.runtime.launch

    def enriching(spec: LaunchSpec) -> None:
        real(spec)
        meta = rig.world.beads[spec.bead].metadata
        doc = json.loads(meta[RECORD_KEY])
        doc.update({"thread_id": "th-7", "reported_model": "m-9", "extra": {"nested": [1, None, "x"]}})
        meta[RECORD_KEY] = json.dumps(doc)

    monkeypatch.setattr(rig.runtime, "launch", enriching)
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    enriched = rig.world.beads["btq-1"].metadata[RECORD_KEY]
    rig.restart()
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.beads["btq-1"].metadata[RECORD_KEY] == enriched
    assert len(rig.runtime.launches) == 1 and rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_replay_into_a_record_of_another_launch_escalates(tmp_path: Path) -> None:
    """r2 finding 2: the bead already records a different launch than the one this pickup chose. The
    record is never overwritten and nothing is launched; a human decides."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    meta = rig.world.beads["btq-1"].metadata
    meta[RECORD_KEY] = json.dumps({"role": "coder", "profile": "p-two", "session_key": "other",
                                   "repo": str(rig.repo), "worktree": str(rig.worktree("btq-1"))})
    before = meta[RECORD_KEY]
    rig.restart()
    rig.pickup()
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("stuck", Reason.UNEXPECTED_STATE)
    assert meta[RECORD_KEY] == before and rig.runtime.launches == []


@pytest.mark.parametrize("crash", [False, True])
def test_first_pickup_shelved_before_its_launch_keeps_its_record(tmp_path: Path, crash: bool) -> None:
    """A blocker appeared between the worktree step and the launch. The guard writes the record first, so
    the shelved bead is an ordinary parked one: it resumes into the recorded session once unblocked,
    with no human release. A crash at `pickup.shelved!` shelves it again, once. The launch is a resume
    of a key no session ever ran under: the runtime creates it (`AgentRuntime.launch`, plan 4)."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.world.add("btq-3")
    rig.world.beads["btq-3"].labels.remove("agent:wsd")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    if crash:
        rig.restart(CrashAt("pickup.shelved!"))
        with pytest.raises(SimulatedCrash):
            rig.pickup()
    rig.restart()
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.beads["btq-1"].labels.count(PARKED) == 1 and rig.state("btq-1") == "parked"
    assert rig.runtime.launches == [] and rig.journal.ops_open() == []
    key = rig.key("btq-1")
    rig.world.close("btq-3")
    assert rig.pickup() is Outcome.RESUMED
    [spec] = rig.runtime.launches
    assert spec.session_key == key and spec.resume


def test_crash_between_worktree_and_provenance_escalates(tmp_path: Path) -> None:
    """btq made the worktree but died before writing its provenance note. The path is never trusted or
    reused: the bead is escalated and nothing is launched in it."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("worktree", RuntimeError("killed"), after=True)
    assert rig.pickup() is Outcome.HELD             # the worktree call's outcome is unknown
    assert rig.pickup() is Outcome.NOTHING          # replayed: the path exists without provenance
    assert rig.state("btq-1") == "stuck"
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.WORKTREE_FAILED
    assert rig.runtime.launches == []


def test_unsettled_action_blocks_a_replayed_launch(tmp_path: Path) -> None:
    """Finding 8: a hold stops every launch, replays included. A running bead whose session died is not
    relaunched while an action is unsettled."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.runtime.end(rig.key("btq-1"))
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    assert rig.pickup() is Outcome.HELD
    assert len(rig.runtime.launches) == 1 and rig.runtime.coders() == []
    rig.world.beads["btq-ap"].metadata["action_state"] = "succeeded"
    assert rig.pickup() is Outcome.BUSY
    assert [s.resume for s in rig.runtime.launches] == [False, True]


def test_closed_bead_with_an_unsettled_action_holds(tmp_path: Path) -> None:
    """Finding 9: closing a bead says nothing about its action."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-ap", labels=["kind:approval"], status="closed", metadata={"action_state": "uncertain"})
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}


def test_no_runtime_holds_without_claiming(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.runtime.up = False
    assert rig.pickup() is Outcome.HELD
    assert rig.world.claims == []
    assert rig.journal.snapshot(WS).holds == {Reason.RUNTIME_UNAVAILABLE: ""}


def test_beads_down_holds_and_never_reads_as_idle(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.snapshot(WS).state is WsState.HELD
    rig.world.down = False
    assert rig.pickup() is Outcome.STARTED
    assert rig.journal.holds(WS) == {}


def test_unsettled_action_holds_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "uncertain"})
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}
    assert rig.world.claims == []


def test_one_coder_session_at_a_time(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    for n in range(3):
        rig.world.add(f"btq-{n}")
    assert rig.pickup() is Outcome.STARTED
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.coders() == ["btq-0"]
    rig.world.close("btq-0")                        # the agent closed it; its session is still listed
    assert rig.pickup() is Outcome.STARTED          # the sweep stopped it first
    assert rig.state("btq-0") == "closed" and rig.runtime.coders() == ["btq-1"]


def test_lost_claim_stops_the_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].assignee = "someone:host:recovery"
    rig.pickup()
    assert rig.state("btq-1") == "stuck"
    assert rig.runtime.coders() == []


def test_dead_session_is_relaunched_as_the_same_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    key = rig.key("btq-1")
    rig.runtime.end(key)
    assert rig.pickup() is Outcome.BUSY             # resumed before any new work
    assert [(s.session_key, s.resume) for s in rig.runtime.launches] == [(key, False), (key, True)]
    assert rig.world.claims == ["btq-1"]


def test_unknown_liveness_never_starts_a_second_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    rig.runtime.set(rig.key("btq-1"), Liveness.UNKNOWN)
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


# --- never idle while an unblocked bead exists ---

KINDS = ("ok", "lost_race", "bad_repo", "launch_fails", "launch_uncertain", "uncertain_landed",
         "uncertain_missed", "blocked", "closed_blocker")


def settle(rig: Rig) -> None:
    """What the world does between triggers: an uncertain launch turns out live, and every bead with a
    live session is finished and closed by its agent."""
    for key, session in list(rig.runtime.listed.items()):
        if session.liveness is Liveness.UNKNOWN:
            rig.runtime.set(key, Liveness.LIVE)
    for key in rig.runtime.live():
        rig.world.close(rig.runtime.listed[key].bead)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(KINDS), min_size=0, max_size=6))
def test_never_idle_while_an_unblocked_bead_exists(tmp_path_factory: pytest.TempPathFactory,
                                                    kinds: list[str]) -> None:
    """The oracle, per pickup: STARTED, RESUMED and BUSY each mean exactly one coder session is listed;
    HELD means a hold names why; NOTHING means no ready bead is left and no coder runs. Faults are scoped
    to their bead, so one bead's trouble never hides another's."""
    rig = make_rig(tmp_path_factory.mktemp("never-idle"))
    rig.world.add("btq-zz-blocker")
    rig.world.beads["btq-zz-blocker"].labels.remove("agent:wsd")      # someone else's bead
    rig.world.add("btq-zz-done", status="closed")
    for n, kind in enumerate(kinds):
        bead = f"btq-{n}"
        rig.world.add(bead)
        if kind == "bad_repo":
            rig.world.beads[bead].metadata["repo"] = "nope"
        elif kind == "blocked":
            rig.world.beads[bead].deps.append(("btq-zz-blocker", "blocks"))
        elif kind == "closed_blocker":
            rig.world.beads[bead].deps.append(("btq-zz-done", "blocks"))
        elif kind == "lost_race":
            rig.world.stolen.add(bead)
        elif kind == "launch_fails":
            rig.runtime.failing_beads.add(bead)
        elif kind == "launch_uncertain":
            rig.runtime.uncertain_beads.add(bead)
        elif kind == "uncertain_landed":
            rig.world.fault("claim", RuntimeError("timeout"), after=True, bead=bead)
        elif kind == "uncertain_missed":
            rig.world.fault("claim", RuntimeError("timeout"), bead=bead)
    for _ in range(6 * len(kinds) + 6):
        outcome = rig.pickup()
        coders = rig.runtime.coders()
        assert len(coders) <= 1
        if outcome in (Outcome.STARTED, Outcome.RESUMED, Outcome.BUSY):
            assert len(coders) == 1
        elif outcome is Outcome.HELD:
            assert rig.journal.holds(WS)
        else:
            assert outcome is Outcome.NOTHING
            assert rig.world.ready_for(WS) == [] and coders == []
            if not rig.journal.ops_open():
                break
        settle(rig)
    else:
        pytest.fail("pickup never settled")
    assert "btq-zz-blocker" not in rig.world.claims
    for n, kind in enumerate(kinds):
        expected = {"ok": "closed", "closed_blocker": "closed", "launch_uncertain": "closed",
                    "uncertain_landed": "closed", "uncertain_missed": "closed", "lost_race": "dropped",
                    "bad_repo": "stuck", "launch_fails": "stuck", "blocked": None}[kind]
        assert rig.state(f"btq-{n}") == expected, (n, kind)


# --- parked beads in pickup (§5.2: resumable before ready; a parked bead never pre-empts) ---


def parked_rig(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    return rig


def test_parked_bead_never_preempts_the_running_one(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.STARTED
    rig.world.close("btq-2")                                        # btq-1 is resumable now
    assert rig.pickup() is Outcome.BUSY
    assert PARKED in rig.world.beads["btq-1"].labels
    rig.world.close("btq-3")
    assert rig.pickup() is Outcome.RESUMED
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.worktree) == ("btq-1", True, rig.worktree("btq-1"))


def test_resumable_beats_ready(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-0")                                          # sorts first in ready order
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED
    assert "btq-0" not in rig.world.claims


def test_held_bead_is_skipped_by_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    assert rig.pickup() is Outcome.NOTHING
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_parked_bead_with_a_listed_session_is_escalated(tmp_path: Path) -> None:
    """Beads say parked, the runtime lists a session of it: never resumed or guessed at."""
    rig = parked_rig(tmp_path)
    rig.runtime.adopt(rig.runtime.launches[0])
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.BUSY             # the listed session keeps the role
    assert rig.state("btq-1") == "stuck" and rig.world.claims == ["btq-1"]


def test_pickup_replays_an_interrupted_park(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.fault("dep", RuntimeError("dolt down"))
    with pytest.raises(BeadsUnavailable):
        rig.parker.park("btq-1", ("btq-2",))
    assert rig.pickup() is Outcome.NOTHING          # the park finished first; btq-1 waits on btq-2
    assert rig.state("btq-1") == "parked"


def test_unconfirmed_park_stop_keeps_the_coder_role(tmp_path: Path) -> None:
    """A park that can't confirm its session stopped holds the workstream (no budget, no `needs-human`):
    the session keeps the coder role and no new work is claimed until the stop is confirmed."""
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.runtime.stop_failures = 3
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.add("btq-3")
    for _ in range(2):
        assert rig.pickup() is Outcome.HELD
        assert rig.state("btq-1") == "parking" and rig.runtime.coders() == ["btq-1"]
    assert rig.world.claims == ["btq-1"] and NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    assert rig.pickup() is Outcome.STARTED          # the third stop is confirmed; the park completes
    assert rig.state("btq-1") == "parked" and rig.world.claims == ["btq-1", "btq-3"]


def test_config_change_never_touches_a_running_bead(tmp_path: Path) -> None:
    """The bead's repo metadata now names a repository wsd doesn't know. The running session was placed
    from its record, so nothing about it changes."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].metadata["repo"] = "gone"
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.world.claims == ["btq-1"]


def test_closed_bead_with_unconfirmed_stop_keeps_the_role(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.close("btq-1")
    rig.world.add("btq-2")
    rig.runtime.stop_failures = 1
    assert rig.pickup() is Outcome.BUSY
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("stuck", Reason.STOP_UNCONFIRMED)
    assert rig.pickup() is Outcome.STARTED          # the retried stop is confirmed
    assert rig.state("btq-1") == "closed"
    assert rig.world.claims == ["btq-1", "btq-2"]


class DoorWatch(Recorder):
    """Signals when the thread named `who` reaches the operation lock's door."""

    def __init__(self, who: str) -> None:
        super().__init__()
        self.who = who
        self.reached = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == "lock.waiting" and threading.current_thread().name == self.who:
            self.reached.set()


def test_pickup_waits_at_the_door_while_a_park_runs(tmp_path: Path) -> None:
    """Finding 13: a park has stopped the session (the coder role looks free) but not yet labelled the
    bead. A pickup arriving now waits on the operation lock; it never claims in that window."""
    pause, door = PauseAt("park.stopped!"), DoorWatch("pickup")
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.add("btq-3")
    rig.restart(Many(pause, door))
    outcome: list[Outcome] = []
    parker = Worker(lambda: rig.parker.park("btq-1", ("btq-2",)), "park")
    picker = Worker(lambda: outcome.append(rig.pickup()), "pickup")
    try:
        parker.start()
        assert pause.reached.wait(10)
        picker.start()
        assert door.reached.wait(10)
        assert outcome == [] and rig.world.claims == ["btq-1"] and rig.runtime.coders() == []
        pause.go.set()
    finally:
        pause.go.set()
        finish(parker, picker)
    assert outcome == [Outcome.STARTED]
    assert rig.state("btq-1") == "parked" and rig.world.claims == ["btq-1", "btq-3"]


def test_external_label_removal_is_not_a_release(tmp_path: Path) -> None:
    """Someone removed `v2:held` by hand. The journal still says HELD; nothing resumes until release."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    rig.world.beads["btq-1"].labels.remove(HELD)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.state("btq-1") == "held"
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_uncertain_hold_ends_with_its_operation(tmp_path: Path) -> None:
    """The uncertain launch's bead was closed meanwhile: the replay finds the claim gone and ends the
    operation, and the hold goes with it. The listed session is the sweep's to stop."""
    rig = parked_rig(tmp_path)
    rig.world.close("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.HELD           # the resume's launch is uncertain
    rig.world.close("btq-1")
    for op in rig.journal.ops_open():
        rig.parker.replay(op)
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CLAIM_LOST
    assert rig.pickup() is Outcome.NOTHING
    assert rig.runtime.coders() == [] and rig.state("btq-1") == "closed"


# --- Task 6 notes and the sweep's guards (deviations: tests the plan does not have) ---


def test_runtime_hold_stays_until_the_runtime_lists_sessions(tmp_path: Path) -> None:
    """Task 6 note 1: pickup clears RUNTIME_UNAVAILABLE only once `available()` and `sessions()` both
    answer. A runtime that says it is up but can't list keeps the hold, whatever else fails after it."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.journal.hold(WS, Reason.RUNTIME_UNAVAILABLE)      # set by a park stop, a release or the guard
    rig.runtime.list_failures = 1
    rig.world.down = True
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.RUNTIME_UNAVAILABLE: ""}
    rig.world.down = False
    assert rig.pickup() is Outcome.STARTED
    assert rig.journal.holds(WS) == {}


def test_another_beads_uncertain_launch_refuses_new_claims(tmp_path: Path) -> None:
    """Task 6 note 2: while any LAUNCH_UNCERTAIN hold is left, nothing new is claimed (the guard alone
    would only refuse the launch, after the claim)."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.journal.hold(WS, Reason.LAUNCH_UNCERTAIN, "btq-9")
    assert rig.pickup() is Outcome.HELD
    assert rig.world.claims == [] and rig.journal.ops_open() == []


def test_replayed_pickup_never_makes_a_worktree_for_a_lost_claim(tmp_path: Path) -> None:
    """Task 6 r1: ownership is checked again before the worktree is made (btq's `worktree` runs `owned`
    first), so a claim lost between the claim step and the replay ends the pickup with no worktree."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.claimed"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.world.beads["btq-1"].assignee = "codex:otherhost:x"
    rig.restart()
    rig.pickup()
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("stuck", Reason.CLAIM_LOST)
    assert rig.world.worktrees == [] and not rig.worktree("btq-1").exists()
    assert rig.runtime.launches == [] and rig.journal.ops_open() == []


class LockAt(Recorder):
    """Another process takes the journal's write lock at `point`, and wsd's connection does not wait."""

    def __init__(self, point: str, db: Path) -> None:
        super().__init__()
        self.point = point
        self.db = db
        self.other: sqlite3.Connection | None = None

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and self.other is None:
            self.other = sqlite3.connect(self.db, isolation_level=None)
            self.other.execute("BEGIN IMMEDIATE")


def test_a_busy_journal_mid_pickup_propagates_and_replays(tmp_path: Path) -> None:
    """Task 3: JournalBusy is never caught as a hold or a failed claim. It leaves the pickup at its last
    recorded step, and the next pickup's replay reads the claim back and starts it, once."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    lock = LockAt("pickup.claimed!", rig.root / "state" / "wsd.db")
    rig.restart(lock)
    rig.journal.db.execute("PRAGMA busy_timeout = 0")
    try:
        with pytest.raises(JournalBusy):
            rig.pickup()
    finally:
        if lock.other is not None:
            lock.other.rollback()
            lock.other.close()
    assert lock.other is not None
    [op] = rig.journal.ops_open()
    assert (op.step, op.attempts) == ("intent", 0) and rig.journal.holds(WS) == {}
    assert rig.world.claims == ["btq-1"] and rig.runtime.launches == []
    rig.restart()
    assert rig.pickup() is Outcome.BUSY
    assert len(rig.runtime.launches) == 1 and rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == [] and rig.world.claims == ["btq-1"]


def started(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    return rig


def reason(rig: Rig, bead: str = "btq-1") -> tuple[str, Reason | None]:
    row = rig.journal.state(WS, bead)
    assert row is not None
    return row.state.value, row.reason


def test_sweep_escalates_a_claim_the_journal_never_recorded(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.claim(WS, "btq-1")                     # claimed, and the journal lost
    assert rig.pickup() is Outcome.NOTHING
    assert reason(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and rig.runtime.launches == []


def test_sweep_escalates_a_routing_change_under_a_running_bead(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.remove("ws:alpha")
    rig.pickup()
    assert reason(rig) == ("stuck", Reason.ROUTING_CHANGED)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_sweep_records_needs_human_from_beads(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    rig.pickup()
    assert reason(rig) == ("stuck", Reason.NEEDS_HUMAN)


@pytest.mark.parametrize("change", ["held_label", "status", "foreign_session"])
def test_sweep_escalates_beads_and_journal_disagreeing(tmp_path: Path, change: str) -> None:
    rig = started(tmp_path)
    bead = rig.world.beads["btq-1"]
    if change == "held_label":
        bead.labels.append(HELD)                    # v2:held without v2:parked
    elif change == "status":
        bead.status = "blocked"
    else:
        spec = rig.runtime.launches[0]
        rig.runtime.adopt(LaunchSpec(WS, "btq-1", "coder", "p-two", "other-key", spec.label, spec.worktree,
                                     resume=False))
    rig.pickup()
    assert reason(rig) == ("stuck", Reason.UNEXPECTED_STATE)
    assert len(rig.runtime.launches) == 1
    # btq writes only to an in_progress bead of ours, so a bead whose status moved can't take the label;
    # the journal row is what says it is stuck.
    assert (NEEDS_HUMAN in bead.labels) is (change != "status")


@pytest.mark.parametrize("record", ["absent", "unreadable"])
def test_sweep_never_resumes_a_running_bead_without_a_record(tmp_path: Path, record: str) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    meta = rig.world.beads["btq-1"].metadata
    if record == "absent":
        del meta[RECORD_KEY]
    else:
        meta[RECORD_KEY] = "{not json"
    assert rig.pickup() is Outcome.NOTHING
    assert reason(rig) == ("stuck", Reason.LAUNCH_UNRECORDED)
    assert len(rig.runtime.launches) == 1


def test_sweep_drops_a_vanished_bead_and_closes_a_closed_one(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    rig.runtime.end(rig.key("btq-1"))
    del rig.world.beads["btq-1"]
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST)
    rig.runtime.end(rig.key("btq-2"))
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.NOTHING
    assert rig.state("btq-2") == "closed" and rig.runtime.stops == []


def test_sweep_marks_a_claim_taken_by_another_worker(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    rig.world.beads["btq-1"].assignee = "codex:otherhost:x"
    assert rig.pickup() is Outcome.NOTHING
    assert reason(rig) == ("stuck", Reason.CLAIM_LOST)
    assert len(rig.runtime.launches) == 1


def test_sweep_follows_a_parked_beads_blockers(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-5")
    rig.world.beads["btq-5"].labels.remove("agent:wsd")
    rig.world.beads["btq-1"].deps.append(("btq-5", "blocks"))
    rig.pickup()
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state, row.detail) == (BeadState.PARKED, "btq-2,btq-5")
    rig.world.beads["btq-5"].labels.append("kind:approval")      # now it waits on the operator too
    rig.pickup()
    assert reason(rig) == ("waiting_input", Reason.WAITING_ON_OPERATOR)


def test_sweep_escalates_a_parked_bead_the_journal_has_running(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    rig.world.beads["btq-1"].labels.append(PARKED)
    rig.pickup()
    assert reason(rig) == ("stuck", Reason.UNEXPECTED_STATE)


def test_paused_pickup_opens_no_operation(tmp_path: Path) -> None:
    """A paused workstream journals no pickup intent: btq lists nothing while paused, and pickup reads
    the flag itself before the ready list."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.state("btq-1") is None and rig.journal.ops_open() == []


def test_replayed_intent_finds_another_workers_claim(tmp_path: Path) -> None:
    """The claim read back belongs to another worker: the pickup is dropped as lost, with no worktree."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.intent"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    bead = rig.world.beads["btq-1"]
    bead.status, bead.assignee = "in_progress", "codex:otherhost:x"
    rig.restart()
    assert rig.pickup() is Outcome.NOTHING
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST)
    assert rig.world.worktrees == [] and rig.runtime.launches == []


def test_sweep_keeps_a_held_row_whose_claim_moved(tmp_path: Path) -> None:
    """HELD and STUCK rows wait for the operator's release, even once the bead is no longer ours."""
    rig = started(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    rig.world.beads["btq-1"].assignee = "codex:otherhost:x"
    rig.pickup()
    assert rig.state("btq-1") == "held"


def test_sweep_escalates_a_running_bead_the_journal_has_starting(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.journal.adopt(WS, "btq-1", BeadState.STARTING)        # no operation is open on it
    assert rig.pickup() is Outcome.BUSY
    assert reason(rig) == ("stuck", Reason.UNEXPECTED_STATE)


def test_sweep_escalates_an_unrecorded_bead_itself(tmp_path: Path) -> None:
    """A missing record is escalated by the sweep, never handed to the guard as a resume."""
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    with rig.parker.entry():
        assert sweep(rig.parker) == Swept(resumes=0, held=1, stopped=0)
    assert reason(rig) == ("stuck", Reason.LAUNCH_UNRECORDED) and rig.journal.ops_open() == []
