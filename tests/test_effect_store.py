import sqlite3

from heterodyne.wsd.effect_slots import Sample
from heterodyne.wsd.effects import EffectStore, ProducerResult


def ready(tmp_path):
    path = tmp_path / "effects.db"
    EffectStore.initialize(path, "install", "generation")
    store = EffectStore(path, "install", "generation")
    assert not store.observe(Sample("boot", 100, 0, True))
    assert store.observe(Sample("boot", 130, 30, True))
    return store, path


def test_missing_is_not_created_and_wrong_identity_holds(tmp_path):
    path = tmp_path / "effects.db"
    store = EffectStore(path, "install", "generation")
    assert store.cache.state == "error"
    assert not path.exists()
    EffectStore.initialize(path, "other", "generation")
    store = EffectStore(path, "install", "generation")
    assert store.cache.state == "error"


def test_dispatch_committed_before_external_call_and_restart_uncertain(tmp_path):
    store, path = ready(tmp_path)
    assert store.prepare("backup", 4, 130)
    observed = []

    def producer(slot, deadline):
        with sqlite3.connect(path) as db:
            observed.append(db.execute("SELECT state FROM jobs").fetchone()[0])
        raise RuntimeError("external ambiguity")

    assert store.dispatch("backup", producer, 130, 5)
    assert observed == ["dispatched"]
    assert store.cache.jobs == (("backup", "uncertain"),)
    assert not store.prepare("backup", 5, 140)
    store.close()
    store = EffectStore(path, "install", "generation")
    assert store.cache.jobs == (("backup", "uncertain"),)
    store.close()


def test_only_proven_unsent_can_retry_and_terminal_highwater_is_atomic(tmp_path):
    store, path = ready(tmp_path)
    try:
        assert store.prepare("job", 3, 130)
        assert store.dispatch("job", lambda *_: ProducerResult("definite_unsent", True), 130, 1)
        assert store.dispatch("job", lambda *_: ProducerResult("succeeded"), 131, 2)
        assert not store.prepare("job", 3, 132)
        assert store.prepare("job", 8, 133)
        assert store.dispatch("job", lambda *_: ProducerResult("definite_unsent"), 133, 2)
        assert not store.dispatch("job", lambda *_: ProducerResult("succeeded"), 134, 3)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT high_water,slot,state,skipped FROM jobs").fetchone() == (
                3,
                8,
                "uncertain",
                7,
            )
    finally:
        store.close()


def test_bounded_pass_leaves_other_jobs_runnable(tmp_path):
    store, _ = ready(tmp_path)
    try:
        for i in range(8):
            assert store.prepare(f"job{i}", i, 130)
        assert not store.prepare("ninth", 0, 130)
        calls = []

        def producer(slot, deadline):
            calls.append((slot, deadline))
            return ProducerResult("held" if slot == 0 else "succeeded")

        assert store.pass_once({f"job{i}": producer for i in range(8)}, lambda: 10, 130) == 8
        assert len(calls) == 8
        assert all(deadline == 15 for _, deadline in calls)
        assert store.cache.jobs[0] == ("job0", "held")
    finally:
        store.close()


def test_schema_extension_is_not_silently_accepted(tmp_path):
    store, path = ready(tmp_path)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE jobs ADD COLUMN unexpected TEXT")
    store = EffectStore(path, "install", "generation")
    assert store.cache.state == "error"


def test_backup_cycle_highwater_and_unresolved_recovery(tmp_path):
    store, path = ready(tmp_path)
    assert store.begin_backup(4, "unknown") == "queued"
    assert store.begin_backup(4, "unknown") == "joined"
    assert store.begin_backup(5, "succeeded") == "unresolved"
    store.finish_backup(4, "partial", "attempt", "a" * 64)
    assert store.begin_backup(3, "succeeded") == "completed"
    assert store.begin_backup(5, "succeeded") == "queued"
    store.close()
    store = EffectStore(path, "install", "generation")
    try:
        assert store.begin_backup(6, "succeeded") == "unresolved"
    finally:
        store.close()
