"""Plan 4c Task 9: the taint watch (plan 4c E17's second path). The runtime re-walks every install it has a
live session on, at each session's end and from `expire` once the last walk is `TAINT_SECONDS` old; a
mismatch writes the taint marker and only then queues one `InstallTainted` per session on the install.

The accepted readings of E17 these tests pin (the PR lists (b)3 and (b)4 as interpretations of E17):
- (b)2: the last walk's time is runtime-wide, per (adapter, install); each signal goes to its session's
  workstream's queue, whichever workstream's `expire` ran the walk.
- (b)3: a walk that finds a marker already there re-queues the signals for the live sessions on it.
- (b)4: the end's walk runs after the teardown and before `ended`, and signals the ending generation too.
- (b)5: a walk that fails for any other reason logs, writes no marker, sends no signal and leaves the
  timer alone (the next `expire` tries again); it never blocks the lifetime pass.
- (b)6: one `INSTALL_TAINTED` text, defined in `wsd.runtime`; `harness.verify` imports it."""

import contextlib
import errno
import shutil
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from harness_env import rewrite
from sandbox_env import RuntimeRig, bead_worktree, launch_spec, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import Clock

from heterodyne.agents.registry import ADAPTERS
from heterodyne.harness import verify
from heterodyne.harness.resources import store_core as sc
from heterodyne.sandbox import runtime as runtime_mod
from heterodyne.sandbox.runtime import TAINT_SECONDS, Phase, SandboxRuntime, SessionRecord, read_record
from heterodyne.wsd.runtime import INSTALL_TAINTED, InstallTainted, LaunchFailed, RuntimeUnavailable, Signal

needs_tools = pytest.mark.skipif(shutil.which("tmux") is None or shutil.which("setsid") is None,
                                 reason="tmux and setsid are needed")
pytestmark = needs_tools


# --- Task 8's seams. Everything these tests assume about T8 is here, so its final names change only this
# section: the record's pin fields, where the rig's store and taint markers live, and the walk's check. ---

def install_of(rec: SessionRecord) -> str:
    """The install the session's generation runs (E15's pin, as the record keeps it)."""
    return rec.install_name


def store_root(rig: RuntimeRig) -> Path:
    """The rig's `LocalStore` (`harness_env.store_fixture(rig.root / "i", adapter)`)."""
    return rig.root / "i" / "store"


def taint_dir(rig: RuntimeRig) -> Path:
    """The runtime's `taint_dir` (E17): `harness_env.TAINT_DIR` beside the rig's store."""
    return rig.runtime.c.taint_dir


def install_root(rig: RuntimeRig, adapter: str, install: str) -> Path:
    return store_root(rig) / adapter / install


def patch_check(monkeypatch: pytest.MonkeyPatch, check: Callable[..., None]) -> None:
    """`store_core.check_fast` replaced wherever the walk may call it from."""
    for module in (sc, runtime_mod, verify):
        monkeypatch.setattr(module, "check_fast", check, raising=module is sc)


# --- the rig ---

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
            for d in (store_root(rig), taint_dir(rig)):
                if d.exists():
                    for p in (d, *d.rglob("*")):
                        if p.is_dir() and not p.is_symlink():
                            p.chmod(0o755)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def launch(rig: RuntimeRig, profile: str, bead: str, ws: str = "alpha") -> SessionRecord:
    """A running session on its own branch; `btq-1` uses the rig's worktree."""
    spec = launch_spec(rig, profile, bead)
    if bead != "btq-1":
        spec = replace(spec, worktree=bead_worktree(rig, bead))
    spec = replace(spec, ws=ws)
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert rec.phase is Phase.RUNNING
    return rec


