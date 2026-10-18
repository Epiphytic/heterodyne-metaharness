"""Second-HIGH ledger regressions using synthetic, nonoperational proof ports."""

from functools import partial

import pytest
from acceptance_fixtures import SyntheticProofs, at, event, replace, timeline
from test_acceptance_ledger import prepared

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import Hold
from heterodyne.acceptance.ledger import Ledger


def test_retry_fence_has_fresh_identity_and_no_old_abort(tmp_path):
    raw, _, _ = timeline(seconds=600)
    ledger = Ledger.create(tmp_path / "ledger", raw, SyntheticProofs())
    first = ledger.begin("segment")
    ledger.abort("synthetic")
    ledger.release(())
    second = ledger.begin("segment")
    assert first != second
    assert not (ledger.root / f"abort-{second}.json").exists()
    with pytest.raises(Hold, match="fence_not_terminal"):
        ledger.release(())


@pytest.mark.parametrize("failure", ["reason", "checkpoint", "start", "proof"])
def test_unprepared_abort_intent_failure_never_poisoned(tmp_path, failure):
    ledger, intent, _, _, _ = prepared(tmp_path)
    if failure == "checkpoint":
        intent = replace(intent, checkpoint_event_sha256="a" * 64)
    elif failure == "start":
        intent = replace(intent, start_event_sha256="b" * 64)
    elif failure == "proof":
        ledger.proofs.verify = lambda request, inventory: (_ for _ in ()).throw(Hold("synthetic_refusal"))
    with pytest.raises((Hold, codec.InvalidEvidence)):
        ledger.abort("bad reason" if failure == "reason" else "synthetic", intent)
    assert not list(ledger.root.glob("intent-*"))
    assert not list(ledger.root.glob("abort-*"))


@pytest.mark.parametrize("failure", ["chronology", "refs", "proof", "clock"])
def test_terminal_candidate_replayed_before_commit(tmp_path, failure):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    stop = codec.decode(stops[0], m.Event)
    if failure == "chronology":
        stop = replace(stop, at=at(1))
    elif failure == "refs":
        stop = replace(stop, refs=(m.Artifact(id="missing", sha256="a" * 64),))
    elif failure == "clock":
        stop = replace(stop, boot_id="22345678-1234-4234-8234-123456789abc")
    else:
        original = ledger.proofs.verify

        def reject(request, inventory):
            if request.role == "event" and b'"segment_stop"' in request.value:
                raise Hold("synthetic_event_refusal")
            original(request, inventory)

        ledger.proofs.verify = reject
    changed = codec.canonical(stop)
    segment = replace(segment, end_event_sha256=codec.digest(changed))
    assessment = replace(assessment, segment_sha256=codec.digest(codec.canonical(segment)))
    with pytest.raises(Hold):
        ledger.commit(intent, segment, assessment, (changed,))
    assert not list(ledger.root.glob("terminal-*"))


@pytest.mark.parametrize("failure", ["checkpoint", "stop", "chronology", "refs"])
def test_release_replays_whole_candidate_before_append(tmp_path, failure):
    ledger, *_ = prepared(tmp_path)
    ledger.abort("synthetic")
    before = ledger.events()
    item = replace(event(len(before), codec.digest(before[-1]), 601, "hold"), segment_id=None)
    if failure == "checkpoint":
        item = replace(item, event="heartbeat")
    elif failure == "stop":
        item = replace(item, event="segment_stop")
    elif failure == "chronology":
        item = replace(item, at=at(1))
    else:
        item = replace(item, refs=(m.Artifact(id="missing", sha256="a" * 64),))
    with pytest.raises(Hold):
        ledger.release((codec.canonical(item),))
    assert ledger.events() == before
    assert (ledger.root / "fence.json").exists()


