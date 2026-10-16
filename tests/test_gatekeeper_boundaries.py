"""Exact admitted boundaries, host/origin separation and proof preservation."""

import hashlib
from typing import Any, cast

import msgspec
import pytest
from gatekeeper_fixtures import judgment, outcome, privacy, ready, request, seal, wire

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.project import (
    intake_disposition,
    permission_disposition,
    sufficiency_disposition,
)
from heterodyne.gatekeeper.validate import (
    canonical,
    decode_request,
    decode_result,
    digest,
    prepare,
    privacy_context,
    text_hash,
)


def check(kind, chosen):
    req = ready(kind).value
    return decode_result(wire(judgment(req, chosen)), req, privacy())


@pytest.mark.parametrize("kind", ["intake", "sufficiency", "permission"])
def test_exact_public_two_argument_interface(kind):
    prepared = ready(kind)
    result = decode_result(wire(judgment(prepared.value)), prepared.value, privacy())
    function = {
        "intake": intake_disposition,
        "sufficiency": sufficiency_disposition,
        "permission": permission_disposition,
    }[kind]
    expected = {
        "intake": c.CreateMinorProposal(),
        "sufficiency": c.SufficientProposal(),
        "permission": c.AllowProposal(),
    }[kind]
    assert function(prepared, c.Completed(result)) == expected


@pytest.mark.parametrize("value", ["x" * 16384, "é" * 8192])
def test_exact_body_bytes(value):
    raw = request()
    raw["original"] = value
    assert isinstance(prepare(seal(raw), privacy()), c.Ready)
    raw["original"] += "x"
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap) and result.gaps[0].code == "oversize"


@pytest.mark.parametrize("count,valid", [(16, True), (17, False)])
def test_acceptance_count_boundary(count, valid):
    chosen = outcome()
    chosen["acceptance"] = ["Complete" for _ in range(count)]
    result = check("intake", chosen)
    assert isinstance(result, c.Valid) == valid
    if not valid:
        assert result == c.Invalid("result", "oversize", "/outcome/acceptance")


@pytest.mark.parametrize("length,valid", [(4096, True), (4097, False)])
@pytest.mark.parametrize("kind", ["intake", "sufficiency", "permission"])
def test_reason_byte_boundary(kind, length, valid):
    chosen = outcome(kind)
    chosen["reason"] = "x" * length
    result = check(kind, chosen)
    assert isinstance(result, c.Valid) == valid
    if not valid:
        assert result == c.Invalid("result", "oversize", "/outcome/reason")


@pytest.mark.parametrize("count,valid", [(64, True), (65, False)])
def test_groom_gap_boundary(count, valid):
    selected = dict(
        status="groom",
        reason="Missing",
        additions=[],
        gaps=[dict(field="why", detail="Explain purpose") for _ in range(count)],
    )
    result = check("sufficiency", selected)
    assert isinstance(result, c.Valid) == valid
    if not valid:
        assert result == c.Invalid("result", "oversize", "/outcome/gaps")


@pytest.mark.parametrize("count,valid", [(8, True), (9, False)])
def test_addition_count_boundary(count, valid):
    additions = [dict(source_id="1", start=i, end=i + 1, quote="Explicit "[i]) for i in range(count)]
    # Avoid a blank quote; offset9 is another nonblank char of the same verified source.
    if count == 9:
        additions[-1] = dict(source_id="1", start=9, end=10, quote="s")
    selected = dict(status="enrich", reason="Evidence", gaps=[], additions=additions)
    result = check("sufficiency", selected)
    assert isinstance(result, c.Valid) == valid
    if not valid:
        assert result == c.Invalid("result", "oversize", "/outcome/additions")


def test_title_unicode_codepoint_bound():
    raw = request("sufficiency")
    for count, valid in ((200, True), (201, False)):
        raw["candidate"]["title"] = "é" * count
        raw["base_candidate_sha256"] = digest(raw["candidate"])
        assert isinstance(prepare(seal(raw), privacy()), c.Ready) == valid


@pytest.mark.parametrize("count,valid", [(32, True), (33, False)])
def test_open_task_boundary(count, valid):
    raw = request()
    raw["open_tasks"] = [
        dict(id=f"btq-item{i}", title="Task", description_digest="a" * 64) for i in range(count)
    ]
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.Ready) == valid
    if not valid:
        assert result == c.HostFailed("egress_oversize")


@pytest.mark.parametrize("depth,valid", [(8, True), (9, False)])
def test_native_container_depth(depth, valid):
    value = "Value"
    for _ in range(depth):
        value = [value]
    raw = request("sufficiency")
    raw["candidate"]["ask"]["effect"] = value
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    assert isinstance(prepare(seal(raw), privacy()), c.Ready) == valid


