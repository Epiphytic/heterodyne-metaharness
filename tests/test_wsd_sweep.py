"""AU-5 r3, rewritten for AU-4: the sweep keeps a completed guard deferral (AU-4 §7)."""

import os
from pathlib import Path

import pytest
from wsd_env import WS, At, Rig, block, make_rig, profile_key

from heterodyne.wsd.beads import DEFERRED, HELD, NEEDS_HUMAN, PARKED
from heterodyne.wsd.headroom import quota_detail
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.scheduler import Outcome, TriggerKind
from heterodyne.wsd.states import BeadState, Reason

HOUR = 3600


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def shelved(tmp_path: Path) -> tuple[Rig, int]:
    """btq-1 deferred by the guard: pickup's gate admitted it, then its key was blocked."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    until = rig.clock() + HOUR
    rig.restart(At("gate.checked", lambda: block(rig, profile_key(rig, "p-one"), HOUR)))
    assert rig.pickup() is Outcome.DEFERRED
    rig.restart()
    return rig, until


def row(rig: Rig) -> tuple[BeadState, Reason | None, str]:
    found = rig.journal.state(WS, "btq-1")
    assert found is not None
    return found.state, found.reason, found.detail


def test_another_pickup_keeps_the_deferred_row(tmp_path: Path) -> None:
    rig, until = shelved(tmp_path)
    for _ in range(2):
        assert rig.pickup() is Outcome.DEFERRED
        assert row(rig) == (BeadState.DEFERRED, Reason.QUOTA, quota_detail(until))
        assert rig.sched.wake_at == until
        assert DEFERRED in rig.world.beads["btq-1"].labels


def test_startup_recovery_keeps_the_deferred_row(tmp_path: Path) -> None:
    rig, until = shelved(tmp_path)
    rig.restart()                                   # a reopened journal and new wsd objects
    assert recover(rig.sched).ok
    assert rig.pickup(TriggerKind.STARTUP) is Outcome.DEFERRED
    assert row(rig) == (BeadState.DEFERRED, Reason.QUOTA, quota_detail(until))
    assert rig.sched.wake_at == until


def add_blocker(rig: Rig, operator: bool) -> None:
    rig.world.add("btq-3", labels=["kind:approval"] if operator else [])
    rig.world.beads["btq-3"].labels.remove("agent:wsd")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))


@pytest.mark.parametrize("added", ["needs_human", "held", "operator_blocker", "blocker"])
@pytest.mark.parametrize("via", ["pickup", "recovery"])
def test_a_stop_added_later_keeps_the_bead_deferred_without_a_wake(tmp_path: Path, added: str,
                                                                     via: str) -> None:
    """Only needing a human replaces the row; a hold or a blocker keeps it DEFERRED (AU-4 §7), and stops
    both its wake and its undefer."""
    rig, until = shelved(tmp_path)
    if added in ("needs_human", "held"):
        rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN if added == "needs_human" else HELD)
    else:
        add_blocker(rig, operator=added == "operator_blocker")
    if via == "recovery":
        assert recover(rig.sched).ok or added == "needs_human"
    outcome = rig.pickup()
    kept = (BeadState.DEFERRED, Reason.QUOTA, quota_detail(until))
    assert row(rig) == ((BeadState.STUCK, Reason.NEEDS_HUMAN, "") if added == "needs_human" else kept)
    assert rig.sched.wake_at is None and outcome is not Outcome.DEFERRED
    rig.clock.advance(HOUR)
    rig.pickup()
    assert rig.runtime.launches == []


def test_once_a_blocker_closes_the_bead_undefers_gated_and_may_be_deferred_again(tmp_path: Path) -> None:
    rig, until = shelved(tmp_path)
    add_blocker(rig, operator=False)
    rig.pickup()
    assert row(rig) == (BeadState.DEFERRED, Reason.QUOTA, quota_detail(until))
    rig.world.close("btq-3")
    assert rig.pickup() is Outcome.DEFERRED                     # not over until `until`
    assert rig.sched.wake_at == until and rig.runtime.launches == []
    rig.clock.advance(HOUR)
    later = rig.clock() + HOUR
    rig.restart(At("undefer.intent", lambda: block(rig, profile_key(rig, "p-one"), HOUR)))
    assert rig.pickup() is Outcome.DEFERRED                     # the undefer's gate defers it again
    assert row(rig) == (BeadState.DEFERRED, Reason.QUOTA, quota_detail(later))
    labels = rig.world.beads["btq-1"].labels
    assert DEFERRED in labels and PARKED not in labels and rig.runtime.launches == []
    rig.clock.advance(HOUR)
    assert rig.pickup() is Outcome.RESUMED and len(rig.runtime.launches) == 1
    assert DEFERRED not in rig.world.beads["btq-1"].labels