def test_retired_spool_does_not_consume_active_capacity(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    monkeypatch.setattr("heterodyne.acceptance.ledger.MAX_SPOOL", 1)
    original = b'{"first":1}'
    first = ledger.spool(original)
    ledger.abort("synthetic")
    ledger.release(())
    assert (ledger.root / "spool" / first).read_bytes() == original
    second = ledger.spool(b'{"second":2}')
    assert ledger._spool_ids() == (second,)


def test_release_receipt_crash_retry_keeps_new_evidence(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    first = ledger.spool(b'{"first":1}')
    ledger.abort("synthetic")
    original = Ledger._atomic

    def crash(path, raw):
        original(path, raw)
        if path.name.startswith("released-"):
            raise OSError("after release receipt")

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, "_atomic", staticmethod(crash))
        with pytest.raises(OSError, match="after release receipt"):
            ledger.release(())
    receipt = next(ledger.root.glob("released-*")).read_bytes()
    second = ledger.spool(b'{"new_after_receipt":2}')
    ledger.release(())
    assert next(ledger.root.glob("released-*")).read_bytes() == receipt
    assert ledger._spool_ids() == (second,)
    assert (ledger.root / "spool" / first).read_bytes() == b'{"first":1}'
    ledger.release(())  # exact retry after the fence has already been removed
    assert ledger._spool_ids() == (second,)


@pytest.mark.parametrize("kind", ["object", "binding", "heartbeat", "terminal"])
@pytest.mark.parametrize("damage", ["missing", "symlink"])
def test_missing_or_symlinked_original_is_typed_hold(tmp_path, kind, damage):
    ledger, intent, segment, assessment, stops = prepared(tmp_path)
    if kind == "terminal":
        ledger.commit(intent, segment, assessment, stops)
        target = next(ledger.root.glob("terminal-*"))
        action = ledger.recover
    elif kind == "binding":
        target = ledger.root / "inventory" / codec.digest(intent.checkpoint.id.encode())
        action = partial(ledger.commit, intent, segment, assessment, stops)
    elif kind == "heartbeat":
        target = ledger.root / "objects" / intent.checkpoint_event_sha256
        action = partial(ledger.commit, intent, segment, assessment, stops)
    else:
        target = ledger.root / "objects" / intent.checkpoint.sha256
        action = partial(ledger._get, intent.checkpoint.sha256)
    target.unlink()
    if damage == "symlink":
        target.symlink_to(ledger.root / "entry.json")
    with pytest.raises(Hold):
        action()


def test_release_partial_append_retries_only_exact_program(tmp_path, monkeypatch):
    ledger, *_ = prepared(tmp_path)
    ledger.abort("synthetic")
    before = ledger.events()
    first = codec.canonical(
        replace(event(len(before), codec.digest(before[-1]), 601, "hold"), segment_id=None)
    )
    second = codec.canonical(
        replace(event(len(before) + 1, codec.digest(first), 602, "hold"), segment_id=None)
    )
    original = Ledger._append

    def crash(self, raw):
        original(self, raw)
        if raw == first:
            raise OSError("after first reconciled event")

    with monkeypatch.context() as patch:
        patch.setattr(Ledger, "_append", crash)
        with pytest.raises(OSError, match="after first"):
            ledger.release((first, second))
    with pytest.raises(Hold, match="release_program_conflict"):
        ledger.release((first,))
    ledger.release((first, second))
    ledger.release((first, second))
    assert ledger.events() == (*before, first, second)


def test_retirement_marker_without_matching_receipt_cannot_drop_source(tmp_path):
    ledger, *_ = prepared(tmp_path)
    original = b'{"source":1}'
    source = ledger.spool(original)
    Ledger._atomic(ledger.root / "spool-retired" / source, codec.canonical("a" * 64))
    with pytest.raises(Hold):
        ledger._spool_ids()
    assert (ledger.root / "spool" / source).read_bytes() == original


def test_unreadable_object_is_typed_hold(tmp_path, monkeypatch):
    ledger, intent, *_ = prepared(tmp_path)
    import os

    original = os.read

    def unreadable(fd, count):
        raise OSError("synthetic unreadable file")

    monkeypatch.setattr(os, "read", unreadable)
    with pytest.raises(Hold, match="ledger_file_unavailable"):
        ledger._get(intent.checkpoint.sha256)
    monkeypatch.setattr(os, "read", original)