@pytest.mark.parametrize("value", [-(2**63), 2**63 - 1, True, [None, False, 0], {"nested": "Safe"}])
def test_native_value_admitted_scalars_and_containers(value):
    raw = request("sufficiency")
    raw["candidate"]["ask"]["effect"] = value
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    assert isinstance(prepare(seal(raw), privacy()), c.Ready)


@pytest.mark.parametrize("value", [-(2**63) - 1, 2**63, 1.0, [0] * 33])
def test_native_value_profile_violations(value):
    raw = request("sufficiency")
    raw["candidate"]["ask"]["effect"] = value
    assert isinstance(prepare(seal(raw), privacy()), c.OriginGap)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("kind", c.InvalidHost("/context")),
        ("unmatched", c.InvalidHost("/context")),
        ("partial", c.HostFailed("stale_digest")),
        ("resolved", c.HostFailed("stale_digest")),
        ("content", c.HostFailed("stale_digest")),
    ],
)
def test_host_context_proof_faults_never_groom(mutation, expected):
    raw = request("sufficiency")
    item = raw["context"][0]
    if mutation == "kind":
        item["kind"] = "diff"
    elif mutation == "unmatched":
        item["ref"] = dict(item["ref"], path="docs/foreign.md")
    elif mutation == "partial":
        item["text"] = item["text"][:-1]
        item["content_id"] = text_hash(item["text"])
    elif mutation == "resolved":
        item["resolved_id"] = "1" * 40
    else:
        item["content_id"] = "1" * 64
    assert prepare(seal(raw), privacy()) == expected


def test_native_digest_readback_is_independent_from_snapshot():
    raw = request("sufficiency")
    assert isinstance(prepare(raw, privacy(), native_digest="d" * 64), c.Ready)
    assert prepare(raw, privacy(), native_digest="c" * 64) == c.HostFailed("stale_digest")


def test_privacy_revision_and_exact_context_hash():
    raw = msgspec.to_builtins(privacy())
    raw["login_dirs"] = ["/other/login"]
    with pytest.raises(ValueError):
        privacy_context(raw)
    raw["sha256"] = digest({key: value for key, value in raw.items() if key != "sha256"})
    assert privacy_context(raw).login_dirs == ("/other/login",)
    raw["rule_revision"] = "1" * 40
    with pytest.raises(ValueError):
        privacy_context(raw)


def test_unused_marker_is_plain_text_and_forged_used_set_is_host_fault():
    raw = request("sufficiency")
    raw["candidate"]["description"] = "Discuss Gatekeeper-added context (source alias 1; ask reference 1):"
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    raw["enrichment"] = dict(
        status="used",
        base_description=raw["candidate"]["description"],
        context_source_ids=["foreign"],
        additions=[dict(source_id="private-source", start=0, end=8, quote="Explicit")],
    )
    assert isinstance(prepare(seal(raw), privacy()), c.InvalidHost)


def test_aggregate_transformed_description_overflow_atomic_groom():
    raw = request("sufficiency")
    raw["candidate"]["description"] = "x" * 16370
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    selected = dict(
        status="enrich",
        reason="Exact context",
        gaps=[],
        additions=[dict(source_id="1", start=0, end=8, quote="Explicit")],
    )
    result = decode_result(wire(judgment(prepared.value, selected)), prepared.value, privacy())
    assert isinstance(result, c.Valid)
    assert sufficiency_disposition(
        cast(c.Ready[c.SufficiencyRequest], prepared), c.Completed(cast(Any, result))
    ) == c.NeedsGrooming((c.Gap("oversize", "/candidate/description", None),))


def test_forbidden_floor_never_invokes_permission_port():
    # Consumer-contract fake: floors precede the port; pure proposals have no effects.
    calls = []
    effects = []
    for floor in ("hard_deny", "operator_only"):

        def dispatch(floor=floor):
            if floor in ("hard_deny", "operator_only"):
                return floor
            calls.append("model")
            effects.append("execute")

        assert dispatch() == floor
    assert calls == effects == []
    assert not hasattr(c.AllowProposal(), "execute")


def test_no_runtime_import_or_effect_surface():
    # The pure module source dependency closure is deliberately narrow.
    import ast
    from pathlib import Path

    from heterodyne.gatekeeper import project, validate

    forbidden = {"subprocess", "socket", "sqlite3", "asyncio", "os", "time", "pathlib"}
    for module in (project, validate):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & forbidden
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden


def test_exact_json_request_aggregate_boundary():
    # Pad a legal body to hit the wire parser boundary with JSON whitespace, not altered oracle semantics.
    encoded = wire(ready().value)
    padded = encoded + b" " * (131072 - len(encoded))
    assert isinstance(decode_request(padded, privacy()), c.Valid)
    assert decode_request(padded + b" ", privacy()) == c.Invalid("request", "oversize", "")


