import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, PauseAt, Recorder, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wsd_env import (
    WS,
    At,
    LockAt,
    Rig,
    Worker,
    block,
    finish,
    git_repo,
    make_rig,
    on_profile,
    own_profile,
    probe_lock,
    profile_key,
)

from heterodyne.wsd import ids
from heterodyne.wsd.accounts import Chosen, ConfiguredAccounts
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsUnavailable
from heterodyne.wsd.headroom import Deadline, quota_detail
from heterodyne.wsd.journal import JournalBusy
from heterodyne.wsd.launches import LaunchEntry
from heterodyne.wsd.park import UNRECEIPTED
from heterodyne.wsd.runtime import LaunchSpec, Liveness, Started
from heterodyne.wsd.scheduler import POINTS, Outcome, TriggerKind
from heterodyne.wsd.states import BeadState, Reason, WsState
from heterodyne.wsd.sweep import Swept, sweep
from heterodyne.wsd.usage import decide
from heterodyne.wsd.workstream import Limits, place


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


@pytest.mark.parametrize("point", [p for p in POINTS if p.split(".")[1] not in UNRECEIPTED])
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


@pytest.mark.parametrize("step", UNRECEIPTED)
def test_crash_between_dispatch_and_receipt_holds_the_pickup(tmp_path: Path, step: str) -> None:
    """AU-3 §4.3: a dispatched generation with no receipt is never dispatched again: the bead is STUCK,
    whether or not the session started."""
    rig = make_rig(tmp_path, cp=CrashAt(f"pickup.{step}"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    calls = rig.runtime.calls
    rig.restart()
    rig.pickup()
    assert rig.runtime.calls == calls
    assert rig.journal.state(WS, "btq-1").reason is Reason.UNEXPECTED_STATE    # type: ignore[union-attr]
    assert rig.state("btq-1") == "stuck"
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


def test_uncertain_launch_is_stuck_and_keeps_the_role(tmp_path: Path) -> None:
    """AU-3 §4.2 step 5: an uncertain launch leaves no receipt, so its bead is STUCK at once and never
    launched again. Its listed session still holds the coder role: the next bead waits for it to end."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.BUSY
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.UNEXPECTED_STATE and rig.state("btq-1") == "stuck"
    assert rig.runtime.calls == 1 and rig.runtime.coders() == ["btq-1"]
    rig.runtime.set(rig.key("btq-1"), Liveness.LIVE)
    assert rig.pickup() is Outcome.BUSY             # listed live: still never settled from the list
    assert rig.state("btq-1") == "stuck" and rig.runtime.calls == 1
    rig.runtime.end(rig.key("btq-1"))
    rig.pickup()
    assert rig.state("btq-1") == "stuck" and rig.state("btq-2") == "running"
    assert [s.bead for s in rig.runtime.launches] == ["btq-1", "btq-2"]


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
    rig.ws = replace(rig.ws, coder_profile="p-two")
    rig.restart()
    rig.pickup()
    [spec] = rig.runtime.launches
    assert spec.profile == "p-one" and spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.session_key == spec.session_key


def renamed(rig: Rig, role: str = "builder") -> None:
    """The operator renamed the coder role in the configuration, and wsd restarted."""
    rig.ws = replace(rig.ws, coder_role=role)
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


def test_role_rename_before_the_placement_escalates(tmp_path: Path) -> None:
    """The claim landed under `coder` but no placement was recorded yet: the pickup can't tell which
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
    model it reports) and wsd died after journaling its receipt (AU-3: a crash before the receipt is STUCK).
    The replay finishes from the receipt and leaves the record byte for byte."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.receipt"))
    rig.world.add("btq-1")
    real = rig.runtime.launch

    def enriching(spec: LaunchSpec) -> Started:
        started = real(spec)
        meta = rig.world.beads[spec.bead].metadata
        doc = json.loads(meta[RECORD_KEY])
        doc.update({"thread_id": "th-7", "reported_model": "m-9", "extra": {"nested": [1, None, "x"]}})
        meta[RECORD_KEY] = json.dumps(doc)
        return started

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
         "uncertain_missed", "blocked", "closed_blocker", "parks", "released", "deleted", "pinned",
         "quota_blocked", "quota_clears", "quota_race", "account_repointed")
QUOTA_KINDS = ("quota_blocked", "quota_clears", "quota_race", "account_repointed")


def gate_of(bead: str) -> str:
    return f"btq-zz-gate-{bead}"


