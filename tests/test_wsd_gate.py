import errno
import fcntl
import os
import threading
import types
from pathlib import Path
from typing import Any

import pytest
from fakes.checkpoints import Many, PauseAt, Recorder, Seen
from fakes.fake_btq import World, factory

from heterodyne.wsd import gate as gate_mod
from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.gate import AlreadyRunning, ClaimGate, Paused, instance_lock, open_lock_file

WS = "alpha"


def gate(tmp_path: Path, cp: Recorder | None = None) -> tuple[World, BeadsAdapter, ClaimGate]:
    world = World(tmp_path / "btq-state")
    beads = BeadsAdapter(factory(world))
    return world, beads, ClaimGate(tmp_path / "claims", beads, cp or Recorder())


def test_claim_checks_the_flag_then_claims(tmp_path: Path) -> None:
    cp = Recorder()
    world, _, g = gate(tmp_path, cp)
    world.add("btq-1")
    assert g.claim(WS, "btq-1").status == "in_progress"
    assert cp.seen == ["gate.checked"]


def test_claim_rechecks_flag_inside_lock(tmp_path: Path) -> None:
    world, beads, g = gate(tmp_path)
    world.add("btq-1")
    beads.set_paused(WS, True)          # a direct `btq pause`: no lock taken
    with pytest.raises(Paused):
        g.claim(WS, "btq-1")
    assert world.claims == []


def test_pause_and_resume_set_the_shared_flag(tmp_path: Path) -> None:
    _, beads, g = gate(tmp_path)
    g.pause(WS)
    assert (beads.ws_queue(WS).state / "paused").exists()
    g.resume(WS)
    assert not beads.paused(WS)


def test_pause_waits_for_the_claim_lock(tmp_path: Path) -> None:
    door = Seen("gate.pause.waiting")
    _, beads, g = gate(tmp_path, door)
    acked = threading.Event()
    errors: list[BaseException] = []

    def pause() -> None:
        try:
            g.pause(WS)
            acked.set()
        except BaseException as exc:  # noqa: BLE001 - reported by the test thread
            errors.append(exc)

    pauser = threading.Thread(target=pause)
    try:
        with g.locked(WS):                  # a claim in flight
            pauser.start()
            assert door.reached.wait(5)     # the pauser is at the lock's door
            assert not acked.wait(0.3)
            assert not beads.paused(WS)
        assert acked.wait(5)
    finally:
        if pauser.ident is not None:
            pauser.join(5)
    assert not pauser.is_alive()
    assert errors == []
    assert beads.paused(WS)


def test_pause_waits_for_an_in_flight_claim(tmp_path: Path) -> None:
    held = PauseAt("gate.checked")
    door = Seen("gate.pause.waiting")
    world, beads, g = gate(tmp_path, Many(held, door))
    world.add("btq-1")
    claimed: list[str] = []
    acked = threading.Event()
    errors: list[BaseException] = []

    def claim() -> None:
        try:
            claimed.append(g.claim(WS, "btq-1").status)
        except BaseException as exc:  # noqa: BLE001 - reported by the test thread
            errors.append(exc)

    def pause() -> None:
        try:
            g.pause(WS)
            acked.set()
        except BaseException as exc:  # noqa: BLE001 - reported by the test thread
            errors.append(exc)

    claimer = threading.Thread(target=claim)
    pauser = threading.Thread(target=pause)
    try:
        claimer.start()
        assert held.reached.wait(5)         # the flag was checked; the claim lock is held
        pauser.start()
        assert door.reached.wait(5)
        assert not acked.wait(0.3)
        assert not beads.paused(WS)
        held.go.set()
        assert acked.wait(5)
    finally:
        held.go.set()
        for t in (claimer, pauser):
            if t.ident is not None:
                t.join(5)
    assert not claimer.is_alive() and not pauser.is_alive()
    assert errors == []
    assert claimed == ["in_progress"] and world.claims == ["btq-1"]
    assert beads.paused(WS)
    with pytest.raises(Paused):
        g.claim(WS, "btq-1")


