import contextlib
import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace

import pytest
from sandbox_env import RuntimeRig, fresh_logins, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import WS, Rig, make_rig, on_profile

from heterodyne.sandbox.runtime import WIP_FAILED, Phase, SandboxRuntime, SessionRecord, read_record
from heterodyne.sandbox.spec import short_id
from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import NEEDS_HUMAN
from heterodyne.wsd.park import Parker
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.runtime import Liveness, RuntimeUnavailable
from heterodyne.wsd.scheduler import Outcome, Scheduler
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.workstream import Limits

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                reason="tmux and setsid are needed")


@dataclass
class Both:
    rig: Rig            # plan 3's scheduler, journal and fake btq
    rt: RuntimeRig      # the sandbox runtime it drives

    def record(self, bead: str) -> SessionRecord:
        rec = read_record(self.rt.runtime.layout(self.rig.key(bead)))
        assert rec is not None
        return rec


@pytest.fixture
def both(request: pytest.FixtureRequest) -> Iterator[Both]:
    limits: Limits = getattr(request, "param", Limits())
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = make_rig(root / "w", limits=limits)
        try:            # the Journal closes whatever fails below: an open one breaks test_wsd_gate's fd count
            rt = runtime_rig(root / "x", tmux, rig.clock)
            rig.deps = replace(rig.deps, runtime=rt.runtime)
            rig.parker = Parker(rig.ws, rig.deps)
            rig.sched = Scheduler(rig.ws, rig.deps, rig.parker)
            try:
                yield Both(rig, rt)
            finally:
                for key in list(rt.runtime.servers):
                    with contextlib.suppress(RuntimeUnavailable):
                        rt.runtime.stop(key)
                tmux.kill_server()
                assert tmux.socket_path is not None
                tmux.socket_path.unlink(missing_ok=True)
        finally:
            rig.journal.close()


def refusal(rig: Rig, bead: str, generation: int) -> str:
    """The guard's journaled receipt for a refused launch: its error."""
    receipt = rig.journal.receipt(rig.key(bead), generation)
    assert receipt is not None and (receipt.kind, receipt.refusal) == ("refused", "failed")
    assert receipt.error is not None
    return receipt.error


def lifetime_marks(rig: Rig, bead: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(rig.worktree(bead)), "log", "--format=%B"], capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("wsd-park: lifetime:")]


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_pickup_runs_the_bead_in_a_sandbox(both: Both, profile: str) -> None:
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", profile)
    assert rig.pickup() is Outcome.STARTED
    rec = both.record("btq-1")
    assert (rec.phase, rec.generation, rec.worktree) == (Phase.RUNNING, 1, str(rig.worktree("btq-1")))
    assert rec.native_id is not None
    if profile == "p-one":
        assert rec.native_id == rig.key("btq-1")         # Claude: the session key
    [session] = rt.runtime.sessions(WS)
    assert session.liveness is Liveness.LIVE and session.bead == "btq-1"
    started = rig.journal.receipt(rig.key("btq-1"), 1)
    assert started is not None and started.native_id == rec.native_id    # the Codex thread ID included
    assert rig.state("btq-1") == "running"
    assert rig.pickup() is Outcome.BUSY
    assert len(rt.backend.created) == 1


@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_the_lifetime_stop_relaunches_the_same_session(both: Both, profile: str) -> None:
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    on_profile(rig, "btq-1", profile)
    assert rig.pickup() is Outcome.STARTED
    first = both.record("btq-1")
    rig.clock.advance(first.deadline - rt.runtime.c.settings.stop_margin_seconds - rig.clock())
    assert rig.pickup() is Outcome.BUSY             # stopped, resumed and relaunched in one pickup
    second = both.record("btq-1")
    assert (second.phase, second.generation, second.native_id) == (Phase.RUNNING, 2, first.native_id)
    assert second.sandbox != first.sandbox and first.sandbox in rt.backend.deleted
    assert lifetime_marks(rig, "btq-1") == [f"wsd-park: lifetime:{rig.key('btq-1')}:1"]
    assert rig.state("btq-1") == "running"
    assert len(rt.runtime.sessions(WS)) == 1
    # T11: each generation has its own watcher; the first's create returned, so its end removed it.
    assert (first.created, second.created) == (True, True)
    assert not rt.tmux.has_session(rt.runtime.reaper_name(first.key, 1))
    assert rt.tmux.has_session(rt.runtime.reaper_name(second.key, 2))
    path = rt.runtime.layout(first.key).record
    assert rt.backend.reapers == [(first.sandbox, first.deadline, path),
                                  (second.sandbox, second.deadline, path)]


