"""Offline fault injection only; synthetic source ports cannot qualify a real release."""

from pathlib import Path

import pytest
from acceptance_fixtures import A, SyntheticProofs, at, entry, event, replace, rows, timeline

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import Hold
from heterodyne.acceptance.ledger import Ledger


def prepared(tmp_path: Path, *, proofs=True):
    raw, events, inventory = timeline(seconds=600)
    ledger = Ledger.create(tmp_path / "ledger", raw, SyntheticProofs() if proofs else None)
    ledger.append(events[0])
    for name, value in inventory.items():
        ledger.retain(m.Artifact(id=name, sha256=codec.digest(value)), value)
    ledger.begin("segment")
    ledger.heartbeat(events[1])
    cp = codec.decode(events[1], m.Event).checkpoint
    assert cp is not None
    intent = m.SealIntent(
        transaction_id="tx",
        segment_id="segment",
        entry_sha256=codec.digest(raw),
        start_event_sha256=codec.digest(events[0]),
        checkpoint=cp,
        checkpoint_event_sha256=codec.digest(events[1]),
        evaluation=A,
        reason="human_repair_required",
        result="interrupted",
    )
    stop = replace(
        event(2, codec.digest(events[1]), 600, "segment_stop"),
        stop=m.Stop(
            reason=intent.reason,
            checkpoint=cp,
            checkpoint_event_sha256=intent.checkpoint_event_sha256,
            transaction_id="tx",
        ),
    )
    stopraw = codec.canonical(stop)
    segment = m.Segment(
        format=1,
        run_id="synthetic",
        segment_id="segment",
        entry_sha256=codec.digest(raw),
        start_event_sha256=codec.digest(events[0]),
        end_event_sha256=codec.digest(stopraw),
        start=at(),
        end=at(600),
        duration_ns="600000000000",
        status="interrupted",
        reason="human_repair_required",
        evidence=(),
    )
    assessment = m.Assessment(
        format=1,
        run_id="synthetic",
        at=at(600),
        segment_sha256=codec.digest(codec.canonical(segment)),
        coverage=rows(),
        final_inventory=A,
        supplemental_corpus=(),
        evaluation=A,
        archive_receipt=None,
        current_release=None,
        unresolved=("synthetic",),
        result="not_qualified",
        evidence=(),
    )
    return ledger, intent, segment, assessment, (stopraw,)


def test_terminal_crash_after_manifest_recovers_exact_bundle_once(tmp_path, monkeypatch):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    with monkeypatch.context() as patch:

        def crash(*args):
            raise OSError("synthetic crash after commit point")

        patch.setattr(ledger, "_complete", crash)
        with pytest.raises(OSError):
            ledger.commit(intent, segment, assessment, stops)
    assert len(ledger.events()) == 2
    reopened = Ledger(ledger.root, SyntheticProofs())
    assert reopened.recover() == "committed_complete_fenced"
    assert reopened.events()[-1] == stops[0]
    assert reopened.recover() == "committed_complete_fenced"
    assert len(reopened.events()) == 3
    with pytest.raises(Hold, match="committed_cannot_abort"):
        reopened.abort("synthetic")


def test_crash_before_manifest_never_infers_commit(tmp_path, monkeypatch):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    original = Ledger._atomic

    def crash(path, raw):
        if path.name.startswith("terminal-"):
            raise OSError("synthetic precommit crash")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, "_atomic", staticmethod(crash))
        with pytest.raises(OSError):
            ledger.commit(intent, segment, assessment, stops)
    assert Ledger(ledger.root).recover() == "prepared_fence"
    assert len(ledger.events()) == 2
    ledger.commit(intent, segment, assessment, stops)
    assert len(ledger.events()) == 3


def test_fence_is_durable_before_heartbeat_and_recovers(tmp_path, monkeypatch):
    raw, events, _ = timeline(seconds=600)
    ledger = Ledger.create(tmp_path / "ledger", raw)
    ledger.append(events[0])
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_append", lambda raw: (_ for _ in ()).throw(OSError("crash")))
        with pytest.raises(OSError):
            ledger.begin("segment")
            ledger.heartbeat(events[1])
    assert (ledger.root / "fence.json").exists()
    assert len(ledger.events()) == 1
    assert Ledger(ledger.root).recover() == "prepared_fence"
    assert ledger.events()[-1] == events[1]


def test_unknown_tail_refuses_recovery(tmp_path):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    ledger.commit(intent, segment, assessment, stops)
    Ledger._atomic(ledger.root / "events" / "9999999999.json", stops[0])
    with pytest.raises(Hold, match="unexpected_event_tail"):
        ledger.recover()


