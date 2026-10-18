"""Actual HIGH R1 regressions at the public two-argument and decoder seams."""

import copy
from typing import Any, cast

import msgspec
import pytest
from gatekeeper_fixtures import judgment, outcome, privacy, ready, request, seal, wire

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper import validate as v
from heterodyne.gatekeeper.project import intake_disposition, sufficiency_disposition


def forged(req, chosen) -> Any:
    value = msgspec.convert(judgment(req.value, chosen), type=v.RESULT_TYPES[v.request_kind(req.value)])
    return c.Completed(c.Valid(value))


@pytest.mark.parametrize("source,quote", [("missing", "Explicit"), ("private-source", "forged")])
def test_two_argument_enrichment_rechecks_whole_quote_and_source(source, quote):
    req = ready("sufficiency")
    selected = dict(
        status="enrich",
        additions=[dict(source_id=source, start=0, end=8, quote=quote)],
        reason="Exact evidence",
        gaps=[],
    )
    assert sufficiency_disposition(req, forged(req, selected)) == c.HoldUnavailable("bad_output")


def test_two_argument_intake_rechecks_dependency_subset():
    req = ready()
    selected = outcome()
    selected["dependencies"] = [dict(task_id="btq-other", type="blocks")]
    assert intake_disposition(req, forged(req, selected)) == c.RawConfirmProposal()


def test_two_argument_plain_or_corrupt_ready_is_invalid_host():
    req = ready("sufficiency")
    assert sufficiency_disposition(c.Ready(req.value), None) == c.HoldUnavailable("invalid_host")
    raw = request("sufficiency")
    raw["candidate"]["ask"]["refs"][0]["path"] = "docs/other.md"
    value = msgspec.convert(seal(raw), type=c.SufficiencyRequest)
    corrupt = msgspec.structs.replace(req, value=value)
    assert sufficiency_disposition(corrupt, None) == c.HoldUnavailable("invalid_host")


@pytest.mark.parametrize("mutate", ["hash", "directories"])
def test_standalone_result_requires_exact_verified_privacy(mutate):
    req = ready().value
    p = privacy()
    if mutate == "hash":
        p = msgspec.structs.replace(p, sha256="0" * 64)
    else:
        body = dict(format=1, login_dirs=[], rule_revision=v.RULE_REVISION)
        p = c.PrivacyContext(1, (), v.RULE_REVISION, v.digest(body))
    selected = outcome()
    selected["reason"] = "/private/login"
    assert v.decode_result(wire(judgment(req, selected)), req, p) == c.Invalid(
        "result", "binding", "/privacy_sha256"
    )
    assert v.check_result(judgment(req, selected), req, p) == c.Invalid(
        "result", "binding", "/privacy_sha256"
    )


@pytest.mark.parametrize("bad", ["x", True])
def test_wire_request_preserves_schema_location(bad):
    raw = request()
    raw["revision"] = bad
    assert v.decode_request(wire(raw), privacy()) == c.Invalid("request", "schema", "/revision")


def test_wire_and_egress_preserve_host_unsafe():
    raw = request()
    raw["public_bindings"]["target"]["repo"]["label"] = "/private/login"
    raw = seal(raw)
    assert v.decode_request(wire(raw), privacy()) == c.Invalid("request", "unsafe", "")
    value = msgspec.convert(raw, type=c.IntakeRequest)
    assert v.egress(value, privacy()) == c.Invalid("request", "unsafe", "")


def test_native_shared_dag_stops_at_global_budget(monkeypatch):
    calls = 0
    original = v._text

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        assert calls <= 513, "traversal exceeded the admitted global node budget"
        return original(*args, **kwargs)

    monkeypatch.setattr(v, "_text", counted)
    node: Any = ["safe"] * 32
    for _ in range(6):
        node = [node] * 32
    with pytest.raises(v.Fault, match="oversize"):
        v._native(node, privacy(), "/invocation/input")
    assert calls <= 512


@pytest.mark.parametrize("quote", ["x" * 1025, "/private/login", "forged"])
def test_used_evidence_fault_is_invalid_host_enrichment(quote):
    raw = request("sufficiency")
    raw["enrichment"] = dict(
        status="used",
        base_description=raw["candidate"]["description"],
        context_source_ids=["private-source"],
        additions=[dict(source_id="private-source", start=0, end=8, quote=quote)],
    )
    assert v.prepare(seal(raw), privacy()) == c.InvalidHost("/enrichment")


def test_context_complete_schema_location_is_not_context():
    raw = request()
    raw["context_complete"] = "yes"
    assert v.prepare(seal(raw), privacy()) == c.InvalidHost("/context_complete")


@pytest.mark.parametrize("field", ["label", "context"])
def test_surrogate_host_text_is_unsafe_before_hash(field):
    raw = request()
    if field == "label":
        raw["public_bindings"]["target"]["repo"]["label"] = "\ud800abc"
    else:
        item = copy.deepcopy(request("sufficiency")["context"][0])
        item["text"] = "\ud800abc"
        raw["context"] = [item]
    assert v.prepare(raw, privacy()) == c.HostFailed("egress_unsafe")


def test_ready_serialization_does_not_export_private_preparation_context():
    prepared = ready()
    assert set(msgspec.to_builtins(prepared)) == {"status", "value"}
    assert b"/private/login" not in msgspec.json.encode(prepared)
    decoded = msgspec.json.decode(msgspec.json.encode(prepared), type=c.Ready[c.IntakeRequest])
    assert intake_disposition(decoded, None) == c.HoldUnavailable("invalid_host")


def test_loaded_rule_drift_fails_binding(monkeypatch):
    from heterodyne.admind import redact

    monkeypatch.setattr(redact, "MAX_PASSES", redact.MAX_PASSES + 1)
    with pytest.raises(v.Fault, match="binding"):
        v.privacy_context(v.data(privacy()))


def test_loaded_kernel_replacement_fails_binding(monkeypatch):
    from heterodyne.admind import redact

    monkeypatch.setattr(redact, "_pass_with_dirs", lambda text, directories: text)
    with pytest.raises(v.Fault, match="binding"):
        v.privacy_context(v.data(privacy()))


def test_private_ready_is_not_serialized_or_reconstituted_by_conversion():
    req = ready("sufficiency")
    built = msgspec.to_builtins(req)
    assert set(built) == {"status", "value"}
    converted = msgspec.convert(built, type=c.Ready[c.SufficiencyRequest])
    assert v.prepared_privacy(converted) is None
    assert sufficiency_disposition(converted, None) == c.HoldUnavailable("invalid_host")
    with pytest.raises(AttributeError):
        cast(Any, req).value = req.value


@pytest.mark.parametrize("kind", [None, [], {}, 1])
def test_unknown_wire_kind_is_bounded_schema_error(kind):
    raw = request()
    raw["kind"] = kind
    assert v.decode_request(wire(raw), privacy()) == c.Invalid("request", "schema", "")


@pytest.mark.parametrize("fault", ["count", "gaps", "privacy"])
def test_two_argument_rechecks_other_model_invariants(fault):
    req = ready("sufficiency")
    selected = dict(
        status="enrich",
        additions=[dict(source_id="private-source", start=0, end=8, quote="Explicit")],
        reason="Exact evidence",
        gaps=[],
    )
    if fault == "count":
        selected["additions"] *= 9
    elif fault == "gaps":
        selected["gaps"] = [dict(code="semantic", field="why", detail="Missing")]
    else:
        selected["reason"] = "/private/login"
    assert sufficiency_disposition(req, forged(req, selected)) == c.HoldUnavailable("bad_output")
