import msgspec
import pytest

from heterodyne.wsd.health import HealthSnapshot, encode, predicates, publish, read


def snapshot(**changes):
    result = HealthSnapshot(
        1,
        "install",
        "generation",
        "process",
        "boot",
        1000,
        100,
        1,
        "systemd",
        20,
        300,
        "complete",
        0,
        True,
        80,
        None,
        None,
        "unready",
        {},
    )
    return msgspec.structs.replace(result, **changes)


def test_private_bounded_health_and_identity(tmp_path):
    path = tmp_path / "health.json"
    publish(path, snapshot())
    assert read(path, "install", "generation") == snapshot()
    with pytest.raises(ValueError, match="identity"):
        read(path, "other", "generation")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        read(path, "install", "generation")
    payload = encode(snapshot(lanes={str(i): {} for i in range(129)}))
    compact = msgspec.json.decode(payload, type=HealthSnapshot)
    assert compact.snapshot_error and not compact.lanes


def test_independent_unknown_stale_and_recovery_predicates():
    assert predicates(None, utc=1000, monotonic=100)["recovery_failed"] == "unknown"
    values = predicates(snapshot(), utc=1000, monotonic=100)
    assert values["stale"] == "false"
    assert values["effect_hold"] == "true"
    assert values["wake_overdue"] == "false"
    assert predicates(snapshot(), utc=1100, monotonic=100)["stale"] == "true"
    assert predicates(snapshot(), utc=900, monotonic=100)["unknown"] == "true"
    assert predicates(snapshot(recovery="in_progress"), utc=1000, monotonic=100)["wake_overdue"] == "unknown"
    assert (
        predicates(
            snapshot(), utc=1000, monotonic=100, prior_process="process", prior_sequence=1, sequence_seen_at=0
        )["stale"]
        == "true"
    )