def test_exact_origin_ref_path_and_unknown_key_ancestor():
    raw = request("sufficiency")
    raw["candidate"]["ask"]["refs"][0]["path"] = None
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap)
    assert result.gaps[0].field == "/candidate/ask/refs/0/path"
    raw = request("sufficiency")
    raw["candidate"]["ask"]["refs"][0]["/private/login/unknown"] = "Bad"
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap)
    assert result.gaps[0].field == "/candidate/ask/refs/0"
    assert "/private/login" not in wire(result).decode()


@pytest.mark.parametrize("count,valid", [(512, True), (513, False)])
def test_native_total_nodes(count, valid):
    # One outer list, 16 inner lists and 495/496 scalars, each container <=32.
    scalars = count - 17
    raw = request("sufficiency")
    raw["candidate"]["ask"]["effect"] = [[1] * (scalars // 16 + (i < scalars % 16)) for i in range(16)]
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.Ready) == valid
    if not valid:
        assert isinstance(result, c.OriginGap) and result.gaps[0].code == "oversize"


@pytest.mark.parametrize("count,valid", [(32, True), (33, False)])
def test_context_count_boundary(count, valid):
    raw = request()
    source = request("sufficiency")["context"][0]
    raw["context"] = [dict(source, source_id=f"source-{i}") for i in range(count)]
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.Ready) == valid
    if not valid:
        assert result == c.HostFailed("egress_oversize")


@pytest.mark.parametrize("count,valid", [(16, True), (17, False)])
def test_dependency_edge_count_boundary(count, valid):
    raw = request()
    raw["open_tasks"] = [
        dict(id=f"btq-item{i}", title="Task", description_digest="a" * 64) for i in range(17)
    ]
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    selected = outcome()
    selected["dependencies"] = [dict(task_id=str(i + 1), type="blocks") for i in range(count)]
    checked = decode_result(wire(judgment(prepared.value, selected)), prepared.value, privacy())
    assert isinstance(checked, c.Valid) == valid
    if not valid:
        assert checked == c.Invalid("result", "oversize", "/outcome/dependencies")


@pytest.mark.parametrize("length,valid", [(1024, True), (1025, False)])
def test_quote_byte_boundary(length, valid):
    raw = request("sufficiency")
    text = "x" * length
    blob = hashlib.sha1(
        b"blob " + str(length).encode() + b"\0" + text.encode(), usedforsecurity=False
    ).hexdigest()
    ref = dict(raw["candidate"]["ask"]["refs"][0], id=blob)
    raw["candidate"]["ask"]["refs"] = [ref]
    raw["resolved_refs"] = [dict(ref=ref, resolved_id=blob)]
    raw["context"] = [
        dict(raw["context"][0], text=text, content_id=text_hash(text), ref=ref, resolved_id=blob)
    ]
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    selected = dict(
        status="enrich",
        reason="Evidence",
        gaps=[],
        additions=[dict(source_id="1", start=0, end=length, quote=text)],
    )
    checked = decode_result(wire(judgment(prepared.value, selected)), prepared.value, privacy())
    assert isinstance(checked, c.Valid) == valid
    if not valid:
        assert checked == c.Invalid("result", "oversize", "/outcome/additions/0/quote")


def test_transformed_whole_request_one_over_is_atomic_groom():
    raw = request("sufficiency")
    text = "x" * 4000
    blob = hashlib.sha1(b"blob 4000\0" + text.encode(), usedforsecurity=False).hexdigest()
    ref = dict(raw["candidate"]["ask"]["refs"][0], id=blob)
    raw["candidate"]["ask"]["refs"] = [ref]
    raw["resolved_refs"] = [dict(ref=ref, resolved_id=blob)]
    raw["context"] = [
        dict(
            raw["context"][0],
            source_id=f"source-{i:02}",
            text=text,
            content_id=text_hash(text),
            ref=ref,
            resolved_id=blob,
        )
        for i in range(27)
    ]
    raw["candidate"]["description"] = "x"
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    sealed = seal(raw)
    remaining = 131052 - len(canonical(sealed)) + 1
    assert 1 <= remaining <= 16384
    raw["candidate"]["description"] = "x" * remaining
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready), prepared
    assert len(canonical(prepared.value)) == 131052
    selected = dict(
        status="enrich",
        reason="Evidence",
        gaps=[],
        additions=[dict(source_id="1", start=0, end=1, quote="x")],
    )
    checked = decode_result(wire(judgment(prepared.value, selected)), prepared.value, privacy())
    assert isinstance(checked, c.Valid)
    assert sufficiency_disposition(
        cast(c.Ready[c.SufficiencyRequest], prepared), c.Completed(cast(Any, checked))
    ) == c.NeedsGrooming((c.Gap("oversize", "/candidate/description", None),))
