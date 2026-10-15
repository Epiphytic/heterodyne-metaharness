"""7hokw runtime cases 9–24. Only private fake backend/tmux fixtures are exercised."""

import contextlib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import msgspec
import pytest
from sandbox_env import RuntimeRig, launch_spec, runtime_rig, short_dir
from tmux_guard import new_test_tmux
from wsd_env import Clock

from heterodyne.sandbox import creates
from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.reaper import LINGER_SECONDS
from heterodyne.sandbox.runtime import Phase, SessionRecord, read_record, write_record
from heterodyne.sandbox.spec import short_id
from heterodyne.tmux import TmuxError
from heterodyne.wsd.runtime import LaunchFailed, ResolveRefused, RuntimeUnavailable


@pytest.fixture
def rig() -> Iterator[RuntimeRig]:
    with short_dir() as root:
        tmux = new_test_tmux()
        env = runtime_rig(root, tmux, Clock())
        try:
            yield env
        finally:
            env.backend.delete_unconfirmed = 0
            for key in list(env.runtime.servers):
                with contextlib.suppress(RuntimeUnavailable):
                    env.runtime.stop(key)
            tmux.kill_server()
            assert tmux.socket_path is not None
            tmux.socket_path.unlink(missing_ok=True)


def book(rig: RuntimeRig) -> creates.Ledger:
    return rig.runtime._ledger()  # pyright: ignore[reportPrivateUsage]


def seed(rig: RuntimeRig, *, generation: int = 1, phase: Phase = Phase.ENDED,
         started: bool = True, created: bool = False, migrated: bool = True) -> SessionRecord:
    key = "old-session"
    rec = SessionRecord(key=key, ws="alpha", bead="btq-old", role="worker", profile="p-one",
                        adapter="claude", generation=generation, phase=phase,
                        sandbox=f"{short_id(key)}g{generation}", tmux_session=rig.runtime.tmux_name(key),
                        deadline=rig.clock() - 100, create_started=started, created=created,
                        create_ledger=migrated)
    write_record(rig.runtime.layout(key), rec)
    if migrated:
        assert rig.runtime._submission(book(rig), rec)  # pyright: ignore[reportPrivateUsage]
    return rec


def record(rig: RuntimeRig, key: str) -> SessionRecord:
    rec = read_record(rig.runtime.layout(key))
    assert rec is not None
    return rec


def test_ended_or_missing_or_replaced_record_never_settles(rig: RuntimeRig) -> None:
    rec = seed(rig)
    for change in ("ended", "missing", "replaced"):
        if change == "missing":
            rig.runtime.layout(rec.key).record.unlink()
        elif change == "replaced":
            write_record(rig.runtime.layout(rec.key), msgspec.structs.replace(
                rec, sandbox=f"{short_id(rec.key)}g2", generation=2))
        rig.runtime.sessions("alpha")
        assert not creates.settled(book(rig).directory, rec.sandbox)
        assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))


def test_replacement_keeps_prior_unknown_and_sweeps_late_sandbox(rig: RuntimeRig,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-one")
    real_create = rig.backend.create
    monkeypatch.setattr(rig.backend, "create", lambda *_: (_ for _ in ()).throw(BackendError("timeout")))
    with pytest.raises(LaunchFailed):
        rig.runtime.launch(spec)
    old = record(rig, spec.session_key)
    monkeypatch.setattr(rig.backend, "create", real_create)
    rig.runtime.launch(replace(spec, generation=2))
    rig.backend.extra.add(old.sandbox)
    real_delete = rig.backend.delete

    def delete(name: str) -> bool:
        if name == old.sandbox:
            assert creates.read(book(rig).directory / f"{name}.observed.json", name) is not None
        return real_delete(name)

    monkeypatch.setattr(rig.backend, "delete", delete)
    assert len(rig.runtime.sessions("alpha")) == 1
    assert old.sandbox not in rig.backend.names()
    assert not creates.settled(book(rig).directory, old.sandbox)
    assert rig.tmux.has_session(rig.runtime.reaper_name(spec.session_key, 1))


def test_running_backstop_is_restored_after_lost_watcher(rig: RuntimeRig) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    watcher = rig.runtime.reaper_name(rec.key, rec.generation)
    rig.tmux.kill(watcher)  # the agent is kept live; only its lost backstop is restored
    before = len(rig.backend.reapers)
    assert len(rig.runtime.sessions("alpha")) == 1
    assert len(rig.backend.reapers) == before + 1
    assert rig.backend.reapers[-1][:2] == (rec.sandbox, rec.deadline)
    assert creates.settled(book(rig).directory, rec.sandbox)
    assert not creates.gone(book(rig).directory, rec.sandbox)
    assert book(rig).submission(rec.sandbox) is not None


def test_observed_before_crash_remains_watched(rig: RuntimeRig) -> None:
    rec = seed(rig)
    book(rig).observe(rec.sandbox, "watcher", rig.clock())
    rig.backend.extra.add(rec.sandbox)
    rig.runtime.sessions("alpha")
    assert rec.sandbox not in rig.backend.names()
    assert not creates.gone(book(rig).directory, rec.sandbox)
    assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))


