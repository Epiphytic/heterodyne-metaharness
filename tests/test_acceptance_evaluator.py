"""Synthetic offline evidence cannot establish operational acceptance."""

from typing import get_args

import pytest
from acceptance_fixtures import IDENTITY, NATIVE, A, SyntheticProofs, at, entry, replace, rows, timeline

from heterodyne.acceptance import codec
from heterodyne.acceptance import models as m
from heterodyne.acceptance.evaluator import DRILLS, GATES, Evaluator, Hold, Inventory


def test_exact_registry_sizes_and_source_adapter_default_hold() -> None:
    assert len(DRILLS) == 17
    assert len(GATES) == 18
    raw, events, inventory = timeline()
    result = Evaluator(Inventory(inventory)).interval(raw, events, "segment", rows())
    assert result.result == "not_qualified"
    assert result.reason.startswith("source_adapter_unavailable:")


def test_exact_same_boot_synthetic_72_hours() -> None:
    raw, events, inventory = timeline()
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert result.result == "qualified_pending_archive"
    assert result.duration_ns == "259200000000000"


@pytest.mark.parametrize("period", [300, 301, 600, 601])
def test_full_audit_gap_rules_not_nominal_rounding(period: int) -> None:
    raw, events, inventory = timeline(period=period)
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert (result.result == "qualified_pending_archive") == (period <= 600)
    if period == 601:
        assert result.reason == "observation_gap"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"prerequisites": ()}, "gate_set"),
        ({"corpus": ()}, "empty_corpus"),
        ({"drills": ()}, "drill_subcases"),
        ({"governing_chain": ()}, "governing_chain_empty"),
    ],
)
def test_missing_mandatory_entry_evidence_holds(change, code: str) -> None:
    raw = codec.canonical(replace(entry(), **change))
    with pytest.raises(Hold, match=code):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(raw)


@pytest.mark.parametrize("gate", get_args(m.GateName))
def test_every_gate_is_required(gate: str) -> None:
    value = entry()
    changed = replace(value, prerequisites=tuple(g for g in value.prerequisites if g.name != gate))
    with pytest.raises(Hold, match="gate_set"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(
            codec.canonical(changed)
        )


@pytest.mark.parametrize("name", get_args(m.DrillSubcase))
def test_every_drill_subcase_is_required(name: str) -> None:
    value = entry()
    changed = replace(value, drills=tuple(d for d in value.drills if d.subcase != name))
    with pytest.raises(Hold, match="drill_subcases"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(
            codec.canonical(changed)
        )


def test_deviation_on_unrelated_gate_is_not_authority() -> None:
    value = entry()
    gates = tuple(
        replace(g, mode="approved_deviation") if g.name == "plan5" else g for g in value.prerequisites
    )
    with pytest.raises(Hold, match="gate_deviation"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(
            codec.canonical(replace(value, prerequisites=gates))
        )


def test_historical_native_channel_cannot_invent_marmot_audit() -> None:
    with pytest.raises(Hold, match="marmot_provenance"):
        Evaluator(Inventory({}), SyntheticProofs()).authority(replace(NATIVE, via="cli"), "a" * 64)


def test_original_seconds_precision_is_not_rewritten() -> None:
    value = replace(NATIVE, approved_at_utc="2026-10-14T15:57:39.000000Z")
    with pytest.raises(Hold, match="authority_time"):
        Evaluator(Inventory({}), SyntheticProofs()).authority(value, "a" * 64)


def test_wrong_artifact_bytes_fail_before_source_proof() -> None:
    with pytest.raises(Hold, match="artifact_missing_or_changed"):
        Evaluator(Inventory({A.id: b"corrupt"}), SyntheticProofs()).entry(codec.canonical(entry()))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("boot_id", "newboot", "clock_uncertain"),
        ("at", at(602), "clock_uncertain"),
        ("result", "hold", "checkpoint_hold"),
    ],
)
def test_checkpoint_interruptions(field: str, value: str, code: str) -> None:
    raw, events, inventory = timeline(seconds=1200)
    first = codec.decode(inventory["cp0"], m.Checkpoint)
    second = codec.decode(inventory["cp1"], m.Checkpoint)
    with pytest.raises(Hold, match=code):
        Evaluator(Inventory(inventory), SyntheticProofs()).checkpoint(
            replace(second, **{field: value}), codec.decode(raw, m.Entry), codec.digest(raw), first
        )


def test_changed_component_and_reverted_unknown_history_never_waived() -> None:
    raw, _, inventory = timeline(seconds=600)
    cp = codec.decode(inventory["cp0"], m.Checkpoint)
    changed = replace(IDENTITY, components=IDENTITY.components[:1])
    with pytest.raises(Hold, match="release_change"):
        Evaluator(Inventory(inventory), SyntheticProofs()).checkpoint(
            replace(cp, identity=changed), codec.decode(raw, m.Entry), codec.digest(raw), None
        )
    with pytest.raises(Hold, match="synthetic_source_refused"):
        Evaluator(Inventory(inventory), SyntheticProofs("checkpoint")).checkpoint(
            cp, codec.decode(raw, m.Entry), codec.digest(raw), None
        )


def test_missing_coverage_does_not_turn_into_idle_success() -> None:
    raw, events, inventory = timeline()
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows()[:-1])
    assert result.reason == "coverage_set"


