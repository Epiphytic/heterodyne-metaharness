"""First-HIGH ledger regressions; synthetic proof ports are not live authority."""

import concurrent.futures
import json
from pathlib import Path

import pytest
from acceptance_fixtures import A, SyntheticProofs, at, event, replace, rows, timeline
from test_acceptance_ledger import prepared

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import Hold
from heterodyne.acceptance.ledger import Ledger


def sealed(tmp_path: Path, *, period: int = 600, coverage=True):
    raw, events, inventory = timeline(period=period)
    ledger = Ledger.create(tmp_path / "ledger", raw, SyntheticProofs())
    # Fixture installation of the complete immutable chain; begin validates it.
    for i, item in enumerate(events[:-1]):
        Ledger._atomic(ledger.root / "events" / f"{i:010d}.json", item)
    for name, value in inventory.items():
        ledger.retain(m.Artifact(id=name, sha256=codec.digest(value)), value)
    ledger.begin("segment")
    ledger.heartbeat(events[-1])
    endpoint = codec.decode(events[-1], m.Event)
    assert endpoint.checkpoint is not None
    intent = m.SealIntent(
        transaction_id="seal",
        segment_id="segment",
        entry_sha256=codec.digest(raw),
        start_event_sha256=codec.digest(events[0]),
        checkpoint=endpoint.checkpoint,
        checkpoint_event_sha256=codec.digest(events[-1]),
        evaluation=A,
        reason="none",
        result="sealed",
    )
    stop = replace(
        event(len(events), codec.digest(events[-1]), 259200, "segment_stop"),
        stop=m.Stop(
            reason="none",
            checkpoint=intent.checkpoint,
            checkpoint_event_sha256=intent.checkpoint_event_sha256,
            transaction_id="seal",
        ),
    )
    stopraw = codec.canonical(stop)
    qualification = codec.canonical(event(len(events) + 1, codec.digest(stopraw), 259200, "qualification"))
    segment = m.Segment(
        format=1,
        run_id="synthetic",
        segment_id="segment",
        entry_sha256=codec.digest(raw),
        start_event_sha256=codec.digest(events[0]),
        end_event_sha256=codec.digest(stopraw),
        start=at(),
        end=at(259200),
        duration_ns="259200000000000",
        status="sealed",
        reason="none",
        evidence=(),
    )
    assessment = m.Assessment(
        format=1,
        run_id="synthetic",
        at=at(259200),
        segment_sha256=codec.digest(codec.canonical(segment)),
        coverage=rows() if coverage else (),
        final_inventory=A,
        supplemental_corpus=(),
        evaluation=A,
        archive_receipt=None,
        current_release=None,
        unresolved=(),
        result="qualified_pending_archive",
        evidence=(),
    )
    return ledger, intent, segment, assessment, (stopraw, qualification)


def test_seal_replays_actual_gap_instead_of_assessment_claim(tmp_path):
    ledger, intent, segment, assessment, stops = sealed(tmp_path, period=259200)
    with pytest.raises(Hold, match="observation_gap"):
        ledger.commit(intent, segment, assessment, stops)
    assert not list(ledger.root.glob("terminal-*"))


def test_seal_requires_all_seven_actual_coverage_rows(tmp_path):
    ledger, intent, segment, assessment, stops = sealed(tmp_path, coverage=False)
    with pytest.raises(Hold, match="coverage"):
        ledger.commit(intent, segment, assessment, stops)


def test_genuine_synthetic_seal_replays_complete_prefix(tmp_path):
    ledger, intent, segment, assessment, stops = sealed(tmp_path)
    ledger.commit(intent, segment, assessment, stops)
    assert codec.decode(ledger.events()[-1], m.Event).event == "qualification"
    assert isinstance(ledger.proofs, SyntheticProofs)
    assert "coverage" in ledger.proofs.seen


def test_terminal_segment_retry_never_creates_fence(tmp_path):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    ledger.commit(intent, segment, assessment, stops)
    ledger.release(())
    with pytest.raises(Hold, match="segment_already_terminal"):
        ledger.begin("segment")
    assert not (ledger.root / "fence.json").exists()


