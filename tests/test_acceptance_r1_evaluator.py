"""Adversarial R1 regressions; synthetic sources assert no real release authority."""

import pytest
from acceptance_fixtures import A, SyntheticProofs, entry, replace
from test_acceptance_archive import archive_events, graph

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import Evaluator, Hold, Inventory


@pytest.mark.parametrize("field,value", [("content_digest", "e" * 64), ("kind", "configuration")])
def test_isolated_projection_requires_exact_component(field, value):
    original = entry()
    drill = next(d for d in original.drills if d.scope == "isolated")
    changed = replace(drill.identity.components[0], **{field: value})
    altered = replace(
        drill, identity=replace(drill.identity, components=(changed, *drill.identity.components[1:]))
    )
    raw = codec.canonical(
        replace(original, drills=tuple(altered if d.id == drill.id else d for d in original.drills))
    )
    with pytest.raises(Hold, match="drill_projection_mismatch"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(raw)


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-16T21:10:14.123456999Z",
        "2026-10-16T21:10:14.123456001Z",
        "2026-10-16 21:10:14Z",
        "20261016T211014Z",
        "2026-W42-5T21:10:14Z",
        "2026-10-16T21:10:14+00:00:01",
    ],
)
def test_native_timestamp_cannot_lose_precision_or_accept_lenient_forms(stamp):
    expected = Hold if ".123456" in stamp else codec.InvalidEvidence
    code = "unsupported_native_precision" if expected is Hold else "invalid_native_time"
    with pytest.raises(expected, match=code):
        codec.native_utc(stamp)


def test_archive_cannot_qualify_without_replayable_interval():
    *raws, inventory = graph()
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(*raws)
    assert result.result == "not_qualified"


def test_archive_replays_gaps_instead_of_trusting_reported_duration():
    *raws, inventory = graph(period=700)
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        *raws, event_bytes=archive_events(inventory)
    )
    assert result.result == "not_qualified"
    assert result.reason == "observation_gap"


@pytest.mark.parametrize("field", ["end", "end_event_sha256"])
def test_archive_requires_nonnull_terminal_endpoint(field):
    raw, before, archive, final, segment, inventory = graph()
    changed = codec.canonical(replace(codec.decode(segment, m.Segment), **{field: None}))
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        raw, before, archive, final, changed, event_bytes=archive_events(inventory)
    )
    assert result.reason == "archive_segment"


def test_archive_replays_exact_seven_coverage_rows():
    raw, before_raw, archive_raw, final_raw, segment, inventory = graph()
    before = replace(codec.decode(before_raw, m.Assessment), coverage=())
    before_raw = codec.canonical(before)
    archive = replace(codec.decode(archive_raw, m.Archive), assessment_sha256=codec.digest(before_raw))
    archive_raw = codec.canonical(archive)
    receipt = m.Artifact(id="rebound-archive", sha256=codec.digest(archive_raw))
    final = replace(codec.decode(final_raw, m.Assessment), coverage=(), archive_receipt=receipt)
    inventory[receipt.id] = archive_raw
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(
        raw, before_raw, archive_raw, codec.canonical(final), segment, event_bytes=archive_events(inventory)
    )
    assert result.reason == "coverage_set"


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-10-16T21:10:14Z", "2026-10-16T21:10:14.000000Z"),
        ("2026-10-16T21:10:14.123456Z", "2026-10-16T21:10:14.123456Z"),
        ("2026-10-16T22:10:14.1+01:00", "2026-10-16T21:10:14.100000Z"),
    ],
)
def test_supported_native_timestamp_precision_is_exact(stamp, expected):
    assert codec.native_utc(stamp) == expected


def test_isolated_drill_may_differ_outside_fixed_projection():
    original = entry()
    drill = next(d for d in original.drills if d.scope == "isolated")
    changed = replace(drill.identity.components[1], content_digest="e" * 64)
    altered = replace(
        drill,
        projection_ids=("claude",),
        identity=replace(drill.identity, components=(drill.identity.components[0], changed)),
    )
    raw = codec.canonical(
        replace(original, drills=tuple(altered if d.id == drill.id else d for d in original.drills))
    )
    assert Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(raw)
