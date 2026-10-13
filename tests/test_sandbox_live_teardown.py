"""The live sandbox module's teardown (tests/live/test_live_sandbox.py), offline on the fake backend: it
reports what it can't confirm gone, so the module keeps the scratch, records and tmux server for it."""

import contextlib
import shutil
from collections.abc import Iterator
from dataclasses import replace

import pytest
from sandbox_env import RuntimeRig, bead_worktree, launch_spec, runtime_rig, sandbox_teardown, short_dir
from tmux_guard import new_test_tmux
from wsd_env import Clock

from heterodyne.sandbox.runtime import Phase, SandboxRuntime, read_record
from heterodyne.wsd.runtime import LaunchFailed, RuntimeUnavailable

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                reason="tmux and setsid are needed")


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        rig = runtime_rig(root, tmux, Clock())
        try:
            yield rig
        finally:
            rig.backend.finish_creates()
            for key in list(rig.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    rig.runtime.stop(key)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def test_a_clean_teardown_reports_nothing(rig: RuntimeRig) -> None:
    one = launch_spec(rig, "p-one")
    two = replace(launch_spec(rig, "p-two", "btq-2"), worktree=bead_worktree(rig, "btq-2"))
    rig.runtime.launch(one)
    rig.runtime.launch(two)
    assert sandbox_teardown(rig.runtime, [one.session_key, two.session_key, one.session_key],
                            rig.backend.names) == []
    assert rig.backend.boxes == {}


def test_an_ended_record_whose_create_is_unresolved_is_uncertain(rig: RuntimeRig) -> None:
    """Codex T14 r2: a create that timed out ends the launch with create_started and not created, and its
    watcher kept. The sandbox list is empty, but the create may still land: that is not a clean end."""
    spec = launch_spec(rig, "p-one")
    rig.backend.create_late = 1
    with pytest.raises(LaunchFailed, match="timed out"):
        rig.runtime.launch(spec)
    rec = read_record(rig.runtime.layout(spec.session_key))
    assert rec is not None and (rec.phase, rec.create_started, rec.created) == (Phase.ENDED, True, False)
    assert rig.backend.names() == set()
    uncertain = sandbox_teardown(rig.runtime, [spec.session_key], rig.backend.names)
    assert any("its create is unresolved" in u for u in uncertain)
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))      # left for the caller


def test_an_earlier_generations_late_create_is_uncertain(rig: RuntimeRig) -> None:
    """The record holds only the latest generation: generation 1's unresolved create shows by its watcher."""
    spec = launch_spec(rig, "p-one")
    rig.backend.create_late = 1
    with pytest.raises(LaunchFailed):
        rig.runtime.launch(spec)
    rig.runtime.launch(replace(spec, generation=2))
    uncertain = sandbox_teardown(rig.runtime, [spec.session_key], rig.backend.names)
    assert uncertain == [f"session {spec.session_key}: the watchers of generations [1] are still running"]


def test_every_session_is_tried_and_a_listed_sandbox_or_a_failed_list_is_uncertain(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    one = launch_spec(rig, "p-one")
    two = replace(launch_spec(rig, "p-two", "btq-2"), worktree=bead_worktree(rig, "btq-2"))
    rig.runtime.launch(one)
    rig.runtime.launch(two)
    stop = rig.runtime.stop

    def failing(key: str) -> None:
        if key == one.session_key:
            raise RuntimeUnavailable("the session's end could not be confirmed")
        stop(key)

    monkeypatch.setattr(rig.runtime, "stop", failing)
    left = read_record(rig.runtime.layout(one.session_key))
    assert left is not None
    uncertain = sandbox_teardown(rig.runtime, [one.session_key, two.session_key], rig.backend.names)
    assert uncertain[0].startswith(f"session {one.session_key}: RuntimeUnavailable")
    assert uncertain[-1] == f"sandboxes still listed: {left.sandbox}"
    assert rig.backend.boxes.keys() == {left.sandbox}              # the second was still stopped
    rig.backend.list_failures = 1
    assert sandbox_teardown(rig.runtime, [two.session_key], rig.backend.names) == [
        "the sandbox list: the fake backend is down"]


def test_a_session_launched_through_another_runtime_is_stopped_through_it(rig: RuntimeRig) -> None:
    """Codex T15 r1: a control launches through a runtime of its own, and only that runtime holds the
    session's server. Teardown stops each session through its owner, so the server is closed too."""
    other = SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)
    spec = launch_spec(rig, "p-one")
    other.launch(spec)
    assert spec.session_key in other.servers
    assert sandbox_teardown(rig.runtime, [spec.session_key], rig.backend.names,
                            owners={spec.session_key: other}) == []
    assert other.servers == {}
    assert rig.backend.boxes == {}
