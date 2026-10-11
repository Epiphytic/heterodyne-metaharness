import contextlib
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, replace

import pytest
from sandbox_env import RuntimeRig, fresh_logins, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import WS, Rig, make_rig, on_profile

from heterodyne.sandbox.runtime import WIP_FAILED, Phase, SessionRecord, read_record
from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import NEEDS_HUMAN
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import Liveness, RuntimeUnavailable
from heterodyne.wsd.scheduler import Outcome, Scheduler
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
    assert rt.backend.reapers == [(first.sandbox, first.deadline), (second.sandbox, second.deadline)]


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


@pytest.mark.parametrize("both", [Limits(launch_failures_before_human=1)], indirect=True)
def test_an_unlanded_lifetime_stop_makes_no_wip_and_needs_a_human(both: Both) -> None:
    """D26: commits that could not be landed come before any host WIP, and the relaunch refuses."""
    rig, rt = both.rig, both.rt
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    first = stop_window(both, "btq-1")
    shutil.rmtree(rt.runtime.layout(first.key).git)       # the seeded private git directory is lost
    rig.pickup()
    rec = both.record("btq-1")
    assert (rec.phase, rec.generation, rec.unlanded) == (Phase.ENDED, 1, True)
    assert rec.wip_mark == f"lifetime:{rig.key('btq-1')}:1"     # still owed, never committed over it
    assert lifetime_marks(rig, "btq-1") == [] and rt.backend.created == [first.sandbox]
    assert "not landed" in refusal(rig, "btq-1", 2)
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rt.runtime.sessions(WS) == []
