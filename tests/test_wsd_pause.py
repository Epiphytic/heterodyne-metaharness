"""Pause at the pickup level (ADR 0001 §4.3): it stops new claims only, and an acknowledged pause is
never followed by a claim. Interleavings are forced with checkpoints."""

import fcntl
import os
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import PauseAt
from wsd_env import WS, Worker, finish, make_rig

from heterodyne.wsd import gate
from heterodyne.wsd.scheduler import Outcome


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rig's queue is in memory; nothing here may see the operator's btq, beads or bd settings."""
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def test_pause_stops_claims(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == []
    rig.gate.resume(WS)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"]


def test_direct_btq_pause_after_listing_is_honoured(tmp_path: Path) -> None:
    cp = PauseAt("pickup.intent")           # ready() has listed btq-1; the claim has not started
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    result: list[Outcome] = []
    worker = Worker(lambda: result.append(rig.pickup()), "pickup")
    try:
        worker.start()
        assert cp.reached.wait(10)
        rig.beads.set_paused(WS, True)
        cp.go.set()
    finally:
        cp.go.set()
        finish(worker)
    assert result == [Outcome.NOTHING]
    assert rig.world.claims == []
    assert rig.state("btq-1") == "dropped"


def test_pause_waits_for_in_flight_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The claim has passed the flag check when the operator pauses. The pause is not acknowledged until
    that claim is done, and after the acknowledgement no claim starts.

    Codex r1 finding 6: the pauser's lock request is instrumented. Before its blocking flock, the pauser's
    thread probes the same lock without blocking and records the answer; the test waits for that probe, so
    it knows the pauser has asked for the lock (and been refused) before checking that nothing was
    acknowledged or written."""
    cp = PauseAt("gate.checked")
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    real = gate.fcntl.flock
    probes: list[str] = []
    asked = threading.Event()

    def flock(fd: int, op: int) -> None:
        if threading.current_thread().name == "pause" and op == fcntl.LOCK_EX and not asked.is_set():
            try:
                real(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                probes.append("acquired")
            except BlockingIOError:
                probes.append("blocked")
            asked.set()
            if probes[-1] == "acquired":
                return
        real(fd, op)

    monkeypatch.setattr(gate.fcntl, "flock", flock)
    outcome: list[Outcome] = []
    acked = threading.Event()
    picker = Worker(lambda: outcome.append(rig.pickup()), "pickup")
    pauser = Worker(lambda: (rig.gate.pause(WS), acked.set()), "pause")
    try:
        picker.start()
        assert cp.reached.wait(10)
        pauser.start()
        assert asked.wait(10)               # the pauser asked for the claim lock, which the claim holds
        assert probes == ["blocked"]
        assert not acked.is_set() and not rig.beads.paused(WS)
        cp.go.set()
    finally:
        cp.go.set()
        finish(picker, pauser)
    assert acked.is_set() and rig.beads.paused(WS)
    assert outcome == [Outcome.STARTED]
    assert rig.world.claims == ["btq-1"]
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == ["btq-1"]


def test_pause_does_not_stop_the_running_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.stops == []


def test_pause_does_not_stop_a_resume(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.gate.pause(WS)
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED          # pausing stops new claims only
