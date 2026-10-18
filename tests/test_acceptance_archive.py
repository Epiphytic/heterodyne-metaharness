"""Closed offline archive graph; no repository is archived and no real consent is asserted."""

from typing import cast

import pytest
from acceptance_fixtures import NATIVE, A, SyntheticProofs, at, entry, event, replace, rows, timeline

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import REPOSITORIES, Evaluator, Inventory


def graph(*, period=600):
    entry_raw, chain, inventory = timeline(period=period)
    last = codec.decode(chain[-1], m.Event)
    assert last.checkpoint is not None
    stop = replace(
        event(len(chain), codec.digest(chain[-1]), 259200, "segment_stop"),
        stop=m.Stop(
            reason="none",
            checkpoint=last.checkpoint,
            checkpoint_event_sha256=codec.digest(chain[-1]),
            transaction_id="tx",
        ),
    )
    chain.append(codec.canonical(stop))
    chain.append(codec.canonical(event(len(chain), codec.digest(chain[-1]), 259200, "qualification")))
    inventory["archive-event-chain"] = codec.canonical([codec.load(raw) for raw in chain])
    segment = m.Segment(
        format=1,
        run_id="synthetic",
        segment_id="segment",
        entry_sha256=codec.digest(entry_raw),
        start_event_sha256=codec.digest(chain[0]),
        end_event_sha256=codec.digest(codec.canonical(stop)),
        start=at(),
        end=at(259200),
        duration_ns="259200000000000",
        status="sealed",
        reason="none",
        evidence=(),
    )
    segment_raw = codec.canonical(segment)
    before = m.Assessment(
        format=1,
        run_id="synthetic",
        at=at(259201),
        segment_sha256=codec.digest(segment_raw),
        coverage=rows(),
        final_inventory=A,
        supplemental_corpus=(),
        evaluation=A,
        archive_receipt=None,
        current_release=None,
        unresolved=(),
        result="qualified_pending_archive",
        evidence=(),
    )
    before_raw = codec.canonical(before)
    archive = m.Archive(
        format=1,
        run_id="synthetic",
        at=at(259202),
        assessment_sha256=codec.digest(before_raw),
        authority=NATIVE,
        final_migration=m.FinalMigration(
            entry_migration=A, final_inventory=A, final_dispositions=A, second_delta_audit=A
        ),
        repositories=tuple(
            m.ArchiveRow(
                repository=cast(m.Repository, repo),
                retained_history=A,
                read_only_verification=A,
                dependency_audit=A,
                operation=A,
                result="complete",
            )
            for repo in sorted(REPOSITORIES)
        ),
        result="complete",
        evidence=(),
    )
    archive_raw = codec.canonical(archive)
    receipt = m.Artifact(id="archive", sha256=codec.digest(archive_raw))
    final = replace(
        before,
        at=at(259203),
        result="release_complete",
        archive_receipt=receipt,
        current_release=entry().release,
    )
    inventory[receipt.id] = archive_raw
    return entry_raw, before_raw, archive_raw, codec.canonical(final), segment_raw, inventory


def test_complete_non_circular_synthetic_graph():
    *raws, inventory = graph()
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        *raws, event_bytes=archive_events(inventory)
    )
    assert result.result == "release_complete"


@pytest.mark.parametrize(
    "role", ["native_authority", "terminal_fence", "final_migration", "archive", "current_release"]
)
def test_every_source_specific_archive_proof_is_mandatory(role):
    *raws, inventory = graph()
    result = Evaluator(Inventory(inventory), SyntheticProofs(role)).archive(
        *raws, event_bytes=archive_events(inventory)
    )
    assert result.result == "not_qualified"
    assert result.reason == "synthetic_source_refused"


def test_archive_default_unavailable_never_releases():
    *raws, inventory = graph()
    assert (
        Evaluator(Inventory(inventory)).archive(*raws, event_bytes=archive_events(inventory)).result
        == "not_qualified"
    )


def test_archive_time_identity_change_cannot_be_waived_by_consent():
    raw, before, archive, final, segment, inventory = graph()
    value = codec.decode(final, m.Assessment)
    assert value.current_release is not None
    changed = replace(value.current_release.identity.components[0], content_digest="e" * 64)
    identity = replace(
        value.current_release.identity, components=(changed, value.current_release.identity.components[1])
    )
    value = replace(value, current_release=replace(value.current_release, identity=identity))
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        raw, before, archive, codec.canonical(value), segment, event_bytes=archive_events(inventory)
    )
    assert result.reason == "release_change"


def test_final_cannot_replace_interval_coverage():
    raw, before, archive, final, segment, inventory = graph()
    value = replace(codec.decode(final, m.Assessment), coverage=())
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        raw, before, archive, codec.canonical(value), segment, event_bytes=archive_events(inventory)
    )
    assert result.reason == "final_interval_changed"


def test_second_delta_missing_is_schema_refusal():
    import json

    _, _, archive, _, _, _ = graph()
    value = json.loads(archive)
    del value["final_migration"]["second_delta_audit"]
    with pytest.raises(codec.InvalidEvidence):
        codec.decode(codec.canonical(value), m.Archive)


def archive_events(inventory):
    return tuple(
        codec.canonical(value) for value in cast(list[object], codec.load(inventory["archive-event-chain"]))
    )