def test_changed_event_bytes_break_hash_chain() -> None:
    raw, events, inventory = timeline(seconds=1200)
    changed = codec.decode(events[1], m.Event)
    events[1] = codec.canonical(replace(changed, detail=replace(changed.detail, code="changed")))
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert result.reason == "event_chain"


def test_global_adverse_event_cannot_be_hidden_with_null_segment():
    raw, events, inventory = timeline()
    prior = codec.decode(events[-1], m.Event)
    hidden = replace(
        prior,
        seq=len(events),
        previous_sha256=codec.digest(events[-1]),
        segment_id=None,
        event="source_change",
        checkpoint=None,
    )
    events.append(codec.canonical(hidden))
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert result.result == "not_qualified"
    assert result.reason == "release_change"


def test_missing_external_coverage_artifact_cannot_pass_synthetic_port():
    raw, events, inventory = timeline()
    coverage = list(rows())
    coverage[0] = replace(coverage[0], evidence=(m.Artifact(id="absent", sha256="f" * 64),))
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", coverage)
    assert result.result == "not_qualified"
    assert result.reason == "artifact_missing_or_changed"


def test_one_nanosecond_short_is_not_rounded_up():
    raw, events, inventory = timeline()
    tail = codec.decode(events[-1], m.Event)
    assert tail.checkpoint is not None
    cp = codec.decode(inventory[tail.checkpoint.id], m.Checkpoint)
    ns = str(int(cp.monotonic_ns) - 1)
    changed = replace(
        cp, monotonic_ns=ns, audit_intervals=tuple(replace(i, to_monotonic_ns=ns) for i in cp.audit_intervals)
    )
    cpraw = codec.canonical(changed)
    inventory[tail.checkpoint.id] = cpraw
    events[-1] = codec.canonical(
        replace(tail, checkpoint=replace(tail.checkpoint, sha256=codec.digest(cpraw)))
    )
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert result.reason == "duration_short"


@pytest.mark.parametrize("extra,expected", [(1, "none"), (1.000001, "clock_uncertain")])
def test_exact_one_second_clock_error_limit(extra, expected):
    raw, events, inventory = timeline()
    tail = codec.decode(events[-1], m.Event)
    assert tail.checkpoint is not None
    cp = codec.decode(inventory[tail.checkpoint.id], m.Checkpoint)
    changed = replace(cp, at=at(259200 + extra))
    cpraw = codec.canonical(changed)
    inventory[tail.checkpoint.id] = cpraw
    events[-1] = codec.canonical(
        replace(tail, at=changed.at, checkpoint=replace(tail.checkpoint, sha256=codec.digest(cpraw)))
    )
    result = Evaluator(Inventory(inventory), SyntheticProofs()).interval(raw, events, "segment", rows())
    assert result.reason == expected


def test_duplicate_drill_subcase_cannot_hide_behind_distinct_id():
    value = entry()
    value = replace(value, drills=value.drills + (replace(value.drills[0], id="duplicate"),))
    with pytest.raises(Hold, match="drill_subcases"):
        Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs()).entry(
            codec.canonical(value)
        )


def test_governing_successor_requires_original_link_and_explicit_amendment():
    value = entry()
    successor = replace(NATIVE, native_bead_id="synthetic-successor", adr_revision="f" * 40)
    links = value.governing_chain + (
        m.GoverningLink(
            revision="f" * 40, parent_revision=NATIVE.adr_revision, authority=successor, amendment=A
        ),
    )
    value = replace(
        value,
        governing_chain=links,
        design_authority=successor,
        release=replace(value.release, governing_adr="f" * 40),
        prerequisites=tuple(
            replace(g, authority=successor) if g.name == "governing_authority" else g
            for g in value.prerequisites
        ),
    )
    evaluator = Evaluator(Inventory({A.id: b"synthetic fixture only"}), SyntheticProofs())
    assert evaluator.entry(codec.canonical(value)) == value
    with pytest.raises(Hold, match="governing_original"):
        evaluator.entry(codec.canonical(replace(value, governing_chain=links[1:])))
    with pytest.raises(Hold, match="governing_successor"):
        evaluator.entry(
            codec.canonical(replace(value, governing_chain=(links[0], replace(links[1], amendment=None))))
        )