def test_default_adapter_cannot_commit_abort_or_release(tmp_path):
    ledger, intent, segment, assessment, stops = prepared(tmp_path, proofs=False)
    with pytest.raises(Hold, match="source_adapter_unavailable"):
        ledger.commit(intent, segment, assessment, stops)
    with pytest.raises(Hold, match="source_adapter_unavailable"):
        ledger.abort("synthetic")
    with pytest.raises(Hold, match="fence_not_terminal"):
        ledger.release(())
    assert len(ledger.events()) == 2


def test_abort_and_release_retain_all_original_evidence(tmp_path):
    ledger, _, _, _, _ = prepared(tmp_path)
    raw = b'{"original":"delayed source envelope"}'
    spool = ledger.spool(raw)
    ledger.abort("synthetic")
    with pytest.raises(Hold, match="terminal_fenced"):
        ledger.append(b"{}")
    ledger.release(())  # Synthetic test port explicitly attests cursor drain; never production authority.
    assert (ledger.root / "spool" / spool).read_bytes() == raw
    assert not (ledger.root / "fence.json").exists()
    assert len(list(ledger.root.glob("abort-*"))) == 1
    assert len(list(ledger.root.glob("released-*"))) == 1


def test_spool_bound_holds_without_deleting_or_duplicate_count(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_SPOOL", 1)
    first = ledger.spool(b'{"x":1}')
    assert ledger.spool(b'{"x":1}') == first
    with pytest.raises(Hold, match="spool_overflow"):
        ledger.spool(b'{"x":2}')
    assert len(list((ledger.root / "spool").iterdir())) == 1


def test_object_capacity_allows_exact_idempotent_readback(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_OBJECTS", 1)
    assert ledger._object(b"synthetic fixture only") == A.sha256
    with pytest.raises(Hold, match="object_capacity"):
        ledger._object(b"new")


def test_private_permissions_and_symlink_refusal(tmp_path):
    raw, _, _ = timeline(seconds=600)
    root = tmp_path / "ledger"
    ledger = Ledger.create(root, raw)
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "entry.json").stat().st_mode & 0o777 == 0o600
    (root / "publisher.lock").unlink()
    (root / "publisher.lock").symlink_to(root / "entry.json")
    with pytest.raises(OSError):
        ledger.events()


def test_aborted_fence_cannot_later_commit(tmp_path):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    ledger.abort("synthetic", intent)
    with pytest.raises(Hold, match="aborted_fence"):
        ledger.commit(intent, segment, assessment, stops)


def test_begin_before_capture_is_recoverable_and_blocks_append(tmp_path):
    raw, events, _ = timeline(seconds=600)
    ledger = Ledger.create(tmp_path / "ledger", raw)
    ledger.append(events[0])
    ledger.begin("segment")
    assert Ledger(ledger.root).recover() == "prepared_fence"
    with pytest.raises(Hold, match="terminal_fenced"):
        ledger.append(events[1])
    ledger.heartbeat(events[1])
    assert ledger.events()[-1] == events[1]


@pytest.mark.parametrize("field,value", [("end", at(601)), ("duration_ns", "601000000000")])
def test_terminal_endpoint_cannot_use_later_publication_clock(tmp_path, field, value):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    changed = replace(segment, **{field: value})
    assessment = replace(assessment, segment_sha256=codec.digest(codec.canonical(changed)))
    with pytest.raises(Hold, match="terminal_endpoint"):
        ledger.commit(intent, changed, assessment, stops)


def test_source_inventory_names_are_not_paths_and_are_immutable(tmp_path):
    ledger, *_ = prepared(tmp_path)
    artifact = m.Artifact(id="..", sha256=codec.digest(b"private original"))
    ledger.retain(artifact, b"private original")
    assert ledger._inventory().read(artifact) == b"private original"
    with pytest.raises(Hold, match="immutable_file_conflict"):
        ledger.retain(m.Artifact(id="..", sha256=codec.digest(b"different")), b"different")


def test_source_spool_does_not_wait_for_terminal_publisher_lock(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    ledger = Ledger.create(tmp_path / "ledger", codec.canonical(entry()), SyntheticProofs())
    with ThreadPoolExecutor(max_workers=1) as executor:
        with ledger._lock():
            future = executor.submit(ledger.spool, b'{"source":"independent"}')
            try:
                digest = future.result(timeout=0.5)
            except TimeoutError:
                blocked = True
            else:
                blocked = False
                assert digest == codec.digest(b'{"source":"independent"}')
        future.result(timeout=2)
    assert not blocked, "independent source spool waited for the terminal publisher"
