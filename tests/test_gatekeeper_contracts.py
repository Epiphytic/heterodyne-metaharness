"""Closed wire grammar, provenance and privacy failures, entirely offline."""

import copy
import json
import re
from pathlib import Path
from typing import Any, cast

import msgspec
import pytest
from gatekeeper_fixtures import judgment, privacy, ready, request, seal, wire
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import (
    canonical,
    decode_request,
    decode_result,
    digest,
    egress,
    native_lint_gap,
    prepare,
    public_path,
)

KINDS = ("intake", "sufficiency", "permission")


@pytest.mark.parametrize("kind", KINDS)
def test_roundtrip_closed_requests_and_results(kind):
    req = ready(kind).value
    assert decode_request(wire(req), privacy()) == c.Valid(req)
    assert isinstance(decode_result(wire(judgment(req)), req, privacy()), c.Valid)
    assert json.loads(wire(req)) == request(kind)


@pytest.mark.parametrize(
    "bad",
    [
        b'{"a":1,"a":2}',
        b'{"x":NaN}',
        b"{} trailing",
        b"\xff",
        b'"\\ud800"',
        b'{"revision":true}',
        b"[]",
        b"null",
    ],
)
@pytest.mark.parametrize("kind", KINDS)
def test_malformed_wire_is_content_free(kind, bad):
    req = ready(kind).value
    result = decode_result(bad, req, privacy())
    assert isinstance(result, c.Invalid)
    assert result.stage == "result"
    assert result.field == ("/revision" if bad == b'{"revision":true}' else "")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mutation", ["unknown", "missing", "bool", "enum", "foreign", "null"])
def test_result_closed_grammar_and_echo(kind, mutation):
    req = ready(kind).value
    raw = judgment(req)
    if mutation == "unknown":
        raw["private-extra"] = "hidden"
    elif mutation == "missing":
        del raw["revision"]
    elif mutation == "bool":
        raw["revision"] = True
    elif mutation == "enum":
        raw["outcome"]["status"] = "invented"
    elif mutation == "foreign":
        raw["request_id"] = "foreign"
    else:
        raw["outcome"] = None
    assert isinstance(decode_result(wire(raw), req, privacy()), c.Invalid)


@pytest.mark.parametrize("kind", KINDS)
def test_model_cannot_select_host_failure(kind):
    req = ready(kind).value
    raw = judgment(req, dict(status="unavailable", reason_code="timeout"))
    assert isinstance(decode_result(wire(raw), req, privacy()), c.Invalid)
    raw["outcome"] = dict(status="host_failed", reason_code="unreadable_policy")
    assert isinstance(decode_result(wire(raw), req, privacy()), c.Invalid)


@pytest.mark.parametrize("kind", KINDS)
def test_raw_response_bound(kind):
    req = ready(kind).value
    result = decode_result(b" " * 32769, req, privacy())
    assert result == c.Invalid("result", "oversize", "")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field", ["request_id", "revision", "snapshot_sha256"])
def test_result_binding(kind, field):
    req = ready(kind).value
    raw = judgment(req)
    raw[field] = 2 if field == "revision" else "e" * 64
    assert decode_result(wire(raw), req, privacy()) == c.Invalid("result", "binding", "")


@pytest.mark.parametrize("kind", KINDS)
def test_changed_request_snapshot_and_privacy_fail_host(kind):
    raw = request(kind)
    raw["revision"] += 1
    assert isinstance(prepare(raw, privacy()), c.InvalidHost)
    raw = request(kind)
    raw["privacy_sha256"] = "e" * 64
    assert isinstance(prepare(seal(raw), privacy()), c.InvalidHost)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "unsafe",
    [
        "/private/login/config",
        "f" * 64,
        "bad\rtext",
        "bad\x80text",
        "-----BEGIN PRIVATE KEY-----\nnot-real\n-----END PRIVATE KEY-----",
    ],
)
def test_origin_unsafe_is_correctable_and_not_echoed(kind, unsafe):
    raw = request(kind)
    if kind == "intake":
        raw["original"] = unsafe
    elif kind == "sufficiency":
        raw["candidate"]["description"] = unsafe
    else:
        raw["invocation"]["input"] = {"secret-key": unsafe}
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap)
    assert unsafe not in wire(result).decode()
    assert all(gap.detail is None and public_path(gap.field) for gap in result.gaps)


@pytest.mark.parametrize("dynamic", ["/private/login", "f" * 64, "/evil~key", "x" * 5000, "title"])
def test_dynamic_native_keys_stop_at_root(dynamic):
    raw = request("sufficiency")
    raw["candidate"]["ask"]["effect"] = {dynamic: 1.5}
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap)
    assert result.gaps[0].field == "/candidate/ask/effect"
    assert dynamic not in wire(result).decode() or dynamic == "title"