def settle(rig: Rig, plan: dict[str, str] | None = None, done: set[tuple[str, str]] | None = None) -> None:
    """What the world does between triggers: an uncertain launch turns out live, and every bead with a
    live session is finished and closed by its agent. Per `plan`, once each: a "parks" bead parks on its
    gate bead, which closes at the next settle; a "released" bead's claim is handed back to the queue; a
    "deleted" bead is deleted while its launch is uncertain."""
    plan = plan or {}
    done = set() if done is None else done
    for bead, kind in plan.items():
        if kind == "parks" and ("parked", bead) in done and ("opened", bead) not in done:
            rig.world.close(gate_of(bead))
            done.add(("opened", bead))
    for key, session in list(rig.runtime.listed.items()):
        if session.liveness is Liveness.UNKNOWN:
            if plan.get(session.bead) == "deleted":
                rig.world.beads.pop(session.bead, None)
            else:
                rig.runtime.set(key, Liveness.LIVE)
    for key in rig.runtime.live():
        bead = rig.runtime.listed[key].bead
        kind = plan.get(bead, "")
        if kind in ("parks", "released") and (kind, bead) not in done:
            done.add((kind, bead))
            if kind == "parks":
                rig.parker.park(bead, (gate_of(bead),))
                done.add(("parked", bead))
            else:
                released(rig, bead)
        else:
            rig.world.close(bead)


def verdict(rig: Rig, bead: str, resume: bool = False) -> object:
    """The shared gate (`usage.decide`) for a bead as pickup sees it: a ready bead on its placement, a
    resumable one on its recorded profile; previous key from its launched entries."""
    shown = rig.beads.show(WS, bead)
    if resume:
        rec = shown.record()
        assert rec is not None
        profile, key = rec.profile, rec.session_key
    else:
        spot = place(rig.ws, shown)
        profile, key = spot.profile, spot.session_key
    launched = [e for e in rig.journal.launches(key) if e.outcome == "launched"]
    accounts = rig.ws.accounts
    assert accounts is not None
    return decide(accounts, rig.journal, profile, launched[-1].credential_key if launched else None,
                  rig.clock(), rig.ws.usage)


def quota_waiters(rig: Rig) -> list[str]:
    """Runnable PARKED/QUOTA waiters: the row, a bead of ours that is resumable, and no open operation."""
    rows = {r.bead for r in rig.journal.states(WS) if (r.state, r.reason) == (BeadState.PARKED, Reason.QUOTA)}
    return [b for b in resumable_now(rig) if b in rows and rig.journal.op_for(WS, b) is None]


class Racer(Recorder):
    """At `gate.checked`, a quota_race bead being claimed for the first time gets a trusted mark on its
    key: pickup's gate admitted it, and the guard's step 2 then gives a Deadline."""

    def __init__(self, rig: Rig, keys: dict[str, str]) -> None:
        super().__init__()
        self.rig = rig
        self.keys = keys
        self.raced: set[str] = set()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name != "gate.checked":
            return
        for op in self.rig.journal.ops_open(WS):
            if op.bead in self.keys and op.bead not in self.raced:
                self.raced.add(op.bead)
                block(self.rig, self.keys[op.bead], 1800)


def resumable_now(rig: Rig) -> list[str]:
    """Parked beads of ours that pickup must resume: in progress under their per-bead worker, parked,
    neither held nor `needs-human`, and with every blocker closed."""
    return sorted(b.id for b in rig.world.beads.values()
                  if b.assignee == rig.beads.bead_queue(WS, b.id).worker and b.status == "in_progress"
                  and PARKED in b.labels and HELD not in b.labels and NEEDS_HUMAN not in b.labels
                  and not rig.world.blocked(b))