@pytest.mark.parametrize("both", [Limits(launch_failures_before_human=1)], indirect=True)
def test_a_relaunch_the_gate_refuses_needs_a_human(both: Both) -> None:
    rig, rt = both.rig, both.rt
    s = rt.runtime.c.settings
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = both.record("btq-1")
    # Valid for this generation, but not for another full lifetime from the stop window.
    fresh_logins(rt.home, first.started_at + s.max_lifetime_seconds + s.stop_margin_seconds + 600)
    rig.clock.advance(first.deadline - s.stop_margin_seconds - rig.clock())
    rig.pickup()
    rec = both.record("btq-1")
    assert rec.phase is Phase.ENDED and rec.generation == 1     # generation 2 never got a record write
    assert rt.backend.created == [first.sandbox]
    assert "expire" in refusal(rig, "btq-1", 2)
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rt.runtime.sessions(WS) == []


def stop_window(both: Both, bead: str) -> SessionRecord:
    """Move the shared clock to the start of the bead's stop window. Returns its record from before."""
    rec = both.record(bead)
    both.rig.clock.advance(rec.deadline - both.rt.runtime.c.settings.stop_margin_seconds - both.rig.clock())
    return rec


def test_a_lifetime_wip_that_fails_once_lands_before_the_relaunch(both: Both,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """T10 r1: a failed lifetime WIP keeps its mark, and the relaunch in the same pickup retries it first."""
    rig = both.rig
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = stop_window(both, "btq-1")
    real, calls = gitwip.wip_commit, []

    def fails_once(pinned: gitwip.Pinned, mark: str, summary: str) -> None:
        calls.append(mark)
        if len(calls) == 1:
            raise gitwip.GitFailed("git commit exited 1")
        real(pinned, mark, summary)

    monkeypatch.setattr(gitwip, "wip_commit", fails_once)
    assert rig.pickup() is Outcome.BUSY
    second = both.record("btq-1")
    assert (second.phase, second.generation, second.native_id) == (Phase.RUNNING, 2, first.native_id)
    assert len(calls) == 2 and second.wip_mark == "" and second.error == ""
    assert lifetime_marks(rig, "btq-1") == [f"wsd-park: lifetime:{rig.key('btq-1')}:1"]


@pytest.mark.parametrize("both", [Limits(launch_failures_before_human=1)], indirect=True)
def test_a_lifetime_wip_that_keeps_failing_holds_the_relaunch(both: Both,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """The owed WIP is never dropped: generation 2 waits for it, and the launch budget brings a human."""
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = stop_window(both, "btq-1")

    def failed(*_: object) -> None:
        raise gitwip.GitFailed("git commit exited 1")

    monkeypatch.setattr(gitwip, "wip_commit", failed)
    rig.pickup()
    rec = both.record("btq-1")
    assert (rec.phase, rec.generation) == (Phase.ENDED, 1)
    assert (rec.error, rec.wip_mark) == (WIP_FAILED, f"lifetime:{rig.key('btq-1')}:1")
    assert rt.backend.created == [first.sandbox] and lifetime_marks(rig, "btq-1") == []
    assert "still owed" in refusal(rig, "btq-1", 2)
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rt.runtime.sessions(WS) == []


def agent_commit(both: Both, bead: str) -> str:
    """A commit the agent makes inside: in the session's private git directory, never the host's."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_DIR": str(both.rt.runtime.layout(both.rig.key(bead)).git),
            "GIT_WORK_TREE": str(both.rig.worktree(bead))}
    git = ["git", "-c", "user.name=agent", "-c", "user.email=agent@example.org"]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "agent work"], env=env, check=True,
                   capture_output=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def host_log(both: Both, bead: str) -> str:
    return subprocess.run(["git", "-C", str(both.rig.worktree(bead)), "log", "--format=%H %s", f"btq/{bead}"],
                          capture_output=True, text=True, check=True).stdout


def private_tip(both: Both, bead: str) -> str:
    git = both.rt.runtime.layout(both.rig.key(bead)).git
    return subprocess.run(["git", "--git-dir", str(git), "rev-parse", f"btq/{bead}"], capture_output=True,
                          text=True, check=True).stdout.strip()


def unlandable(both: Both, bead: str) -> str:
    """The agent commits, then its private git directory can no longer be landed. Returns the commit."""
    made = agent_commit(both, bead)
    (both.rt.runtime.layout(both.rig.key(bead)).git / "objects" / "zz").symlink_to(both.rt.root)
    return made


def restart(both: Both) -> None:
    """A new runtime, parker and scheduler over the same journal, session directory and backend."""
    rig, rt = both.rig, both.rt
    rt.runtime = SandboxRuntime(rt.runtime.c, rt.backend, rt.selftest)
    rig.deps = replace(rig.deps, runtime=rt.runtime)
    rig.parker = Parker(rig.ws, rig.deps)
    rig.sched = Scheduler(rig.ws, rig.deps, rig.parker)


def assert_held_unlanded(both: Both, bead: str, host_before: str, made: str) -> None:
    rig = both.rig
    detail = rig.journal.holds(WS).get(Reason.RUNTIME_UNAVAILABLE, "")
    sid = short_id(rig.key(bead))
    assert f"session {sid} generation 1" in detail and f" s/{sid}/git " in detail     # the rig's is s/
    assert host_log(both, bead) == host_before and lifetime_marks(rig, bead) == []   # no host WIP
    assert private_tip(both, bead) == made                                        # kept for a human
    assert rig.journal.receipt(rig.key(bead), 2) is None                          # no generation 2
    assert NEEDS_HUMAN not in rig.world.beads[bead].labels                        # no budget spent


@pytest.mark.parametrize("both", [Limits(launch_failures_before_human=1)], indirect=True)
def test_an_unlanded_lifetime_stop_makes_no_wip_and_holds_the_workstream(both: Both) -> None:
    """D26, T12 (B): commits that could not be landed hold the workstream before any host WIP or relaunch;
    it stays held through a restart, with the session and its git directory named for the operator."""
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = stop_window(both, "btq-1")
    before = host_log(both, "btq-1")
    made = unlandable(both, "btq-1")
    assert rig.pickup() is Outcome.HELD
    rec = both.record("btq-1")
    assert (rec.phase, rec.generation, rec.unlanded) == (Phase.ENDED, 1, True)
    assert rec.wip_mark == f"lifetime:{rig.key('btq-1')}:1"     # still owed, never committed over it
    assert rt.backend.created == [first.sandbox]
    assert_held_unlanded(both, "btq-1", before, made)
    restart(both)
    assert recover(rig.sched).ok is False and rig.pickup() is Outcome.HELD
    assert_held_unlanded(both, "btq-1", before, made)
    assert rt.backend.created == [first.sandbox]


@pytest.mark.parametrize("how", ["park", "defer"])
def test_a_park_or_defer_over_unlanded_commits_makes_no_wip(both: Both, how: str) -> None:
    """T10 r3: the stop ends the session but its commits can't land, so the park or defer holds at its
    stop step: no host WIP, the host branch unchanged, the private commits kept, through a restart."""
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.STARTED
    before = host_log(both, "btq-1")
    made = unlandable(both, "btq-1")
    if how == "park":
        state = rig.parker.park("btq-1", ("btq-2",))
    else:
        state = rig.parker.defer("btq-1", "account_changed", None)
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (state, row.state, row.reason) == (BeadState.PARKING, BeadState.PARKING,
                                                                   Reason.STOP_UNCONFIRMED)
    rec = both.record("btq-1")
    assert (rec.phase, rec.unlanded) == (Phase.ENDED, True) and rt.backend.boxes == {}
    assert_held_unlanded(both, "btq-1", before, made)
    [op] = rig.journal.ops_open(WS)
    restart(both)
    assert recover(rig.sched).ok is False and rig.pickup() is Outcome.HELD
    assert [o.op_id for o in rig.journal.ops_open(WS)] == [op.op_id]       # still open at its stop step
    assert_held_unlanded(both, "btq-1", before, made)
