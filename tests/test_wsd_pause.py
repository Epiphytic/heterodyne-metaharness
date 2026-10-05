"""Pause at the pickup level (ADR 0001 §4.3): it stops new claims only, and an acknowledged pause is
never followed by a claim. Interleavings are forced with checkpoints."""

import os
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import Many, PauseAt, Seen
from wsd_env import WS, Worker, finish, make_rig

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


def test_pause_waits_for_in_flight_claim(tmp_path: Path) -> None:
    """The claim has passed the flag check when the operator pauses. The pause is not acknowledged until
    that claim is done, and after the acknowledgement no claim starts."""
    cp = PauseAt("gate.checked")
    door = Seen("gate.pause.waiting")
    rig = make_rig(tmp_path, cp=Many(cp, door))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    outcome: list[Outcome] = []
    acked = threading.Event()
    picker = Worker(lambda: outcome.append(rig.pickup()), "pickup")
    pauser = Worker(lambda: (rig.gate.pause(WS), acked.set()), "pause")
    try:
        picker.start()
        assert cp.reached.wait(10)
        pauser.start()
        assert door.reached.wait(10)        # the pauser is at the claim lock's door, which the claim holds
        assert not acked.is_set()
        cp.go.set()
    finally:
        cp.go.set()
        finish(picker, pauser)
    assert acked.is_set()
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