def claimable_now(rig: Rig) -> list[str]:
    return [b.id for b in rig.world.ready_for(WS)
            if not any(x.startswith("session:") for x in b.labels) and PARKED not in b.labels]


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(KINDS), min_size=0, max_size=6))
def test_never_idle_while_an_unblocked_bead_exists(tmp_path_factory: pytest.TempPathFactory,
                                                    kinds: list[str]) -> None:
    """The oracle, per pickup: STARTED, RESUMED and BUSY each mean exactly one coder session is listed;
    HELD means a hold names why; NOTHING means no bead is ready, none is resumable, no quota waiter is left
    and no coder runs; STUCK means the same except that ready beads btq's claim can't take are left, each
    recorded as UNCLAIMABLE (or, AU-5, ACCOUNT_REPOINTED), or skipped for quota; DEFERRED means no coder
    runs, every ready, resumable or quota-waiting bead gates to a Deadline, and the wake is after now.
    AU-5's kinds: "quota_blocked" (its profile's key stays blocked), "quota_clears" (blocked for 30
    minutes), "quota_race" (blocked at `gate.checked`, after pickup's gate admitted it) and
    "account_repointed" (launched before on another key, under failover "none": never claimed). On
    DEFERRED the clock moves to the wake time, as the wake trigger would. Faults are scoped to their
    bead, so one bead's trouble never hides another's. The world also parks beads and unblocks them,
    hands claims back, deletes a bead under its open operation and pins beads to the workstream session.
    (Configuration changes across a crash are covered by
    test_replay_after_a_repository_change_uses_the_recorded_placement.)"""
    rig = make_rig(tmp_path_factory.mktemp("never-idle"))
    quota = {f"btq-{n}": own_profile(rig, f"q-{n}") for n, kind in enumerate(kinds) if kind in QUOTA_KINDS}
    racer = Racer(rig, {b: k for b, k in quota.items() if kinds[int(b[4:])] == "quota_race"})
    rig.restart(racer)
    rig.world.add("btq-zz-blocker")
    rig.world.beads["btq-zz-blocker"].labels.remove("agent:wsd")      # someone else's bead
    rig.world.add("btq-zz-done", status="closed")
    plan: dict[str, str] = {}
    for n, kind in enumerate(kinds):
        bead = f"btq-{n}"
        plan[bead] = kind
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
        elif kind in ("launch_uncertain", "deleted"):
            rig.runtime.uncertain_beads.add(bead)
        elif kind == "uncertain_landed":
            rig.world.fault("claim", RuntimeError("timeout"), after=True, bead=bead)
        elif kind == "uncertain_missed":
            rig.world.fault("claim", RuntimeError("timeout"), bead=bead)
        elif kind == "parks":
            rig.world.add(gate_of(bead))
            rig.world.beads[gate_of(bead)].labels.remove("agent:wsd")
        elif kind == "pinned":
            pinned(rig, bead)
        elif kind in QUOTA_KINDS:
            on_profile(rig, bead, f"q-{n}")
            if kind in ("quota_blocked", "quota_clears"):
                block(rig, quota[bead], 1800 if kind == "quota_clears" else 100 * 3600)
            elif kind == "account_repointed":
                key = ids.role_session(bead, "coder", f"q-{n}")
                rig.journal.launch_insert(LaunchEntry(key, 1, WS, bead, "coder", f"q-{n}", f"q-{n}",
                                                      "ck1-" + "0" * 32, "", False, "t", "t", None, None,
                                                      "launched"))
    done: set[tuple[str, str]] = set()
    for _ in range(8 * len(kinds) + 8):
        outcome = rig.pickup()
        coders = rig.runtime.coders()
        assert len(coders) <= 1
        if outcome in (Outcome.STARTED, Outcome.RESUMED, Outcome.BUSY):
            assert len(coders) == 1
        elif outcome is Outcome.HELD:
            assert rig.journal.holds(WS)
        else:
            assert outcome in (Outcome.NOTHING, Outcome.STUCK, Outcome.DEFERRED)
            # AU-3: an uncertain launch leaves no receipt, so its bead is STUCK at once while its session
            # may still be listed; it holds the role until the sweep or the agent ends it.
            unreceipted = [rig.runtime.listed[k].bead for k in rig.runtime.listed]
            assert all(reason(rig, b) == ("stuck", Reason.UNEXPECTED_STATE) for b in unreceipted)
            gated = {b for b in claimable_now(rig) if not isinstance(verdict(rig, b), Chosen)}
            assert [b for b in claimable_now(rig) if b not in gated] == [] or unreceipted
            assert all(isinstance(verdict(rig, b, resume=True), Deadline) for b in resumable_now(rig))
            waiters = quota_waiters(rig)
            assert all(isinstance(verdict(rig, b, resume=True), Deadline) for b in waiters)
            left = [b.id for b in rig.world.ready_for(WS)]
            stuck_left = [b for b in left if reason_of(rig, b) in (("stuck", Reason.UNCLAIMABLE),
                                                                    ("stuck", Reason.ACCOUNT_REPOINTED))]
            quota_left = [b for b in left if isinstance(verdict(rig, b), Deadline)]
            if outcome is Outcome.NOTHING:
                assert left == [] and waiters == [] and rig.sched.wake_at is None
            elif outcome is Outcome.DEFERRED:
                assert coders == []                        # a listed session is BUSY, never DEFERRED
                assert stuck_left == [] and sorted(quota_left) == sorted(left)
                assert rig.sched.wake_at is not None and rig.sched.wake_at > rig.clock()
                assert quota_left or waiters
            else:
                assert stuck_left and sorted(stuck_left + quota_left) == sorted(left)
            pending = any(("parked", b) in done and ("opened", b) not in done for b in plan)
            waking = any(plan[b] in ("quota_clears", "quota_race") and rig.world.beads[b].status != "closed"
                         for b in quota)
            if waking and rig.sched.wake_at is not None:
                rig.clock.now = rig.sched.wake_at          # the wake fires
            elif not rig.journal.ops_open() and not pending:
                break
        settle(rig, plan, done)
        for b, key in quota.items():
            if plan[b] == "quota_blocked":
                block(rig, key, 100 * 3600)                # it stays blocked, however the clock moves
    else:
        pytest.fail("pickup never settled")
    assert "btq-zz-blocker" not in rig.world.claims
    assert not any(plan[b] in ("account_repointed", "quota_blocked") for b in rig.world.claims)
    for n, kind in enumerate(kinds):
        expected = {"ok": "closed", "closed_blocker": "closed", "launch_uncertain": "closed",
                    "uncertain_landed": "closed", "uncertain_missed": "closed", "lost_race": "dropped",
                    "bad_repo": "stuck", "launch_fails": "stuck", "blocked": None, "parks": "closed",
                    "released": "closed", "deleted": "dropped", "pinned": "stuck", "quota_blocked": None,
                    "quota_clears": "closed", "quota_race": "closed", "account_repointed": "stuck"}[kind]
        # "launch_uncertain" and "deleted": the uncertain launch left no receipt, so its bead is STUCK at
        # once (AU-3 §4.3); it ends closed or dropped only if its agent closes it or bd loses it first.
        if kind in ("launch_uncertain", "deleted"):
            assert rig.state(f"btq-{n}") in ("stuck", expected), (n, kind)
        else:
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


