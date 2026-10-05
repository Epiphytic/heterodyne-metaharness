import os
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import Many, PauseAt, Recorder, Seen
from fakes.fake_btq import World, factory

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.gate import AlreadyRunning, ClaimGate, Paused, instance_lock

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