def change(rig: RuntimeRig, rec: SessionRecord) -> None:
    """One file of the session's install rewritten in place, same size: the fast walk's change time and
    bytes differ (r16 §7)."""
    root = install_root(rig, rec.adapter, install_of(rec))
    first = min(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    rewrite(first)


def marker(rig: RuntimeRig, rec: SessionRecord) -> Path:
    return taint_dir(rig) / rec.adapter / install_of(rec)


def tainted(rig: RuntimeRig, ws: str) -> list[Signal]:
    return [s for s in rig.runtime.signals(ws) if isinstance(s, InstallTainted)]


def signal(rec: SessionRecord) -> InstallTainted:
    return InstallTainted(rec.key, rec.generation, rec.adapter, install_of(rec))


def counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Every fast walk the runtime runs, counted; each still runs the real check."""
    calls: list[int] = []
    real = sc.check_fast

    def check(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        real(*args, **kwargs)

    patch_check(monkeypatch, check)
    return calls


# --- the plan's tests ---

def test_a_change_found_at_session_end_taints_and_signals_every_session_on_the_install(
        rig: RuntimeRig) -> None:
    ending, other = launch(rig, "p-two", "btq-1"), launch(rig, "p-two", "btq-2")
    elsewhere = launch(rig, "p-one", "btq-3")              # Claude's install: another install
    assert install_of(ending) == install_of(other) != install_of(elsewhere)
    change(rig, ending)
    rig.runtime.stop(ending.key)
    assert record(rig, ending.key).phase is Phase.ENDED
    assert marker(rig, ending).is_file() and marker(rig, ending).stat().st_mode & 0o777 == 0o600
    assert verify.tainted(taint_dir(rig), "codex", install_of(ending))
    assert sorted(tainted(rig, "alpha"), key=lambda s: s.session_key) == sorted(
        [signal(ending), signal(other)], key=lambda s: s.session_key)     # (b)4: the ending one too
    assert not marker(rig, elsewhere).exists()


def test_the_walk_repeats_every_ten_minutes_while_a_session_runs(rig: RuntimeRig) -> None:
    assert TAINT_SECONDS == 600
    rec = launch(rig, "p-two", "btq-1")
    change(rig, rec)
    rig.clock.advance(TAINT_SECONDS - 1)
    rig.runtime.expire("alpha", rig.clock())
    assert not marker(rig, rec).exists() and tainted(rig, "alpha") == []
    rig.clock.advance(1)
    rig.runtime.expire("alpha", rig.clock())
    assert marker(rig, rec).is_file()
    assert tainted(rig, "alpha") == [signal(rec)]
    assert record(rig, rec.key).phase is Phase.RUNNING      # the walk stops nothing: wsd parks the bead


def test_the_marker_is_written_before_the_signal_and_survives_a_restart(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = launch(rig, "p-two", "btq-1")
    seen: list[tuple[bool, int]] = []
    real = verify.taint

    def taint(*args: Any, **kwargs: Any) -> None:
        queued = sum(isinstance(s, InstallTainted) for q in rig.runtime.queued.values() for s in q)
        real(*args, **kwargs)
        seen.append((marker(rig, rec).is_file(), queued))

    monkeypatch.setattr(verify, "taint", taint)
    change(rig, rec)
    rig.runtime.stop(rec.key)
    assert seen == [(True, 0)]                               # the marker is durable before any signal
    restarted = SandboxRuntime(rig.runtime.c, rig.backend, rig.selftest)
    with pytest.raises(LaunchFailed) as refused:             # before any signal is drained
        restarted.verified_cli(ADAPTERS["codex"])
    assert str(refused.value) == INSTALL_TAINTED
    assert tainted(rig, "alpha") == [signal(rec)]


def test_a_walk_that_finds_nothing_writes_no_marker_and_sends_no_signal(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    ending, running = launch(rig, "p-two", "btq-1"), launch(rig, "p-two", "btq-2")
    walks = counting(monkeypatch)
    rig.runtime.stop(ending.key)
    rig.clock.advance(TAINT_SECONDS)
    rig.runtime.expire("alpha", rig.clock())
    assert len(walks) == 2                                   # the end's walk, then the interval's
    assert not taint_dir(rig).exists() or not any(taint_dir(rig).rglob("*"))
    assert tainted(rig, "alpha") == []
    assert record(rig, running.key).phase is Phase.RUNNING


def test_a_login_file_copied_in_under_the_fallback_is_found_by_the_next_walk(rig: RuntimeRig) -> None:
    """The §11 fallback control: a `LocalStore` install is the service user's to write, so a copy made
    after the launch's verify and its probe is caught only by the walk."""
    rec = launch(rig, "p-two", "btq-1")
    root = install_root(rig, rec.adapter, install_of(rec))
    login = rig.home / ".codex" / "auth.json"
    assert login.is_file()
    root.chmod(0o755)
    shutil.copy2(login, root / "auth.json")
    root.chmod(0o555)
    rig.clock.advance(TAINT_SECONDS)
    rig.runtime.expire("alpha", rig.clock())
    assert marker(rig, rec).is_file()
    assert tainted(rig, "alpha") == [signal(rec)]


# --- the accepted readings of E17 ---

def test_one_install_tainted_text_defined_by_wsd_and_refused_by_verify() -> None:
    """(b)6: the park's `why` and verify's refusal are one constant, `wsd.runtime`'s."""
    assert verify.INSTALL_TAINTED is INSTALL_TAINTED
    assert INSTALL_TAINTED in verify.REFUSALS


def test_two_workstreams_on_one_install_walk_it_once_per_interval_and_each_gets_its_signals(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b)2: the walk's time is the runtime's, per install; each signal goes to its session's queue."""
    alpha, beta = launch(rig, "p-two", "btq-1", "alpha"), launch(rig, "p-two", "btq-2", "beta")
    assert install_of(alpha) == install_of(beta)
    walks = counting(monkeypatch)
    change(rig, alpha)
    rig.clock.advance(TAINT_SECONDS)
    rig.runtime.expire("alpha", rig.clock())
    rig.runtime.expire("beta", rig.clock())
    assert len(walks) == 1
    assert tainted(rig, "alpha") == [signal(alpha)]
    assert tainted(rig, "beta") == [signal(beta)]


def test_an_existing_marker_re_signals_the_live_sessions_on_its_install(rig: RuntimeRig) -> None:
    """(b)3: a marker already there (an earlier walk, or a launch's verify) is not news to the runtime,
    but the sessions on the install may be new since it was written."""
    rec = launch(rig, "p-two", "btq-1")
    verify.taint(taint_dir(rig), rec.adapter, install_of(rec), verify.INSTALL_CHANGED)
    rig.clock.advance(TAINT_SECONDS)
    rig.runtime.expire("alpha", rig.clock())
    assert tainted(rig, "alpha") == [signal(rec)]


def test_the_end_walk_runs_after_the_teardown_and_signals_the_ending_generation(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b)4: the walk sees the tree as the generation left it, and the bead it ran is parked too."""
    rec = launch(rig, "p-two", "btq-1")
    phases: list[tuple[Phase, bool]] = []
    real = sc.check_fast

    def check(*args: Any, **kwargs: Any) -> None:
        now = record(rig, rec.key)
        phases.append((now.phase, now.sandbox in rig.backend.deleted))
        real(*args, **kwargs)

    patch_check(monkeypatch, check)
    change(rig, rec)
    rig.runtime.stop(rec.key)
    assert phases == [(Phase.STOPPING, True)]
    assert tainted(rig, "alpha") == [signal(rec)]


def test_a_walk_that_cant_run_taints_nothing_and_is_tried_again_at_the_next_expire(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b)5: only a mismatch taints; an I/O failure is no evidence, so the timer stays due."""
    rec = launch(rig, "p-two", "btq-1")
    failures: list[int] = []

    def broken(*_: Any, **__: Any) -> None:
        failures.append(1)
        raise OSError(errno.EIO, "unreadable")

    patch_check(monkeypatch, broken)
    rig.clock.advance(TAINT_SECONDS)
    rig.runtime.expire("alpha", rig.clock())
    assert failures == [1]
    assert not marker(rig, rec).exists() and tainted(rig, "alpha") == []
    walks = counting(monkeypatch)
    rig.runtime.expire("alpha", rig.clock())                 # the same instant: the walk is still due
    assert len(walks) == 1
    assert not marker(rig, rec).exists()


def test_a_walk_that_cant_run_never_blocks_a_lifetime_stop(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = launch(rig, "p-two", "btq-1")

    def broken(*_: Any, **__: Any) -> None:
        raise OSError(errno.EIO, "unreadable")

    patch_check(monkeypatch, broken)
    rig.clock.now = rec.deadline
    rig.runtime.expire("alpha", rig.clock())
    ended = record(rig, rec.key)
    assert (ended.phase, ended.stop_reason) == (Phase.ENDED, "lifetime")
    assert not marker(rig, rec).exists() and tainted(rig, "alpha") == []