def test_pickup_waits_at_the_door_while_a_park_runs(tmp_path: Path) -> None:
    """Finding 13: a park has stopped the session (the coder role looks free) but not yet labelled the
    bead. A pickup arriving now waits on the operation lock; it never claims in that window.

    Task 7 r2: the pickup's acquisition of the operation lock is probed, so the test proves the pickup
    asked for the lock and was blocked by the park, rather than having merely reached the door."""
    pause = PauseAt("park.stopped!")
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.add("btq-3")
    rig.restart(pause)
    lock = probe_lock(rig, "pickup")
    outcome: list[Outcome] = []
    parker = Worker(lambda: rig.parker.park("btq-1", ("btq-2",)), "park")
    picker = Worker(lambda: outcome.append(rig.pickup()), "pickup")
    try:
        parker.start()
        assert pause.reached.wait(10)
        picker.start()
        assert lock.asked.wait(10)          # the pickup asked for the lock, which the park holds
        assert lock.probes == ["blocked"]
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


def test_uncertain_resume_is_stuck_until_its_bead_closes(tmp_path: Path) -> None:
    """An uncertain resume leaves no hold and no open operation: the bead is STUCK at once (AU-3 §4.2
    step 5). Once its bead is closed, the sweep stops the listed session and the bead reads closed."""
    rig = parked_rig(tmp_path)
    rig.world.close("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.NOTHING        # the resume's launch is uncertain
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}
    assert rig.state("btq-1") == "stuck"
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.NOTHING
    assert rig.runtime.coders() == [] and rig.state("btq-1") == "closed"


