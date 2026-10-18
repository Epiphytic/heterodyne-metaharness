import json

from test_plan8_health import snapshot

from heterodyne.wsd.health import publish
from heterodyne.wsd.observer import Observer


def test_observer_unknown_does_not_clear_and_reopen_uses_distinct_sequence(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    observer = Observer(state, tmp_path / "alerts", "install", "generation")
    values = observer.inspect(1000, 100, "boot")
    assert values["unavailable"] == "true"
    first = json.loads((state / "observer-state.json").read_text())
    epoch = first["epoch"]
    unavailable = first["episodes"]["unavailable"]
    assert unavailable["sequence"] == 1 and unavailable["phase"] == "open"
    # A future-clock snapshot makes downstream conditions unknown, never recovered.
    publish(state / "health.json", snapshot(utc=2000))
    observer.inspect(1000, 101, "boot")
    publish(state / "health.json", snapshot(effect_state="ready"))
    observer.inspect(1000, 102, "boot")
    closed = json.loads((state / "observer-state.json").read_text())
    assert closed["episodes"]["unavailable"]["phase"] == "closed"
    (state / "health.json").unlink()
    observer.inspect(1001, 103, "boot")
    reopened = json.loads((state / "observer-state.json").read_text())
    assert reopened["epoch"] == epoch
    assert reopened["episodes"]["unavailable"]["sequence"] == 2
    assert (
        reopened["episodes"]["unavailable"]["failure"]["episode_id"] != unavailable["failure"]["episode_id"]
    )


def test_corrupt_observer_preserves_bytes_and_reset_is_idempotent(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "observer-state.json").write_bytes(b"broken")
    observer = Observer(state, tmp_path / "alerts", "install", "generation")
    observer.inspect(1000, 100, "boot")
    first = json.loads((state / "observer-state.json").read_text())
    assert list(state.glob("observer-state.corrupt-*"))[0].read_bytes() == b"broken"
    observer.inspect(1001, 101, "boot")
    assert json.loads((state / "observer-state.json").read_text())["epoch"] == first["epoch"]


def test_capacity_coalescing_retains_exact_pending_intent_until_space_returns(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    alerts = tmp_path / "alerts"
    alerts.mkdir()
    for number in range(255):
        (alerts / f"foreign{number}.json").write_text("{}")
    observer = Observer(state, alerts, "install", "generation")
    observer.inspect(1000, 100, "boot")
    pending = json.loads((state / "observer-state.json").read_text())
    notice = pending["episodes"]["unavailable"]["notice"]
    assert notice is not None
    assert pending["reset"] is not None
    for number in range(255):
        (alerts / f"foreign{number}.json").unlink()
    observer.inspect(1001, 101, "boot")
    published = json.loads((state / "observer-state.json").read_text())
    assert published["reset"] is None
    assert published["episodes"]["unavailable"]["notice"] is None
    assert published["episodes"]["unavailable"]["failure"] == notice