@pytest.mark.parametrize("confirmed", [False, True])
def test_teardown_gone_precedes_watcher_kill_only_after_confirmed_delete(
        rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch, confirmed: bool) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    real_kill = rig.tmux.kill
    watcher = rig.runtime.reaper_name(rec.key, 1)

    def kill(name: str) -> None:
        if name == watcher:
            assert creates.gone(book(rig).directory, rec.sandbox)
        real_kill(name)

    monkeypatch.setattr(rig.tmux, "kill", kill)
    if not confirmed:
        rig.backend.delete_unconfirmed = 1
        with pytest.raises(RuntimeUnavailable):
            rig.runtime.stop(rec.key)
        assert not creates.gone(book(rig).directory, rec.sandbox)
        assert rig.tmux.has_session(watcher)
    else:
        rig.runtime.stop(rec.key)
        assert creates.gone(book(rig).directory, rec.sandbox)
        assert not rig.tmux.has_session(watcher)


@pytest.mark.parametrize("missing", [False, True])
def test_sweep_finishes_settled_ended_or_missing_record(rig: RuntimeRig, missing: bool) -> None:
    rec = seed(rig, created=True)
    book(rig).put(rec.sandbox, "settled", how="returned", at=1)
    if missing:
        rig.runtime.layout(rec.key).record.unlink()
    rig.runtime.sessions("alpha")
    assert creates.gone(book(rig).directory, rec.sandbox)
    gone = creates.read(book(rig).directory / f"{rec.sandbox}.gone.json", rec.sandbox)
    assert gone is not None and gone["by"] == "sweep"


def test_unconfirmed_stale_sweep_holds_and_preserves_watcher(rig: RuntimeRig) -> None:
    rec = seed(rig)
    rig.backend.extra.add(rec.sandbox)
    rig.backend.delete_unconfirmed = 1
    with pytest.raises(RuntimeUnavailable, match="earlier generation"):
        rig.runtime.sessions("alpha")
    assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))
    assert not creates.gone(book(rig).directory, rec.sandbox)


def test_resolve_guard_uses_ledger_even_without_pane(rig: RuntimeRig) -> None:
    rec = seed(rig)
    sid, runtime = short_id(rec.key), rig.runtime
    assert "not settled and deleted" in runtime._running(sid)  # pyright: ignore[reportPrivateUsage]
    book(rig).observe(rec.sandbox, "watcher", 1)
    assert "not settled and deleted" in runtime._running(sid)  # pyright: ignore[reportPrivateUsage]
    book(rig).put(rec.sandbox, "settled", how="operator", at=1)
    assert runtime._running(sid)  # pyright: ignore[reportPrivateUsage]
    book(rig).put(rec.sandbox, "gone", by="watcher", at=2)
    assert runtime._running(sid) == ""  # pyright: ignore[reportPrivateUsage]
    rig.runtime.layout(rec.key).record.unlink()
    assert creates.settled(book(rig).directory, rec.sandbox)


@pytest.mark.parametrize(("started", "created", "expected"),
                         [(False, False, "never_submitted"), (True, True, "returned"),
                          (True, False, None)])
