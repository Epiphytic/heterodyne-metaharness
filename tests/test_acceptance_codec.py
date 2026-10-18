"""Synthetic offline Plan9 contracts; no native or live evidence is authenticated."""

import json

import msgspec
import pytest

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m


def test_nanoseconds_preserve_uint64_and_beyond_binary_float() -> None:
    for value in ("9007199254740993", "18446744073709551615"):
        assert codec.decode(json.dumps(value).encode(), m.NS) == value


@pytest.mark.parametrize(
    "raw", [b"0", b"true", b"1.0", b'"01"', b'"-1"', b'"18446744073709551616"', b'"1e9"']
)
def test_nanoseconds_refuse_noncanonical_or_overflow(raw: bytes) -> None:
    with pytest.raises(codec.InvalidEvidence):
        codec.decode(raw, m.NS)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id":"x","id":"y","sha256":"' + b"a" * 64 + b'"}',
        b'{"id":"x","sha256":"' + b"a" * 64 + b'","extra":0}',
        b'{"id":"x","sha256":"' + b"a" * 64 + b'"} true',
    ],
)
def test_closed_duplicate_and_trailing_refusal(raw: bytes) -> None:
    with pytest.raises(codec.InvalidEvidence):
        codec.decode(raw, m.Artifact)


def test_canonical_identity_is_ascii_sorted_without_newline() -> None:
    assert codec.canonical({"z": "é", "a": 1}) == b'{"a":1,"z":"\\u00e9"}'


@pytest.mark.parametrize("value", ["2026-02-30T00:00:00.000000Z", "2026-10-16T00:00:00Z"])
def test_utc_requires_real_generated_microsecond_timestamp(value: str) -> None:
    with pytest.raises(codec.InvalidEvidence):
        codec.decode(json.dumps(value).encode(), m.UTC)


def test_bool_is_not_a_count_or_literal_integer() -> None:
    with pytest.raises(codec.InvalidEvidence):
        codec.decode(b"true", m.Count)


def test_native_seconds_are_retained_and_normalized_separately() -> None:
    assert codec.native_utc("2026-10-14T15:57:38Z") == "2026-10-14T15:57:38.000000Z"
    with pytest.raises(codec.InvalidEvidence):
        codec.native_utc("2026-10-14T15:57:38")


def test_model_construction_is_not_validation() -> None:
    invalid = m.Artifact(id="not an ID", sha256="bad")
    with pytest.raises(codec.InvalidEvidence):
        codec.validate(invalid)
    assert msgspec.to_builtins(m.Artifact(id="x", sha256="a" * 64))["id"] == "x"


def test_optional_ns_and_utc_fields_are_fully_validated():
    from acceptance_fixtures import at

    value = m.Segment(
        format=1,
        run_id="fixture",
        segment_id="segment",
        entry_sha256="a" * 64,
        start_event_sha256="b" * 64,
        end_event_sha256=None,
        start=at(),
        end="2026-02-30T00:00:00.000000Z",
        duration_ns=None,
        status="running",
        reason="none",
        evidence=(),
    )
    with pytest.raises(codec.InvalidEvidence):
        codec.validate(value)
    value = msgspec.structs.replace(value, end=None, duration_ns="18446744073709551616")
    with pytest.raises(codec.InvalidEvidence):
        codec.validate(value)