def test_abort_adopts_exact_persisted_intent(tmp_path, monkeypatch):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    original = ledger._proof
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_proof", lambda *args: (_ for _ in ()).throw(Hold("proof_held")))
        with pytest.raises(Hold, match="proof_held"):
            ledger.commit(intent, segment, assessment, stops)
    ledger.abort("synthetic")
    abort = json.loads(next(ledger.root.glob("abort-*")).read_bytes())
    assert abort["intent_sha256"] == codec.digest(codec.canonical(intent))
    assert ledger._proof == original


def test_abort_different_intent_refuses_without_record(tmp_path, monkeypatch):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_proof", lambda *args: (_ for _ in ()).throw(Hold("proof_held")))
        with pytest.raises(Hold):
            ledger.commit(intent, segment, assessment, stops)
    with pytest.raises(Hold, match="prepared_intent_conflict"):
        ledger.abort("synthetic", replace(intent, transaction_id="other"))
    assert not list(ledger.root.glob("abort-*"))


@pytest.mark.parametrize("operation", ["heartbeat", "abort", "release"])
def test_missing_fence_is_typed_hold(tmp_path, operation):
    raw, events, _ = timeline(seconds=600)
    ledger = Ledger.create(tmp_path / "ledger", raw, SyntheticProofs())
    with pytest.raises(Hold, match="no_active_fence"):
        if operation == "heartbeat":
            ledger.heartbeat(events[1])
        elif operation == "abort":
            ledger.abort("synthetic")
        else:
            ledger.release(())


def test_failed_prepared_files_do_not_consume_spool_or_object_capacity(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    for directory in ("spool", "objects"):
        Ledger._atomic(ledger.root / directory / ".prepare-legacy-crash", b"diagnostic")
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_SPOOL", 1)
    spool = ledger.spool(b'{"one":1}')
    assert (ledger.root / "spool" / spool).exists()
    count = sum(
        p.name != ".prepare-legacy-crash" and not p.name.startswith(".")
        for p in (ledger.root / "objects").iterdir()
    )
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_OBJECTS", count + 1)
    assert ledger._object(b"new object") == codec.digest(b"new object")
    ledger.abort("synthetic")
    ledger.release(())
    released = json.loads(next(ledger.root.glob("released-*")).read_bytes())
    assert released["spool_ids"] == [spool]
    assert (ledger.root / "spool" / ".prepare-legacy-crash").exists()


@pytest.mark.parametrize("operation", ["commit", "release"])
def test_spool_snapshot_locked_through_terminal_publication(tmp_path, operation):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    if operation == "release":
        ledger.abort("synthetic")
    existing = ledger.spool(b'{"before":1}')
    original = ledger.proofs.verify
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        pending = []

        def verify(request, inventory):
            original(request, inventory)
            if request.role == "terminal_fence":
                future = executor.submit(ledger.spool, b'{"concurrent":2}')
                pending.append(future)
                with pytest.raises(concurrent.futures.TimeoutError):
                    future.result(timeout=0.05)

        ledger.proofs.verify = verify
        if operation == "commit":
            ledger.commit(intent, segment, assessment, stops)
            retained = json.loads(next(ledger.root.glob("terminal-*")).read_bytes())
        else:
            ledger.release(())
            retained = json.loads(next(ledger.root.glob("released-*")).read_bytes())
        assert retained["spool_ids"] == [existing]
        assert len(pending) == 1
        assert pending[0].result(timeout=2) == codec.digest(b'{"concurrent":2}')


def test_failed_atomic_prepare_is_retained_outside_envelope_quota(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    original = Path.rename

    def fail_publish(self, target):
        if Path(target).parent == ledger.root / "spool":
            raise OSError("synthetic publication failure")
        return original(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail_publish)
        with pytest.raises(OSError, match="synthetic publication failure"):
            ledger.spool(b'{"failed":"original"}')
    retained = tuple((ledger.root / ".prepared-spool").iterdir())
    assert len(retained) == 1 and retained[0].read_bytes() == b'{"failed":"original"}'
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_SPOOL", 1)
    assert ledger.spool(b'{"valid":1}') == codec.digest(b'{"valid":1}')
    assert len(tuple((ledger.root / "spool").iterdir())) == 1
    assert retained[0].read_bytes() == b'{"failed":"original"}'