def test_claim_lock_file_is_private(tmp_path: Path) -> None:
    _, _, g = gate(tmp_path)
    with g.locked(WS):
        pass
    assert (tmp_path / "claims").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "claims" / f"{WS}.claim").stat().st_mode & 0o777 == 0o600


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    fd = instance_lock(tmp_path / "state" / "wsd.lock")
    with pytest.raises(AlreadyRunning):
        instance_lock(tmp_path / "state" / "wsd.lock")
    os.close(fd)
    os.close(instance_lock(tmp_path / "state" / "wsd.lock"))


def flock_spy(monkeypatch: pytest.MonkeyPatch, on_flock: Any) -> None:
    """Replace the gate module's `fcntl` with one whose `flock` calls `on_flock(fd, op)` first."""
    def flock(fd: int, op: int) -> None:
        on_flock(fd, op)
        fcntl.flock(fd, op)

    monkeypatch.setattr(gate_mod, "fcntl", types.SimpleNamespace(
        flock=flock, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))


def test_pause_set_while_a_claim_waits_for_the_lock_refuses_it(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    world, beads, g = gate(tmp_path)
    world.add("btq-1")
    claimer: threading.Thread | None = None
    at_door = threading.Event()

    def on_flock(_fd: int, _op: int) -> None:
        if threading.current_thread() is claimer:
            at_door.set()

    errors: list[BaseException] = []

    def claim() -> None:
        try:
            g.claim(WS, "btq-1")
        except BaseException as exc:  # noqa: BLE001 - reported by the test thread
            errors.append(exc)

    claimer = threading.Thread(target=claim)
    try:
        with g.locked(WS):                  # a pause holds the lock ...
            flock_spy(monkeypatch, on_flock)
            claimer.start()
            assert at_door.wait(5)          # ... while the claim waits for it
            beads.set_paused(WS, True)      # ... and sets the flag before the claim gets the lock
    finally:
        if claimer.ident is not None:
            claimer.join(5)
    assert not claimer.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], Paused)
    assert world.claims == [] and all(call != "claim" for _, call in world.calls)


def test_instance_lock_closes_its_descriptor_on_any_failure(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[int] = []

    def on_flock(fd: int, _op: int) -> None:
        opened.append(fd)
        raise OSError(errno.ENOLCK, "no locks available")

    flock_spy(monkeypatch, on_flock)
    with pytest.raises(OSError) as raised:
        instance_lock(tmp_path / "state" / "wsd.lock")
    assert raised.value.errno == errno.ENOLCK and not isinstance(raised.value, AlreadyRunning)
    assert len(opened) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(opened[0])
    assert closed.value.errno == errno.EBADF


def test_lock_files_are_narrowed_to_0600(tmp_path: Path) -> None:
    _, _, g = gate(tmp_path)
    (tmp_path / "claims").mkdir(mode=0o700)
    claim_file = tmp_path / "claims" / f"{WS}.claim"
    claim_file.touch()
    claim_file.chmod(0o666)
    with g.locked(WS):
        pass
    assert claim_file.stat().st_mode & 0o777 == 0o600
    (tmp_path / "state").mkdir(mode=0o700)
    lock = tmp_path / "state" / "wsd.lock"
    lock.touch()
    lock.chmod(0o644)
    os.close(instance_lock(lock))
    assert lock.stat().st_mode & 0o777 == 0o600


def test_lock_files_refuse_anything_but_our_regular_file(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, g = gate(tmp_path)
    (tmp_path / "claims").mkdir(mode=0o700)
    os.mkfifo(tmp_path / "claims" / f"{WS}.claim", 0o600)
    with pytest.raises(PermissionError, match="not a regular file"):
        with g.locked(WS):
            pass
    target = tmp_path / "elsewhere"
    target.touch()
    (tmp_path / "claims" / "beta.claim").symlink_to(target)
    with pytest.raises(OSError):
        with g.locked("beta"):
            pass
    mine = tmp_path / "mine.lock"
    mine.touch(mode=0o600)
    fds = Path("/proc/self/fd")         # Linux: the refusal must leave no descriptor open
    before = len(list(fds.iterdir())) if fds.is_dir() else None
    monkeypatch.setattr(gate_mod.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(PermissionError, match="not owned"):
        open_lock_file(mine)
    monkeypatch.undo()
    if before is not None:
        assert len(list(fds.iterdir())) == before
