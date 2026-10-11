import contextlib
import json
import shutil
import subprocess
import types
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from sandbox_env import RuntimeRig, launch_spec, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import WS, Clock, make_rig

from heterodyne.sandbox import reaper
from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.sandbox.runtime import WIP_FAILED, Phase, SessionRecord, read_record
from heterodyne.sandbox.spec import sandbox_name
from heterodyne.wsd import gitwip
from heterodyne.wsd.runtime import LaunchFailed, NoRuntime, RuntimeUnavailable
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import Reason

needs_tools = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                 reason="tmux and setsid are needed")


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            rig.backend.delete_unconfirmed = 0
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def wip_marks(rig: RuntimeRig) -> list[str]:
    out = subprocess.run(["git", "-C", str(rig.worktree), "log", "--format=%B"], capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("wsd-park: lifetime:")]


class Crash(BaseException):
    """wsd dies here: nothing after this point runs, and no handler catches it."""


@needs_tools
def test_nothing_stops_before_the_window(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.runtime.expire("alpha", rec.deadline - rig.runtime.c.settings.stop_margin_seconds - 1)
    assert record(rig, spec.session_key).phase is Phase.RUNNING and wip_marks(rig) == []


@needs_tools
def test_an_idle_session_stops_softly_once_the_window_opens(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-two")
    rig.runtime.launch(spec)                    # the self-test's turn ended with a Stop: idle
    rec = record(rig, spec.session_key)
    rig.runtime.expire("beta", rec.deadline)
    assert record(rig, spec.session_key).phase is Phase.RUNNING          # another workstream's call
    rig.runtime.expire("alpha", rec.deadline - rig.runtime.c.settings.stop_margin_seconds)
    after = record(rig, spec.session_key)
    assert after.phase is Phase.ENDED and after.error == "" and rig.backend.boxes == {}
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]
    assert rig.runtime.sessions("alpha") == []


@needs_tools
def test_a_session_with_no_turn_boundary_stops_hard_at_the_deadline(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rig.runtime.servers.pop(spec.session_key).close()       # a wsd restart: the turn state is gone
    assert len(rig.runtime.sessions("alpha")) == 1           # re-bound, not idle
    rec = record(rig, spec.session_key)
    rig.runtime.expire("alpha", rec.deadline - 1)
    assert record(rig, spec.session_key).phase is Phase.RUNNING
    rig.runtime.expire("alpha", rec.deadline)
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert not rig.tmux.has_session(rec.tmux_session)


@needs_tools
@pytest.mark.parametrize("retry", ["expire", "sessions"])
def test_an_unconfirmed_lifetime_stop_keeps_its_wip_owed(rig: RuntimeRig, retry: str) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    deadline = record(rig, spec.session_key).deadline
    rig.backend.delete_unconfirmed = 1
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.expire("alpha", deadline)
    owed = record(rig, spec.session_key)
    assert (owed.phase, owed.stop_reason, owed.wip_mark) == (
        Phase.STOPPING, "lifetime", f"lifetime:{spec.session_key}:1")
    assert wip_marks(rig) == []
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))      # the backstop stays
    if retry == "expire":
        rig.runtime.expire("alpha", deadline)
    else:
        assert rig.runtime.sessions("alpha") == []   # ordinary cleanup, with wsd's clock before the deadline
    assert record(rig, spec.session_key).phase is Phase.ENDED
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]


@needs_tools
def test_a_sandbox_the_reaper_deleted_ends_with_its_lifetime_wip(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    rig.clock.advance(rec.deadline - rig.clock.now)
    del rig.backend.boxes[rec.sandbox]                  # the backstop acted while wsd's pickups did not
    assert rig.runtime.sessions("alpha") == []
    after = record(rig, spec.session_key)
    assert (after.phase, after.stop_reason, after.wip_mark) == (Phase.ENDED, "lifetime", "")
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))


@needs_tools
@pytest.mark.parametrize("point", ["stopping", "ended", "committed"])
@pytest.mark.parametrize("replay", ["expire", "launch"])
def test_a_crash_around_the_lifetime_stop_replays_the_wip_exactly_once(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, point: str, replay: str) -> None:
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    deadline = record(rig, spec.session_key).deadline
    rig.clock.advance(deadline - rig.clock.now)
    real = gitwip.wip_commit

    def crash(*_: object) -> None:
        raise Crash

    def commit_then_crash(pinned: gitwip.Pinned, mark: str, summary: str) -> None:
        real(pinned, mark, summary)
        raise Crash

    with monkeypatch.context() as m:
        if point == "stopping":
            m.setattr(rig.backend, "names", crash)          # `stopping` is written; the sandbox is not gone
        else:
            m.setattr(gitwip, "wip_commit", crash if point == "ended" else commit_then_crash)
        with pytest.raises(Crash):
            rig.runtime.expire("alpha", deadline)
    owed = record(rig, spec.session_key)
    assert (owed.stop_reason, owed.wip_mark) == ("lifetime", f"lifetime:{spec.session_key}:1")
    assert owed.phase is (Phase.STOPPING if point == "stopping" else Phase.ENDED)
    if replay == "expire":
        rig.runtime.expire("alpha", deadline)
        assert record(rig, spec.session_key).phase is Phase.ENDED
    else:
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
        assert record(rig, spec.session_key).generation == 2
    assert record(rig, spec.session_key).wip_mark == ""
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]     # landed, and only once


