"""R2 archive terminal completeness and native precision diagnostics."""

import pytest
from acceptance_fixtures import A, SyntheticProofs, entry, replace
from test_acceptance_archive import archive_events, graph

from heterodyne.acceptance import codec
from heterodyne.acceptance.evaluator import Evaluator, Hold, Inventory


def test_archive_requires_published_qualification_after_stop():
    *raws, inventory = graph()
    events = archive_events(inventory)
    assert b'"event":"qualification"' in events[-1]
    result = Evaluator(Inventory(inventory), SyntheticProofs()).archive(*raws, event_bytes=events[:-1])
    assert result.result == "not_qualified"
    assert result.reason == "archive_terminal_tail"


@pytest.mark.parametrize(
    "stamp",
    [
        "2026-10-16T21:10:14.123456789Z",
        "2026-10-16T22:10:14.123456001+01:00",
    ],
)
def test_supported_form_with_excess_precision_is_distinct_typed_hold(stamp):
    with pytest.raises(Hold, match="^unsupported_native_precision$"):
        codec.native_utc(stamp)


def test_excess_precision_does_not_hide_invalid_calendar():
    with pytest.raises(codec.InvalidEvidence, match="^invalid_native_time$"):
        codec.native_utc("2026-19-16T21:10:14.123456789Z")


def test_direct_entry_reports_unsupported_native_precision_as_hold():
    original = entry()
    changed = replace(original.deployment_authority, approved_at="2026-10-14T15:57:38.000000001Z")
    raw = codec.canonical(replace(original, deployment_authority=changed))
    with pytest.raises(Hold, match="^unsupported_native_precision$"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(raw)
