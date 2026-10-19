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


def test_missing_or_mismatched_counter_holds_only_companion(tmp_path):
    binding = Binding("install", "generation", "a" * 64)
    calls = []
    def native():
        calls.append("native")
        return "succeeded"

    def companion(*_):
        calls.append("companion")
    assert paired_attempt(tmp_path, binding, native, companion) == (None, "succeeded")
    assert not (tmp_path / "native-backup-cycle.state").exists()
    initialize(tmp_path, binding)
    before = (tmp_path / "native-backup-cycle.state").read_bytes()
    assert paired_attempt(tmp_path, Binding("other", "generation", "a" * 64),
                          native, companion) == (None, "succeeded")
    assert (tmp_path / "native-backup-cycle.state").read_bytes() == before
    assert calls == ["native", "native"]