@needs_tools
def test_a_failed_wip_commit_is_recorded_and_retried(rig: RuntimeRig,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """T10 r1: a failed commit keeps its mark (the plan's gap 8 cleared it), so the next expire retries it."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    deadline = record(rig, spec.session_key).deadline

    def failed(*_: object) -> str:
        raise gitwip.GitFailed("git commit exited 1")

    with monkeypatch.context() as m:
        m.setattr(gitwip, "wip_commit", failed)
        rig.runtime.expire("alpha", deadline)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.error, rec.wip_mark) == (Phase.ENDED, WIP_FAILED, f"lifetime:{spec.session_key}:1")
    assert wip_marks(rig) == []
    rig.runtime.expire("alpha", deadline)
    rec = record(rig, spec.session_key)
    assert (rec.error, rec.wip_mark) == ("", "")
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]


@needs_tools
def test_a_create_that_times_out_keeps_its_watcher_through_cleanup(rig: RuntimeRig) -> None:
    """T11 r2: a create that timed out may still complete. wsd's cleanup confirms the sandbox absent and
    ends the launch, but the generation's watcher stays: if the create lands and wsd then crashes, the
    watcher is all that stops it at its deadline."""
    spec = launch_spec(rig, "p-one")
    rig.backend.create_late = 1
    with pytest.raises(LaunchFailed, match="timed out"):
        rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.created) == (Phase.ENDED, False)
    rig.backend.finish_creates()                         # the create lands; wsd crashes and does nothing more
    assert rec.sandbox in rig.backend.boxes
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))
    assert rig.backend.reapers == [(rec.sandbox, rec.deadline)]          # it watches exactly that sandbox


@needs_tools
def test_the_next_generation_never_removes_an_earlier_watcher(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.backend.create_late = 1
    with pytest.raises(LaunchFailed):
        rig.runtime.launch(spec)
    rig.runtime.launch(replace(spec, generation=2))
    names = [rig.runtime.reaper_name(spec.session_key, g) for g in (1, 2)]
    assert all(rig.tmux.has_session(n) for n in names)
    rig.backend.finish_creates()                         # generation 1's create lands after 2 launched
    assert sandbox_name(spec.session_key, 1) in rig.backend.boxes
    rig.runtime.stop(spec.session_key)
    assert rig.backend.boxes == {}                       # every sandbox of the key goes with the end
    assert rig.tmux.has_session(names[0]) and not rig.tmux.has_session(names[1])


@needs_tools
def test_one_stop_that_fails_does_not_keep_the_next_from_stopping(rig: RuntimeRig) -> None:
    """T11 r1: every session of the workstream is tried; the failure is raised after the pass."""
    one, two = launch_spec(rig, "p-one"), launch_spec(rig, "p-two")
    rig.runtime.launch(one)
    rig.runtime.launch(two)
    deadline = max(record(rig, one.session_key).deadline, record(rig, two.session_key).deadline)
    rig.backend.delete_unconfirmed = 1                      # whichever is tried first can't be confirmed
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.expire("alpha", deadline)
    phases = sorted(record(rig, s.session_key).phase for s in (one, two))
    assert phases == sorted([Phase.ENDED, Phase.STOPPING])
    rig.runtime.expire("alpha", deadline)
    assert {record(rig, s.session_key).phase for s in (one, two)} == {Phase.ENDED}


@needs_tools
def test_a_lifetime_stop_whose_landing_fails_makes_no_host_wip(rig: RuntimeRig) -> None:
    """D26: a host WIP would diverge from the commits that could not be landed, so the mark stays owed and
    nothing is committed, however often expire runs."""
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    deadline = record(rig, spec.session_key).deadline
    before = host_log(rig)
    (rig.worktree / "edit.txt").write_text("unsaved work\n")
    (rig.runtime.layout(spec.session_key).git / "objects" / "zz").symlink_to(rig.root)
    rig.runtime.expire("alpha", deadline)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.unlanded, rec.wip_mark) == (Phase.ENDED, True, f"lifetime:{spec.session_key}:1")
    rig.runtime.expire("alpha", deadline)
    assert host_log(rig) == before and wip_marks(rig) == []
    assert record(rig, spec.session_key).wip_mark


def host_log(rig: RuntimeRig) -> str:
    return subprocess.run(["git", "-C", str(rig.repo), "log", "--format=%H", "btq/btq-1"],
                          capture_output=True, text=True, check=True).stdout


class Killed(Exception):
    """wsd kills the reaper's process: the only way it ends."""


def test_the_reaper_waits_for_the_deadline_then_kills_and_deletes_until_confirmed() -> None:
    now = [1000.0]
    slept: list[float] = []
    tries: list[float] = []
    kills: list[float] = []
    answers: list[bool | None] = [None, False, True, True]        # None: the backend is down

    def sleep(seconds: float) -> None:
        if not answers:
            raise Killed
        slept.append(seconds)
        now[0] += seconds

    def delete(name: str) -> bool:
        assert name == "box"
        tries.append(now[0])
        answer = answers.pop(0)
        if answer is None:
            raise BackendUnavailable("down")
        return answer

    def kill(name: str) -> None:
        assert name == "box"
        kills.append(now[0])

    with pytest.raises(Killed):
        reaper.reap("box", 1075, delete, kill, clock=lambda: now[0], sleep=sleep)
    assert slept[:3] == [30, 30, 15] and tries[0] == kills[0] == 1075 and len(tries) == len(kills) == 4
    assert slept[3:] == [5, 5, 30]       # retried until confirmed, then it keeps watching at the slower poll


def test_the_reaper_outlasts_any_outage_then_deletes() -> None:
    """wsd is gone and the OpenShell gateway is down for a day, far past any retry budget. The reaper keeps
    killing the workload locally and deletes the sandbox once the gateway is back."""
    now = [0.0]
    outage = 86_400.0
    tries: list[float] = []
    kills: list[float] = []

    def sleep(seconds: float) -> None:
        if now[0] >= outage:
            raise Killed
        now[0] += seconds

    def delete(name: str) -> bool:
        tries.append(now[0])
        if now[0] < outage:
            raise BackendUnavailable("the gateway is down")
        return True

    def kill(name: str) -> None:
        kills.append(now[0])
        if len(kills) % 2:
            raise BackendError("podman kill timed out")         # a failed kill is retried too

    with pytest.raises(Killed):
        reaper.reap("box", 0, delete, kill, clock=lambda: now[0], sleep=sleep)
    assert kills[0] == 0 and tries[-1] >= outage and len(tries) > 60          # 60: r2's old budget


def test_the_reaper_outlives_a_create_that_comes_after_its_deadline() -> None:
    """T11 r1: wsd starts the reaper before the sandbox exists. If wsd stalls until past the deadline (or a
    create completes late), the reaper first finds nothing to delete; it must still be there to kill and
    delete the sandbox once it appears, even if wsd then crashes."""
    now = [0.0]
    boxes: set[str] = set()
    killed: list[float] = []
    deleted: list[float] = []
    sleeps = [0]

    def sleep(seconds: float) -> None:
        sleeps[0] += 1
        if sleeps[0] == 4:
            boxes.add("box")                    # wsd resumes and creates, then dies
        if deleted:
            raise Killed
        now[0] += seconds

    def delete(name: str) -> bool:
        if name in boxes:
            boxes.discard(name)
            deleted.append(now[0])
        return True                             # absent: confirmed, which says nothing about later

    def kill(name: str) -> None:
        if name in boxes:
            killed.append(now[0])

    with pytest.raises(Killed):
        reaper.reap("box", 0, delete, kill, clock=lambda: now[0], sleep=sleep)
    assert killed == deleted == [now[0]] and now[0] == 4 * reaper.POLL_SECONDS and boxes == set()


def test_a_watcher_whose_sandbox_stays_absent_exits_after_its_bound() -> None:
    now = [0.0]
    tries: list[float] = []

    def delete(name: str) -> bool:
        tries.append(now[0])
        return True

    def sleep(seconds: float) -> None:
        now[0] += seconds

    reaper.reap("box", 100, delete, lambda _: None, clock=lambda: now[0], sleep=sleep)
    assert tries[0] == 100 and 100 + reaper.LINGER_SECONDS <= tries[-1] < 100 + reaper.LINGER_SECONDS + 30


def test_past_its_bound_a_watcher_still_waits_for_a_confirmed_deletion() -> None:
    now = [0.0]
    late = reaper.LINGER_SECONDS + 3_600.0

    def delete(name: str) -> bool:
        if now[0] < late:
            raise BackendUnavailable("the gateway is down")
        return True

    def sleep(seconds: float) -> None:
        now[0] += seconds

    reaper.reap("box", 0, delete, lambda _: None, clock=lambda: now[0], sleep=sleep)
    assert late <= now[0] < late + 5


def test_the_reaper_reads_the_argv_the_backend_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 4's `reaper_argv` and `main` agree, so the backstop wsd starts is the one tested here."""
    backend = OpenShellBackend("/x/openshell", "/x/podman", "img:1", {"PATH": "/usr/bin"})
    argv = backend.reaper_argv("box", 1075)
    module = argv.index("heterodyne.sandbox.reaper")
    assert argv[module - 2:module] == ["-I", "-m"]
    seen: list[tuple[str, int, str, str, str]] = []

    def reap(name: str, deadline: int, delete: object, kill: object) -> None:
        assert isinstance(delete, types.MethodType) and isinstance(delete.__self__, OpenShellBackend)
        backend = delete.__self__
        seen.append((name, deadline, backend.openshell, backend.podman, backend.image))

    monkeypatch.setattr(reaper, "reap", reap)
    assert reaper.main(argv[module + 1:]) == 0
    assert seen == [("box", 1075, "/x/openshell", "/x/podman", "img:1")]


def test_no_runtime_expires_nothing() -> None:
    NoRuntime().expire("alpha", 0)


def test_pickup_expires_before_it_sweeps(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.runtime.expires == [(WS, rig.clock())]
    rig.runtime.expire_failures = 1
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.RUNTIME_UNAVAILABLE: "lifetime stop not confirmed"}


@needs_tools
def test_a_launch_stalled_past_its_deadline_creates_nothing(rig: RuntimeRig,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """T11 r3: wsd stalls after starting the watcher and before the create, longer than the watcher's
    linger. The create is never submitted, so no sandbox can land after the watcher has gone."""
    spec = launch_spec(rig, "p-one")
    real = rig.runtime._reaper  # pyright: ignore[reportPrivateUsage]

    def stalled(rec: SessionRecord) -> None:
        real(rec)
        rig.clock.advance(rec.deadline - rig.clock.now + reaper.LINGER_SECONDS + 1)

    monkeypatch.setattr(rig.runtime, "_reaper", stalled)
    with pytest.raises(LaunchFailed, match="stalled past its deadline"):
        rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.created) == (Phase.ENDED, False)
    assert rig.backend.created == [] and rig.backend.pending == [] and rig.backend.boxes == {}


def test_a_create_that_returns_past_its_deadline_is_destroyed_at_once(rig: RuntimeRig,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """T11 r3: the check before the create leaves a window up to its submission. A create that returns at
    or past the deadline is destroyed and its record ended at once, never left to the watcher."""
    spec = launch_spec(rig, "p-one")
    real = rig.backend.create
    trusted: list[object] = []

    def slow(*args: object, **kwargs: object) -> None:
        real(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        rec = record(rig, spec.session_key)
        rig.clock.advance(rec.deadline - rig.clock.now)

    def trust(*args: object) -> None:
        trusted.append(args)

    monkeypatch.setattr(rig.backend, "create", slow)
    monkeypatch.setattr(rig.runtime, "_trust", trust)
    with pytest.raises(LaunchFailed, match="sandbox was created past its deadline"):
        rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert (rec.phase, rec.created) == (Phase.ENDED, True)
    assert len(rig.backend.created) == 1 and rig.backend.boxes == {} and trusted == []
    assert not rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))


@needs_tools
@pytest.mark.parametrize("replay", ["expire", "launch"])
def test_a_record_from_before_created_replays_and_keeps_its_watcher(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, replay: str) -> None:
    """T11: a record written before `created` existed reads it as False, the safe side. Its lifetime stop
    replays to the end and lands the WIP once, and its watcher is never removed (LINGER_SECONDS bounds it)."""
    spec = launch_spec(rig, "p-one")
    first = rig.runtime.launch(spec)
    deadline = record(rig, spec.session_key).deadline
    rig.clock.advance(deadline - rig.clock.now)

    def crash(*_: object) -> None:
        raise Crash

    with monkeypatch.context() as m:
        m.setattr(rig.backend, "names", crash)          # `stopping` is written; the sandbox is not gone
        with pytest.raises(Crash):
            rig.runtime.expire("alpha", deadline)
    path = rig.runtime.layout(spec.session_key).record
    data = json.loads(path.read_text())
    assert data.pop("created") is True
    path.write_text(json.dumps(data))                 # as a record from before the field was written
    assert record(rig, spec.session_key).created is False
    if replay == "expire":
        rig.runtime.expire("alpha", deadline)
        assert record(rig, spec.session_key).phase is Phase.ENDED
    else:
        rig.runtime.launch(replace(spec, generation=2, resume=True, native_id=first.native_id))
        assert record(rig, spec.session_key).generation == 2
    assert wip_marks(rig) == [f"wsd-park: lifetime:{spec.session_key}:1"]
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))
