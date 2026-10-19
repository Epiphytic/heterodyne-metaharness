import msgspec
import pytest

from heterodyne.wsd.maintenance_window import Window, label, validate
from heterodyne.wsd.observer import Observer


def test_window_duration_binding_and_unknown_clock():
    window = Window("marmot:decision", "a" * 64, "install", "generation", 1000, 2000)
    validate(window, "install", "generation")
    assert label(window, 1500) == "expected"
    assert label(window, 2100) == "inactive"
    assert label(window, float("nan")) == "unknown"
    with pytest.raises(ValueError):
        validate(msgspec.structs.replace(window, end_utc=3000), "install", "generation")


def test_restart_cannot_renew_window_and_alerts_not_suppressed(tmp_path):
    window = Window("marmot:decision", "a" * 64, "install", "generation", 1000, 2000)
    observer = Observer(tmp_path / "state", tmp_path / "alerts", "install", "generation", window)
    assert observer.inspect(1500, 100, "boot")["unavailable"] == "true"
    state = observer._load(1500)
    assert state.maintenance_label == "expected"
    renewed = msgspec.structs.replace(window, start_utc=1900, end_utc=2800)
    observer = Observer(tmp_path / "state", tmp_path / "alerts", "install", "generation", renewed)
    assert observer.inspect(2200, 200, "boot")["unavailable"] == "true"
    state = observer._load(2200)
    assert state.maintenance_label == "unknown"
    assert state.window_history[window.authority_id] == window
