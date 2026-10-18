import pytest

from heterodyne.wsd.backup_pairing import Binding, initialize, paired_attempt


def test_exact_native_attempt_then_companion_never_fakes_success(tmp_path):
    calls = []
    binding = Binding("install", "generation", "a" * 64)
    initialize(tmp_path, binding)

    def native():
        calls.append("native")
        return "failed"

    def companion(cycle, status):
        calls.append((cycle, status))
        raise RuntimeError("companion down")

    assert paired_attempt(tmp_path, binding, native, companion) == (1, "failed")
    assert paired_attempt(tmp_path, binding, native, companion) == (2, "failed")
    assert calls == ["native", (1, "failed"), "native", (2, "failed")]


def test_missing_counter_never_restarts_native_namespace(tmp_path):
    binding = Binding("install", "generation", "a" * 64)
    calls = []
    with pytest.raises(FileNotFoundError):
        paired_attempt(tmp_path, binding, lambda: calls.append(1), lambda *_: None)
    initialize(tmp_path, binding)
    with pytest.raises(ValueError, match="identity"):
        paired_attempt(
            tmp_path, Binding("other", "generation", "a" * 64), lambda: calls.append(1), lambda *_: None
        )
    assert not calls