def test_recovery_only_uses_same_generation_evidence(rig: RuntimeRig, started: bool,
                                                     created: bool, expected: str | None) -> None:
    rec = seed(rig, started=started, created=created)
    rig.runtime._recover_create(book(rig), rec)  # pyright: ignore[reportPrivateUsage]
    evidence = creates.settlement(book(rig).directory, rec.sandbox)
    assert (evidence["how"] if evidence else None) == expected


def test_dead_retained_watcher_is_replaced_and_checked(rig: RuntimeRig) -> None:
    rec = seed(rig)
    watcher = rig.runtime.reaper_name(rec.key, 1)
    rig.tmux.new_session(watcher, rig.root, ["/bin/sh", "-c", "exit 1"])
    assert rig.runtime._until(lambda: rig.tmux.pane_dead(watcher), 2)  # pyright: ignore[reportPrivateUsage]
    rig.runtime.sessions("alpha")
    assert rig.tmux.has_session(watcher) and not rig.tmux.pane_dead(watcher)
    assert not list(book(rig).directory.glob(".ready-*"))


@pytest.mark.parametrize("failure", ["no-ready", "ready-dead", "tmux-error"])
def test_unconfirmed_arm_submits_nothing_and_can_be_retried(rig: RuntimeRig,
                                                           monkeypatch: pytest.MonkeyPatch,
                                                           failure: str) -> None:
    real = rig.backend.reaper_argv

    def argv(name: str, deadline: int, record: Path, watch: Any) -> list[str]:
        if failure == "tmux-error":
            raise TmuxError("unreadable")
        if failure == "ready-dead":
            watch.ready.write_text("ready")
        return ["/bin/sh", "-c", "exit 1"]

    monkeypatch.setattr(rig.backend, "reaper_argv", argv)
    spec = launch_spec(rig, "p-one")
    with pytest.raises(LaunchFailed, match="watcher"):
        rig.runtime.launch(spec)
    assert not rig.backend.created and not rig.backend.pending
    rec = record(rig, spec.session_key)
    assert not creates.settled(book(rig).directory, rec.sandbox)
    monkeypatch.setattr(rig.backend, "reaper_argv", real)
    rig.runtime.sessions("alpha")
    assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))