def test_an_uncertain_resume_before_a_quota_skip_is_busy_not_deferred(tmp_path: Path) -> None:
    """AU-5 r1 finding 3: the uncertain resume leaves its session listed, so the role is taken. The quota
    skip after it still sets the wake, but pickup reports BUSY until the session is gone."""
    rig = parked_rig(tmp_path)
    key = own_profile(rig, "q-1")
    rig.world.add("btq-3")
    on_profile(rig, "btq-3", "q-1")
    until = block(rig, key, HOUR)
    rig.world.close("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.BUSY                    # btq-1's uncertain resume, then btq-3 skipped
    assert rig.state("btq-1") == "stuck" and rig.runtime.coders() == ["btq-1"]
    assert rig.sched.wake_at == until and "btq-3" not in rig.world.claims
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.DEFERRED                # the sweep ended it: now only the quota waits
    assert rig.runtime.coders() == [] and rig.sched.wake_at == until
    rig.clock.now = until
    assert rig.pickup() is Outcome.STARTED and rig.runtime.coders() == ["btq-3"]


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


def reason_of(rig: Rig, bead: str) -> tuple[str, Reason | None] | None:
    row = rig.journal.state(WS, bead)
    return None if row is None else (row.state.value, row.reason)


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


# --- discovery agrees with claiming ---


def pinned(rig: Rig, bead: str) -> None:
    """The bead is pinned to the workstream session, which only lists ready work: btq's ready() for that
    worker lists it, and every per-bead worker's claim refuses it."""
    rig.world.beads[bead].labels.append(f"session:{ids.ws_session(WS)}")


def test_pinned_bead_is_never_claimed_and_never_reads_as_idle(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    pinned(rig, "btq-1")
    assert [b.id for b in rig.beads.ready(WS)] == ["btq-1"]
    for _ in range(2):
        assert rig.pickup() is Outcome.STUCK
        assert reason(rig) == ("stuck", Reason.UNCLAIMABLE)
        assert rig.journal.snapshot(WS).state is WsState.STUCK
    assert rig.world.claims == [] and rig.journal.ops_open() == []
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-2"] and reason(rig) == ("stuck", Reason.UNCLAIMABLE)


def test_unpinned_bead_is_claimed_and_a_vanished_one_forgotten(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    pinned(rig, "btq-1")
    pinned(rig, "btq-2")
    assert rig.pickup() is Outcome.STUCK
    rig.world.beads["btq-1"].labels.pop()           # a human removed the pin
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and rig.state("btq-1") == "running"
    assert reason(rig, "btq-2") == ("dropped", Reason.CLAIM_ABANDONED)


def test_refused_claim_of_a_free_bead_is_recorded(tmp_path: Path) -> None:
    """btq refuses for a reason wsd can't see (here the per-bead worker's own pause flag): the bead stays
    free and ready, so it is recorded as unclaimable, never left to read as idle."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    (rig.beads.bead_queue(WS, "btq-1").state / "paused").touch()
    assert rig.pickup() is Outcome.STUCK
    assert reason(rig) == ("stuck", Reason.UNCLAIMABLE) and rig.journal.ops_open() == []
    assert rig.journal.snapshot(WS).state is WsState.STUCK
    (rig.beads.bead_queue(WS, "btq-1").state / "paused").unlink()
    assert rig.pickup() is Outcome.STARTED and rig.world.claims == ["btq-1"]


def test_refusal_that_reads_back_as_ours_is_started(tmp_path: Path) -> None:
    """A refusal is read back like any claim outcome, and beads are the truth: a claim that reads back as
    ours (not something btq does, injected here) is started, never left claimed and idle."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", ValueError("Task is not eligible for this worker"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "running" and rig.world.claims == ["btq-1"]


def test_uncertain_claim_of_a_bead_bd_says_is_gone_moves_on(tmp_path: Path) -> None:
    """The claim's outcome is unknown and the read-back finds no such bead (bd says so twice): the pickup
    ends, the bead is dropped, and the next candidate is tried in the same pickup."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), bead="btq-1")
    rig.world.fault("show", RuntimeError("Error: no issue found matching 'btq-1'"), times=2, bead="btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST) and rig.state("btq-2") == "running"


def test_unclaimed_bead_still_labelled_parked_is_not_new_work(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", labels=[PARKED])
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("stuck", Reason.UNCLAIMABLE) and rig.world.claims == ["btq-2"]


# --- a claim given back to the queue ---


def released(rig: Rig, bead: str = "btq-1") -> None:
    """Someone with the right (the operator, Bel's recovery) handed the claim back to the queue."""
    rig.world.beads[bead].status, rig.world.beads[bead].assignee = "open", None


def test_released_running_bead_is_claimed_again(tmp_path: Path) -> None:
    """Codex r1 finding 2: the session ended and the claim went back to the queue. The sweep records the
    lost claim; the bead is then ready, so it is claimed afresh rather than wedging every pickup."""
    rig = started(tmp_path)
    rig.world.add("btq-2")
    rig.runtime.end(rig.key("btq-1"))
    released(rig)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-1"] and rig.state("btq-1") == "running"
    assert len(rig.runtime.launches) == 2 and rig.world.worktrees == ["btq-1"]     # the worktree reused


def test_released_lost_claim_never_stops_the_next_candidate(tmp_path: Path) -> None:
    """A STUCK row left from a lost claim, on a bead that is ready again but can't start: the next ready
    bead is still tried."""
    rig = started(tmp_path)
    rig.world.add("btq-2")
    rig.runtime.end(rig.key("btq-1"))
    released(rig)
    rig.runtime.failing_beads.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-1", "btq-2"] and rig.state("btq-2") == "running"


@pytest.mark.parametrize("labels", [True, False])
def test_released_held_bead(tmp_path: Path, labels: bool) -> None:
    """A bead the operator stopped (HELD) is given back to the queue. Still labelled as parked by wsd it
    is no new work and the next bead starts; once the labels are gone it is claimed like any other."""
    rig = started(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    released(rig)
    rig.world.add("btq-2")
    if not labels:
        rig.world.beads["btq-1"].labels[:] = [x for x in rig.world.beads["btq-1"].labels
                                              if x not in (PARKED, HELD)]
    assert rig.pickup() is Outcome.STARTED
    if labels:
        assert reason(rig) == ("stuck", Reason.UNCLAIMABLE) and rig.state("btq-2") == "running"
    else:
        assert rig.state("btq-1") == "running" and rig.world.claims == ["btq-1", "btq-1"]


# --- a bead deleted under an open operation ---


@pytest.mark.parametrize("point", [p for p in POINTS if p != "lock.waiting"])
def test_bead_deleted_after_a_crash_is_dropped(tmp_path: Path, point: str) -> None:
    """Codex r1 finding 3: bd confirms the bead is gone, so whatever the crash left open ends, its session
    (if one started) is stopped, and the next bead starts."""
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    del rig.world.beads["btq-1"]
    rig.world.add("btq-2")
    rig.restart()
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST)
    assert rig.runtime.coders() == ["btq-2"] and rig.journal.holds(WS) == {}
    assert [op.bead for op in rig.journal.ops_open()] == []


def test_deleted_bead_keeps_its_session_stop_confirmation(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, cp=CrashAt("pickup.launched!"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    del rig.world.beads["btq-1"]
    rig.world.add("btq-2")
    rig.restart()
    rig.runtime.stop_failures = 1
    assert rig.pickup() is Outcome.HELD
    assert rig.runtime.coders() == ["btq-1"] and len(rig.journal.ops_open()) == 1
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST) and rig.runtime.coders() == ["btq-2"]


def test_deleted_bead_with_an_unreadable_claim_ends_its_hold(tmp_path: Path) -> None:
    """The claim could not be read back (held as CLAIM_UNCERTAIN); then the bead was deleted."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), after=True, bead="btq-1")
    rig.world.fault("show", RuntimeError("dolt down"), times=2, bead="btq-1")
    assert rig.pickup() is Outcome.HELD and Reason.CLAIM_UNCERTAIN in rig.journal.holds(WS)
    del rig.world.beads["btq-1"]
    assert rig.pickup() is Outcome.STARTED
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST) and rig.journal.holds(WS) == {}


def test_deleted_bead_keeps_another_beads_claim_hold(tmp_path: Path) -> None:
    """Task 7 r2 (R18): an older pickup of btq-2 can't read its claim back (CLAIM_UNCERTAIN, btq-2). An
    operator release of btq-1 crashes and btq-1 is deleted. The replays re-establish btq-2's hold, then
    end btq-1's release; ending it must keep btq-2's hold, so nothing new is claimed."""
    rig = started(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), bead="btq-2")
    rig.world.fault("show", RuntimeError("dolt down"), times=2, bead="btq-2")
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.CLAIM_UNCERTAIN: "btq-2"}
    rig.restart(CrashAt("release.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    del rig.world.beads["btq-1"]
    rig.world.add("btq-3")
    rig.world.fault("show", RuntimeError("dolt down"), times=2, bead="btq-2")
    rig.restart()
    assert [op.bead for op in rig.journal.ops_open()] == ["btq-2", "btq-1"]     # the pickup is older
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.CLAIM_UNCERTAIN: "btq-2"}
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST)
    assert rig.world.claims == ["btq-1"] and rig.journal.state(WS, "btq-3") is None
    assert [op.bead for op in rig.journal.ops_open()] == ["btq-2"]


def test_deleted_bead_ends_an_interrupted_park(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.add("btq-2")
    rig.world.add("btq-3")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.restart(CrashAt("park.committed!"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    del rig.world.beads["btq-1"]
    rig.restart()
    assert rig.pickup() is Outcome.STARTED          # the park's replay reads no claim of ours: it ends
    assert rig.runtime.coders() == ["btq-3"] and rig.journal.ops_open() == []
    assert rig.pickup() is Outcome.BUSY             # and the sweep drops the row of a bead that is gone
    assert reason(rig) == ("dropped", Reason.CLAIM_LOST)


def test_unreachable_queue_is_never_read_as_a_deleted_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, cp=CrashAt("pickup.placed"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.world.fault("worktree", RuntimeError("dolt down"))
    assert rig.pickup() is Outcome.HELD             # the bead still exists: the failure is the queue's
    assert Reason.BEADS_UNREACHABLE in rig.journal.holds(WS) and len(rig.journal.ops_open()) == 1
    assert rig.pickup() is Outcome.BUSY and rig.state("btq-1") == "running"


# --- the placement is the journal's before its worktree exists ---


@pytest.mark.parametrize("point", ["pickup.placed", "pickup.worktree!"])
def test_replay_after_a_repository_change_uses_the_recorded_placement(tmp_path: Path, point: str) -> None:
    """Codex r1 finding 4: the default repository changed across the crash. The replay makes (or reuses)
    the worktree the pickup placed, never a second one in the new repository."""
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    other = git_repo(tmp_path / "repos" / "other")
    rig.ws = replace(rig.ws, repos={"default": other})
    rig.restart()
    assert rig.pickup() is Outcome.BUSY             # the replay started it
    [spec] = rig.runtime.launches
    assert spec.worktree == rig.worktree("btq-1") and rig.world.worktrees == ["btq-1"]
    assert not (other.parent / "other-btq-btq-1").exists()
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.repo == str(rig.repo.resolve())


# --- the pause flag can't be read ---


def test_unreadable_pause_flag_is_paused_not_unreachable(tmp_path: Path) -> None:
    """Codex r1 finding 5: the workstream worker's state directory can't be read. paused() fails closed
    (paused) while ready() would raise: pickup claims nothing and reports the workstream PAUSED."""
    if os.geteuid() == 0:
        pytest.skip("root reads any directory")
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    state = rig.beads.ws_queue(WS).state
    state.chmod(0)
    try:
        assert rig.pickup() is Outcome.NOTHING
        assert rig.journal.snapshot(WS).state is WsState.PAUSED
        assert rig.journal.holds(WS) == {} and rig.world.claims == []
    finally:
        state.chmod(0o700)


# --- AU-5: the headroom gate in pickup (design §3.6; §5 pickup) ---

HOUR = 3600


def repoint_codex(rig: Rig, to: str) -> None:
    """Repoint the default codex login (`~/.codex/auth.json`) to another file, or back (`to` = "")."""
    accounts = rig.ws.accounts
    assert isinstance(accounts, ConfiguredAccounts)
    link = Path(accounts.env["HOME"]) / ".codex" / "auth.json"
    link.unlink()
    if to:
        other = link.parent.parent / f".codex-{to}" / "auth.json"
        other.parent.mkdir(exist_ok=True)
        other.write_text("{}")
        link.symlink_to(other)
    else:
        link.write_text("{}")


def handed_back(rig: Rig, bead: str) -> None:
    """The bead's session ended and its claim went back to the queue: ready again, launched before."""
    rig.runtime.end(rig.key(bead))
    released(rig, bead)


def test_an_eligible_later_candidate_starts_while_an_earlier_one_is_blocked(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    key = own_profile(rig, "q-a")
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", "q-a")
    rig.world.add("btq-2")
    block(rig, key, HOUR)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-2"] and rig.state("btq-1") is None
    assert rig.sched.wake_at is None                       # the coder role is busy: no wake time


def test_only_blocked_candidates_left_is_deferred_with_the_deadline(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    until = block(rig, profile_key(rig, "p-one"), HOUR)
    assert rig.pickup() is Outcome.DEFERRED
    assert rig.world.claims == [] and rig.sched.wake_at == until
    assert rig.journal.snapshot(WS).state is WsState.DEFERRED
    rig.clock.advance(HOUR)
    assert rig.pickup(TriggerKind.QUOTA_WAKE) is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and rig.sched.wake_at is None


def test_a_resumable_bead_is_gated_on_its_recorded_profile(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED                 # on p-one
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    rig.ws = replace(rig.ws, coder_profile="p-two")        # the configured default moved on
    rig.restart()
    until = block(rig, profile_key(rig, "p-one"), HOUR)
    block(rig, profile_key(rig, "p-two"), 2 * HOUR)
    assert rig.pickup() is Outcome.DEFERRED and rig.sched.wake_at == until
    assert len(rig.runtime.launches) == 1
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED                 # p-one clears; p-two still blocks
    assert rig.runtime.launches[-1].profile == "p-one"


def deferral(rig: Rig, bead: str, until: int | None, reason: str = "quota", role: str = "coder") -> None:
    rig.journal.db.execute("INSERT INTO deferrals VALUES (?, 1, ?, ?, 'p-one', ?, ?, 'trusted')",
                           (f"sk-{bead}", bead, role, reason, None if until is None else str(until)))


def bystander(rig: Rig, bead: str, state: BeadState = BeadState.DROPPED) -> None:
    """A bead of the journal's that pickup never considers: not wsd's, so never ready."""
    rig.world.add(bead)
    rig.world.beads[bead].labels.remove("agent:wsd")
    rig.journal.adopt(WS, bead, state)


@pytest.mark.parametrize("excluded", ["none", "past", "held_row", "stuck_row", "held_label", "needs_human",
                                      "blocked", "account_changed", "reviewer"])
def test_the_wake_time_counts_current_coder_quota_deferrals(tmp_path: Path, excluded: str) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    until = block(rig, profile_key(rig, "p-one"), HOUR)
    now = rig.clock()
    state = {"held_row": BeadState.HELD, "stuck_row": BeadState.STUCK}.get(excluded, BeadState.DROPPED)
    bystander(rig, "btq-5", state)
    if excluded == "held_label":
        rig.world.beads["btq-5"].labels.append(HELD)
    elif excluded == "needs_human":
        rig.world.beads["btq-5"].labels.append(NEEDS_HUMAN)
    elif excluded == "blocked":
        rig.world.add("btq-6")
        rig.world.beads["btq-5"].deps.append(("btq-6", "blocks"))
        rig.world.beads["btq-6"].labels.remove("agent:wsd")
    deferral(rig, "btq-5", now - 1 if excluded == "past" else now + 600,
             "account_changed" if excluded == "account_changed" else "quota",
             "reviewer" if excluded == "reviewer" else "coder")
    assert rig.pickup() is Outcome.DEFERRED
    wake = rig.sched.wake_at
    assert wake is not None and wake > now and wake == (now + 600 if excluded == "none" else until)


def test_an_account_changed_deferral_alone_never_arms_a_wake(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    bystander(rig, "btq-5")
    deferral(rig, "btq-5", None, "account_changed")
    assert rig.pickup() is Outcome.NOTHING and rig.sched.wake_at is None
    assert rig.journal.snapshot(WS).state is WsState.IDLE


def repointed(tmp_path: Path) -> Rig:
    """btq-1 launched on p-two (codex default, failover "none"), handed back, and its login repointed."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", "p-two")
    assert rig.pickup() is Outcome.STARTED
    handed_back(rig, "btq-1")
    repoint_codex(rig, "other")
    return rig


def test_a_ready_bead_whose_account_changed_is_never_claimed(tmp_path: Path) -> None:
    rig = repointed(tmp_path)
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-2"]          # btq-1 only its first time
    assert reason(rig) == ("stuck", Reason.ACCOUNT_REPOINTED)
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.STUCK and rig.sched.wake_at is None
    assert rig.world.claims == ["btq-1", "btq-2"]
    assert rig.journal.snapshot(WS).state is WsState.STUCK
    repoint_codex(rig, "")                                 # restored
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-2", "btq-1"] and rig.state("btq-1") == "running"


def test_account_changed_then_deadline_then_chosen(tmp_path: Path) -> None:
    rig = repointed(tmp_path)
    bystander(rig, "btq-9", BeadState.STUCK)
    rig.journal.adopt(WS, "btq-9", BeadState.STUCK, Reason.ACCOUNT_CHANGED, "a claimed bead's escalation")
    assert rig.pickup() is Outcome.STUCK
    assert reason(rig) == ("stuck", Reason.ACCOUNT_REPOINTED)
    repoint_codex(rig, "")
    until = block(rig, profile_key(rig, "p-two"), HOUR)
    assert rig.pickup() is Outcome.DEFERRED and rig.sched.wake_at == until
    assert reason(rig) == ("dropped", Reason.CLAIM_ABANDONED)
    assert rig.world.claims == ["btq-1"]
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1", "btq-1"] and rig.state("btq-1") == "running"
    assert reason(rig, "btq-9") == ("stuck", Reason.ACCOUNT_CHANGED)


def test_an_unlisted_account_repointed_row_is_dropped(tmp_path: Path) -> None:
    rig = repointed(tmp_path)
    assert rig.pickup() is Outcome.STUCK
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.NOTHING
    assert reason(rig) == ("dropped", Reason.CLAIM_ABANDONED) and rig.world.claims == ["btq-1"]


def raced(tmp_path: Path) -> tuple[Rig, int]:
    """btq-1 admitted by pickup's gate, then blocked (a trusted mark) before the guard's step 2."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    until = rig.clock() + HOUR
    rig.restart(At("gate.checked", lambda: block(rig, profile_key(rig, "p-one"), HOUR)))
    return rig, until


def test_the_guard_race_is_deferred_with_a_wake(tmp_path: Path) -> None:
    rig, until = raced(tmp_path)
    assert rig.pickup() is Outcome.DEFERRED and rig.sched.wake_at == until
    assert reason(rig) == ("parked", Reason.QUOTA) and rig.runtime.launches == []
    assert PARKED in rig.world.beads["btq-1"].labels and rig.journal.ops_open() == []
    rig.clock.advance(HOUR)
    assert rig.pickup(TriggerKind.QUOTA_WAKE) is Outcome.RESUMED
    assert len(rig.runtime.launches) == 1 and rig.state("btq-1") == "running"


@pytest.mark.parametrize("point", ["pickup.quota", "pickup.quota!"])
def test_a_crash_in_the_quota_shelve_replays_to_parked_quota(tmp_path: Path, point: str) -> None:
    rig, until = raced(tmp_path)
    assert isinstance(rig.cp, At)
    block_now = rig.cp.fn
    rig.restart(Both(At("gate.checked", block_now), CrashAt(point)))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    assert rig.pickup() is Outcome.DEFERRED and rig.sched.wake_at == until
    assert reason(rig) == ("parked", Reason.QUOTA) and rig.runtime.launches == []
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.detail == quota_detail(until)


@pytest.mark.parametrize("point", ["resume.quota", "resume.quota!", None])
def test_the_resume_race_is_deferred_and_replays(tmp_path: Path, point: str | None) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    until = rig.clock() + HOUR
    blocker = At("resume.intent", lambda: block(rig, profile_key(rig, "p-one"), HOUR))
    if point is None:
        rig.restart(blocker)
        assert rig.pickup() is Outcome.DEFERRED
    else:
        rig.restart(Both(blocker, CrashAt(point)))
        with pytest.raises(SimulatedCrash):
            rig.pickup()
        rig.restart()
        assert rig.pickup() is Outcome.DEFERRED
    assert rig.sched.wake_at == until and reason(rig) == ("parked", Reason.QUOTA)
    assert len(rig.runtime.launches) == 1
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED and len(rig.runtime.launches) == 2


@pytest.mark.parametrize("added", ["blocker", "held", "needs_human"])
def test_a_quota_waiter_that_gains_a_stop_before_the_replay_ends_in_it(tmp_path: Path, added: str) -> None:
    rig, _ = raced(tmp_path)
    assert isinstance(rig.cp, At)
    rig.restart(Both(At("gate.checked", rig.cp.fn), CrashAt("pickup.quota")))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    if added == "blocker":
        rig.world.add("btq-2")
        rig.world.beads["btq-2"].labels.remove("agent:wsd")
        rig.world.beads["btq-1"].deps.append(("btq-2", "blocks"))
    else:
        rig.world.beads["btq-1"].labels.append(HELD if added == "held" else NEEDS_HUMAN)
    rig.restart()
    outcome = rig.pickup()
    expected = {"blocker": ("parked", Reason.BLOCKED_ON_BEAD), "held": ("held", Reason.HELD_BY_OPERATOR),
                "needs_human": ("stuck", Reason.NEEDS_HUMAN)}[added]
    assert reason(rig) == expected and rig.sched.wake_at is None
    assert outcome is not Outcome.DEFERRED


def test_a_runnable_waiter_counts_at_its_deadline_or_the_recheck(tmp_path: Path) -> None:
    rig, until = raced(tmp_path)
    assert rig.pickup() is Outcome.DEFERRED
    now = rig.clock()
    assert rig.sched._waiters(now) == [until]  # pyright: ignore[reportPrivateUsage]
    rig.journal.db.execute("DELETE FROM account_exhausted")
    assert rig.sched._waiters(now) == [now + rig.ws.usage.min_recheck_seconds]  # pyright: ignore[reportPrivateUsage]


class Both(Recorder):
    """Two checkpoint doubles at once, in order."""

    def __init__(self, *parts: Recorder) -> None:
        super().__init__()
        self.parts = parts

    def __call__(self, name: str) -> None:
        super().__call__(name)
        for part in self.parts:
            part(name)