@pytest.mark.parametrize("kind", KINDS)
def test_egress_no_private_locators_or_provenance(kind):
    req = ready(kind).value
    projected = egress(req, privacy())
    assert isinstance(projected, c.Valid)
    encoded = wire(projected.value).decode()
    for forbidden in (
        "/private",
        "private-source",
        "private-session",
        "creator_ref",
        "source_id",
        "login_dirs",
        "policy_revision",
        "workstream_id",
        "subject_id",
    ):
        assert forbidden not in encoded
    assert projected.value["request_id"] == "req"
    if kind == "sufficiency":
        assert cast(Any, projected.value["refs"])[0]["repo"] == {"label": "Product"}
    if kind == "permission":
        assert projected.value["input"] == {"path": "src/a.py"}
        assert projected.value["cwd"] == {"repo": {"label": "Product"}, "path": "src"}


def test_native_targets_omission_and_null_and_separate_id_grammars():
    raw = request("sufficiency")
    assert isinstance(prepare(raw, privacy()), c.Ready)
    raw["candidate"]["ask"]["targets"] = ["btq-short.1"]
    assert isinstance(prepare(seal(raw), privacy()), c.OriginGap)
    raw["candidate"]["ask"]["targets"] = ["btq-abcde"]
    raw["base_candidate_sha256"] = digest(raw["candidate"])
    assert isinstance(prepare(seal(raw), privacy()), c.Ready)
    for value in (None, [], ["btq-abcde", "btq-abcde"]):
        raw["candidate"]["ask"]["targets"] = value
        assert isinstance(prepare(seal(raw), privacy()), c.OriginGap)


def test_origin_candidate_unknown_keys_and_native_float_are_profile_gaps():
    raw = request("sufficiency")
    raw["candidate"]["ask"]["purpose"] = "Not a native field"
    assert isinstance(prepare(seal(raw), privacy()), c.OriginGap)
    del raw["candidate"]["ask"]["purpose"]
    raw["candidate"]["ask"]["effect"] = 1.5
    result = prepare(seal(raw), privacy())
    assert isinstance(result, c.OriginGap) and result.gaps[0].code == "profile"


@pytest.mark.parametrize("count", [1, 32])
def test_lint_fact_is_one_gap_regardless_of_private_diagnostics(count):
    diagnostics = ["private diagnostic" for _ in range(count)]
    assert native_lint_gap(bool(diagnostics)) == (c.Gap("native_lint", "/candidate/ask", None),)
    with pytest.raises(ValueError):
        native_lint_gap(cast(Any, diagnostics))


@given(st.dictionaries(st.text(max_size=12), st.integers(), max_size=8))
def test_canonical_json_has_no_newline_and_is_key_order_independent(value):
    encoded = canonical(value)
    assert encoded == canonical(dict(reversed(list(value.items()))))
    assert not encoded.endswith(b"\n")
    assert json.loads(encoded) == value


def test_host_unsafe_and_aggregate_oversize_never_origin_charge():
    raw = request()
    raw["public_bindings"]["target"]["repo"]["label"] = "/private/login"
    assert prepare(seal(raw), privacy()) == c.HostFailed("egress_unsafe")
    raw = request()
    context = request("sufficiency")["context"][0]
    for i in range(32):
        row = copy.deepcopy(context)
        row.update(source_id=f"source-{i}", text="x" * 4096)
        from heterodyne.gatekeeper.validate import text_hash

        row["content_id"] = text_hash(row["text"])
        raw["context"].append(row)
    assert prepare(seal(raw), privacy()) == c.HostFailed("egress_oversize")


def test_native_constants_match_pinned_existing_loader_capture():
    capture = json.loads(
        (Path(__file__).parent / "fixtures/gatekeeper/native-6fe-source-constants.json").read_text()
    )
    assert capture["revision"] == "6fe57598468ff53bf74dfd9e1cf181ad3177c3f1"
    assert capture["sha256"] == "ec2bc67b227326c80bf5d9a32eb661b220ea9af6942ef31bff04d8c06941bd2e"
    constants = capture["constants"]
    assert [field.name for field in msgspec.structs.fields(c.NativeAsk)] == constants["ASK_FIELDS"]
    assert [field.name for field in msgspec.structs.fields(c.FileRef)] == constants["REF_FIELDS"][1:]
    assert [ref.__struct_config__.tag for ref in (c.CommitRef, c.FileRef, c.RangeRef, c.BeadRef)] == (
        constants["REF_KINDS"]
    )
    native = re.compile(constants["BEAD_ID"])
    assert native.fullmatch("btq-abcde") and not native.fullmatch("btq-old.1")
    assert ready().value.open_tasks[0].id == "btq-old.1"  # broader admind identity retained


@pytest.mark.parametrize("kind", KINDS)
def test_parser_integer_conversion_limit_is_a_typed_refusal(kind):
    encoded = b'{"revision":' + b"9" * 5000 + b"}"
    assert decode_request(encoded, privacy()) == c.Invalid("request", "schema", "")
    assert decode_result(encoded, ready(kind).value, privacy()) == c.Invalid("result", "schema", "")
