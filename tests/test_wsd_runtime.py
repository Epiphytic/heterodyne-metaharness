from pathlib import Path

import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.runtime import HoldingReconciler, LaunchSpec, NoRuntime, RuntimeUnavailable


def test_no_runtime_is_unavailable_and_lists_nothing_as_ended() -> None:
    runtime = NoRuntime()
    assert not runtime.available()
    with pytest.raises(RuntimeUnavailable):
        runtime.sessions("alpha")       # never "no sessions": that would read as "none running"
    with pytest.raises(RuntimeUnavailable):
        runtime.launch(LaunchSpec("alpha", "btq-a", "coder", "p", "k", "l", Path("/nonexistent"), False))
    with pytest.raises(RuntimeUnavailable):
        runtime.stop("any")


def test_holding_reconciler_reports_every_unsettled_action(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    world.add("btq-a", metadata={"action_state": "executing"})
    world.add("btq-b", metadata={"action_state": "uncertain"})
    world.add("btq-c", metadata={"action_state": "succeeded"})
    world.add("btq-d", ws="beta", metadata={"action_state": "uncertain"})
    world.add("btq-e", status="closed", metadata={"action_state": "uncertain"})   # closed still counts
    world.add("btq-f", metadata={"action_state": "reticulating"})      # unknown: never settled
    world.add("btq-g", metadata={"action_state": "pending"})
    world.add("btq-h", status="closed", metadata={"action_state": "failed"})
    world.add("btq-i")
    assert HoldingReconciler(BeadsAdapter(factory(world))).unresolved("alpha") == ["btq-a", "btq-b", "btq-e",
                                                                                   "btq-f"]


def test_holding_reconciler_fails_closed(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue("alpha")
    world.down = True
    with pytest.raises(BeadsUnavailable):
        HoldingReconciler(adapter).unresolved("alpha")


def test_holding_reconciler_never_reads_a_non_string_as_settled(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    world.add("btq-a", metadata={"action_state": 0})
    world.add("btq-b", metadata={"action_state": None})
    world.add("btq-c", metadata={"action_state": {"state": "succeeded"}})
    world.add("btq-d", metadata={"action_state": ["failed"]})
    world.add("btq-e", metadata={"action_state": "Succeeded"})
    assert HoldingReconciler(BeadsAdapter(factory(world))).unresolved("alpha") == ["btq-a", "btq-b", "btq-c",
                                                                                   "btq-d", "btq-e"]


def test_fake_runtime_that_is_down_never_confirms_a_stop() -> None:
    runtime = FakeRuntime()
    spec = LaunchSpec("alpha", "btq-a", "coder", "p", "k", "l", Path("/nonexistent"), False)
    runtime.launch(spec)
    runtime.up = False
    with pytest.raises(RuntimeUnavailable):
        runtime.stop("k")
    assert runtime.stops == [] and "k" in runtime.listed