def test_liveness_failure_holds_without_assuming_watched(rig: RuntimeRig,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    rec = seed(rig)
    rig.runtime.sessions("alpha")
    monkeypatch.setattr(rig.tmux, "pane_dead", lambda _: (_ for _ in ()).throw(TmuxError("down")))
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.sessions("alpha")
    assert not creates.settled(book(rig).directory, rec.sandbox)


@pytest.mark.parametrize("observed", [False, True])
def test_operator_settlement_guards_replay_and_observation(rig: RuntimeRig, observed: bool,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    rec = seed(rig)
    sid = short_id(rec.key)
    with pytest.raises(ResolveRefused, match="linger"):
        rig.runtime.settle_sandbox("alpha", sid, 1)
    rig.clock.advance(LINGER_SECONDS)
    rig.backend.extra.add(rec.sandbox)
    with pytest.raises(ResolveRefused, match="may exist"):
        rig.runtime.settle_sandbox("alpha", sid, 1)
    rig.backend.extra.clear()
    real = rig.backend.names
    monkeypatch.setattr(rig.backend, "names", lambda: (_ for _ in ()).throw(BackendUnavailable("down")))
    with pytest.raises(ResolveRefused, match="may exist"):
        rig.runtime.settle_sandbox("alpha", sid, 1)
    monkeypatch.setattr(rig.backend, "names", real)
    if observed:
        book(rig).observe(rec.sandbox, "watcher", 123)
    reply = rig.runtime.settle_sandbox("alpha", sid, 1)
    assert ("observed at least once by watcher at 123" if observed else "never observed") == reply
    path = book(rig).directory / f"{rec.sandbox}.settled.json"
    before = path.read_bytes(), path.stat().st_mtime_ns
    rig.backend.extra.add(rec.sandbox)   # exact operator replay remains unchanged even after a late create
    assert rig.runtime.settle_sandbox("alpha", sid, 1) == reply
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with pytest.raises(ResolveRefused, match="unknown"):
        rig.runtime.settle_sandbox("alpha", sid, 2)
    with pytest.raises(ResolveRefused, match="unknown"):
        rig.runtime.settle_sandbox("beta", sid, 1)


@pytest.mark.parametrize("how", ["returned", "never_submitted"])
def test_operator_does_not_replace_other_settlement(rig: RuntimeRig, how: str) -> None:
    rec = seed(rig)
    book(rig).put(rec.sandbox, "settled", how=how, at=1)
    with pytest.raises(ResolveRefused, match="another kind"):
        rig.runtime.settle_sandbox("alpha", short_id(rec.key), 1)


@pytest.mark.parametrize(("phase", "started", "created", "expected"), [
    (Phase.CREATING, True, False, "unknown"), (Phase.STOPPING, True, False, "unknown"),
    (Phase.ENDED, True, False, "unknown"), (Phase.RUNNING, True, True, "returned"),
    (Phase.ENDED, True, True, None), (Phase.CREATING, False, False, None),
])
def test_legacy_migration_is_idempotent(rig: RuntimeRig, phase: Phase, started: bool,
                                      created: bool, expected: str | None) -> None:
    rec = seed(rig, phase=phase, started=started, created=created, migrated=False)
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    paths = {p.name: p.read_bytes() for p in book(rig).directory.iterdir()}
    assert (book(rig).submission(rec.sandbox) is not None) == (expected is not None)
    evidence = creates.settlement(book(rig).directory, rec.sandbox)
    assert (evidence["how"] if evidence else None) == ("returned" if expected == "returned" else None)
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    assert {p.name: p.read_bytes() for p in book(rig).directory.iterdir()} == paths
    assert record(rig, rec.key).create_ledger


@pytest.mark.parametrize("point", range(1, 7))
@pytest.mark.parametrize("after_sync", [False, True])
def test_interrupted_cleanup_never_rearms_done_entries(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch,
                                                       point: int, after_sync: bool) -> None:
    rec = seed(rig)
    ledger = book(rig)
    ledger.observe(rec.sandbox, "watcher", 1)
    ledger.put(rec.sandbox, "settled", how="operator", at=2)
    ledger.put(rec.sandbox, "gone", by="watcher", at=3)
    ledger.alert(rec.sandbox, 4)
    ledger.alert(rec.sandbox, 5, close=True)
    real, calls = creates.sync, [0]

    class Crash(BaseException):
        pass

    def sync(path: Path) -> None:
        calls[0] += 1
        if calls[0] == point and not after_sync:
            raise Crash
        real(path)
        if calls[0] == point:
            raise Crash

    monkeypatch.setattr(creates, "sync", sync)
    with pytest.raises(Crash):
        rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(creates, "sync", real)
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    assert not book(rig).names() and not rig.backend.reapers
    assert not list(ledger.alerts.iterdir())
    # The unchanged ended record must never migrate again after final cleanup.
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    assert not book(rig).names()


def test_cleanup_unlink_and_directory_sync_order(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = seed(rig)
    ledger = book(rig)
    ledger.put(rec.sandbox, "settled", how="operator", at=1)
    ledger.put(rec.sandbox, "gone", by="watcher", at=2)
    ledger.observe(rec.sandbox, "sweep", 3)
    ledger.alert(rec.sandbox, 4)
    ledger.alert(rec.sandbox, 5, close=True)
    events: list[tuple[str, str]] = []
    unlink, sync = Path.unlink, creates.sync

    def remove(path: Path, missing_ok: bool = False) -> None:
        events.append(("unlink", path.name))
        unlink(path, missing_ok=missing_ok)

    def flush(path: Path) -> None:
        events.append(("sync", path.name))
        sync(path)

    monkeypatch.setattr(Path, "unlink", remove)
    monkeypatch.setattr(creates, "sync", flush)
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    expected = [(f"create-{rec.sandbox}.json", "alerts"),
                (f"create-{rec.sandbox}-settled.json", "alerts"),
                *((f"{rec.sandbox}{suffix}.json", "creates")
                  for suffix in ("", ".observed", ".gone", ".settled"))]
    assert events == [event for name, directory in expected
                      for event in (("unlink", name), ("sync", directory))]


def test_orphan_returned_settlement_requires_confirmed_delete_before_cleanup(rig: RuntimeRig) -> None:
    rec = seed(rig)
    ledger = book(rig)
    ledger.put(rec.sandbox, "settled", how="returned", at=1)
    (ledger.directory / f"{rec.sandbox}.json").unlink()
    rig.backend.delete_unconfirmed = 1
    with pytest.raises(RuntimeUnavailable):
        rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    assert creates.settled(ledger.directory, rec.sandbox)
    assert not creates.gone(ledger.directory, rec.sandbox) and not rig.backend.reapers
    rig.runtime._restore_creates(ws="alpha")  # pyright: ignore[reportPrivateUsage]
    assert not ledger.names() and not rig.backend.reapers


def test_unreadable_ledger_never_means_empty(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(creates.Ledger, "names", lambda _: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(RuntimeUnavailable):
        rig.runtime.sessions("alpha")
    with pytest.raises(RuntimeUnavailable):
        rig.runtime._running(short_id("old-session"))  # pyright: ignore[reportPrivateUsage]


def test_submission_and_return_evidence_surround_create_record_writes(rig: RuntimeRig,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from heterodyne.sandbox import runtime

    events: list[str] = []
    real_write, real_phase, real_arm = creates.write_once, runtime.write_record, rig.runtime._arm

    def evidence(path: Path, body: bytes) -> bool:
        if path.parent.name == "creates" and not path.name.startswith("."):
            events.append("returned" if path.name.endswith(".settled.json") else "submission")
        return real_write(path, body)

    def phase(layout: Any, rec: SessionRecord) -> None:
        if rec.create_started and rec.phase == Phase.CREATING:
            events.append("created" if rec.created else "create_started")
        real_phase(layout, rec)

    def arm(entry: dict[str, Any]) -> None:
        real_arm(entry)
        events.append("armed")

    monkeypatch.setattr(creates, "write_once", evidence)
    monkeypatch.setattr(runtime, "write_record", phase)
    monkeypatch.setattr(rig.runtime, "_arm", arm)
    rig.runtime.launch(launch_spec(rig, "p-one"))
    assert events[:5] == ["submission", "create_started", "armed", "returned", "created"]
    # The host-only ledger is outside every sandbox bind, including parent bind directories.
    [box] = rig.backend.boxes.values()
    ledger = book(rig).directory
    for source in box.paths.values():
        assert not ledger.is_relative_to(Path(source))


def test_failed_submission_never_submits_backend_create(rig: RuntimeRig,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    real = creates.write_once
    monkeypatch.setattr(creates, "write_once", lambda path, body: False if
                        path.parent.name == "creates" else real(path, body))
    with pytest.raises(LaunchFailed, match="submission"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert not rig.backend.created and not rig.backend.pending


def test_failed_returned_write_still_records_created_then_r2_repairs(rig: RuntimeRig,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    real = creates.write_once
    monkeypatch.setattr(creates, "write_once", lambda path, body: False if
                        path.name.endswith(".settled.json") else real(path, body))
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert rec.created and not creates.settled(book(rig).directory, rec.sandbox)
    monkeypatch.setattr(creates, "write_once", real)
    assert len(rig.runtime.sessions("alpha")) == 1
    evidence = creates.settlement(book(rig).directory, rec.sandbox)
    assert evidence is not None and evidence["how"] == "returned"


def test_deadline_never_submitted_evidence_precedes_flag_clear(rig: RuntimeRig,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from heterodyne.sandbox import runtime

    real_arm, real_write = rig.runtime._reaper, runtime.write_record
    events: list[str] = []

    def stalled(rec: SessionRecord) -> None:
        real_arm(rec)
        rig.clock.advance(rec.deadline - rig.clock() + 1)

    def write(layout: Any, rec: SessionRecord) -> None:
        if rec.deadline and not rec.create_started:
            evidence = creates.settlement(book(rig).directory, rec.sandbox)
            assert evidence is not None and evidence["how"] == "never_submitted"
            events.append("clear")
        real_write(layout, rec)

    monkeypatch.setattr(rig.runtime, "_reaper", stalled)
    monkeypatch.setattr(runtime, "write_record", write)
    with pytest.raises(LaunchFailed, match="stalled"):
        rig.runtime.launch(launch_spec(rig, "p-one"))
    assert events and not rig.backend.created


def test_gone_write_failure_keeps_teardown_watcher(rig: RuntimeRig, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = launch_spec(rig, "p-one")
    rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    real = creates.write_once
    monkeypatch.setattr(creates, "write_once", lambda path, body: False if
                        path.name.endswith(".gone.json") else real(path, body))
    rig.runtime.stop(rec.key)
    assert record(rig, rec.key).phase == Phase.ENDED
    assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))
    assert not creates.gone(book(rig).directory, rec.sandbox)


def test_teardown_observes_each_listed_generation_before_delete(rig: RuntimeRig,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    rec = seed(rig)
    other = f"{short_id(rec.key)}g2"
    rig.backend.extra.update({rec.sandbox, other})
    real, names = rig.backend.delete, []

    def delete(name: str) -> bool:
        assert creates.read(book(rig).directory / f"{name}.observed.json", name) is not None
        names.append(name)
        return real(name)

    monkeypatch.setattr(rig.backend, "delete", delete)
    assert rig.runtime._teardown(rec, interrupt=False)  # pyright: ignore[reportPrivateUsage]
    assert set(names) == {rec.sandbox, other}
    assert not creates.settled(book(rig).directory, rec.sandbox)


def test_crash_after_returned_before_created_keeps_settlement(rig: RuntimeRig,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from heterodyne.sandbox import runtime

    class Crash(BaseException):
        pass

    real_write = runtime.write_record

    def crash(layout: Any, rec: SessionRecord) -> None:
        if rec.created:
            assert creates.settled(book(rig).directory, rec.sandbox)
            raise Crash()
        real_write(layout, rec)

    spec = launch_spec(rig, "p-one")
    monkeypatch.setattr(runtime, "write_record", crash)
    with pytest.raises(Crash):
        rig.runtime.launch(spec)
    before = record(rig, spec.session_key)
    assert not before.created and before.create_started
    assert creates.settled(book(rig).directory, before.sandbox)
    monkeypatch.setattr(runtime, "write_record", real_write)
    assert rig.runtime.sessions("alpha") == []
    assert record(rig, before.key).phase == Phase.ENDED
    assert creates.gone(book(rig).directory, before.sandbox)


def test_failed_never_submitted_write_retains_unknown_flag(rig: RuntimeRig,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    real_arm, real_write = rig.runtime._reaper, creates.write_once

    def stalled(rec: SessionRecord) -> None:
        real_arm(rec)
        rig.clock.advance(rec.deadline - rig.clock() + 1)

    monkeypatch.setattr(rig.runtime, "_reaper", stalled)
    monkeypatch.setattr(creates, "write_once", lambda path, body: False if
                        path.name.endswith(".settled.json") else real_write(path, body))
    spec = launch_spec(rig, "p-one")
    with pytest.raises(LaunchFailed):
        rig.runtime.launch(spec)
    rec = record(rig, spec.session_key)
    assert rec.create_started and not rig.backend.created
    assert not creates.settled(book(rig).directory, rec.sandbox)
    assert rig.tmux.has_session(rig.runtime.reaper_name(rec.key, 1))


def test_live_watcher_without_ready_times_out_before_create(rig: RuntimeRig,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    waited = Clock()
    rig.runtime.c = replace(rig.runtime.c, wait_clock=waited, sleep=waited.advance)
    monkeypatch.setattr(rig.backend, "reaper_argv", lambda *_: ["/bin/sleep", "86400"])
    spec = launch_spec(rig, "p-one")
    start = waited()
    with pytest.raises(LaunchFailed, match="watcher"):
        rig.runtime.launch(spec)
    assert waited() >= start + 10
    assert not rig.backend.created and not rig.backend.pending
    assert not list(book(rig).directory.glob(".ready-*"))


def test_actual_resolve_refuses_outstanding_create_without_watcher(rig: RuntimeRig) -> None:
    rec = seed(rig)
    write_record(rig.runtime.layout(rec.key), msgspec.structs.replace(rec, unlanded=True))
    with pytest.raises(ResolveRefused, match="not settled and deleted"):
        rig.runtime.resolve_unlanded("alpha", short_id(rec.key), 1, "discard", None)
    assert rig.runtime.layout(rec.key).root.exists()
    assert book(rig).submission(rec.sandbox) is not None
