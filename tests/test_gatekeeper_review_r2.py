"""Bounded public-seam regressions from the actual second HIGH review."""

from typing import Any

import msgspec
import pytest
from gatekeeper_fixtures import judgment, privacy, ready, request, wire

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper import validate as v
from heterodyne.gatekeeper.project import disposition, intake_disposition, sufficiency_disposition


def shared() -> Any:
    node: Any = ["safe"] * 32
    for _ in range(7):
        node = [node] * 32
    return node


def test_shared_graph_is_bounded_before_to_builtins(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unbounded graph reached converter")

    monkeypatch.setattr(msgspec, "to_builtins", forbidden)
    with pytest.raises(v.Fault, match="oversize"):
        v.data(shared())


def test_forged_shared_model_and_origin_tokens_fail_closed():
    req = ready("sufficiency")
    assert sufficiency_disposition(req, c.Completed(c.Valid(shared()))) == c.HoldUnavailable("bad_output")
    assert intake_disposition(c.OriginGap(shared()), None) == c.HoldUnavailable("invalid_host")


@pytest.mark.parametrize("code", [[], {}, None, 1])
def test_unhashable_or_unknown_host_code_never_raises(code):
    assert intake_disposition(c.HostFailed(code), None) == c.HoldUnavailable("invalid_host")
    assert intake_disposition(ready(), c.HostFailed(code)) == c.HoldUnavailable("invalid_host")


def test_forged_typed_host_fields_do_not_escape_prepare_or_egress():
    req = ready("sufficiency").value
    candidate = msgspec.structs.replace(req.candidate, title=None)
    bad = msgspec.structs.replace(req, candidate=candidate)
    assert isinstance(v.prepare(bad, privacy()), (c.InvalidHost, c.OriginGap))
    assert isinstance(v.egress(bad, privacy()), c.Invalid)
    assert disposition("sufficiency", c.Ready(bad), None, privacy()) == c.HoldUnavailable("invalid_host")
    intake = msgspec.structs.replace(ready().value, open_tasks=(object(),))
    assert isinstance(v.prepare(intake, privacy()), (c.InvalidHost, c.OriginGap))


def test_egress_is_detached_from_mutable_host_input():
    req = ready("permission").value
    result = v.egress(req, privacy())
    assert isinstance(result, c.Valid)
    req.invocation.input["path"] = "/private/login"
    assert result.value["input"] == {"path": "src/a.py"}
    assert result.value["input"] is not req.invocation.input


def test_normalized_response_enforces_whole_response_budget():
    req = ready("sufficiency")
    body = judgment(
        req.value,
        dict(
            status="groom",
            reason="Missing context",
            additions=[],
            gaps=[dict(code="semantic", field="why", detail="word " * 800)] * 64,
        ),
    )
    assert v.check_result(body, req.value, privacy()) == c.Invalid("result", "oversize", "")
    assert sufficiency_disposition(req, c.Completed(c.Valid(body))) == c.HoldUnavailable("bad_output")


@pytest.mark.parametrize("path", [("snapshot_sha256",), ("policy_revision", "sha256")])
def test_schema_path_retains_digit_field_names(path):
    raw = request()
    target = raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = 5
    assert v.decode_request(wire(raw), privacy()) == c.Invalid("request", "schema", "/" + "/".join(path))


def test_permission_missing_host_invocation_is_invalid_host():
    raw = request("permission")
    raw["invocation"] = None
    assert v.prepare(raw, privacy()) == c.InvalidHost("/invocation")


def test_actual_validation_redactor_binding_is_checked(monkeypatch):
    monkeypatch.setattr(v, "redact_with_dirs", lambda text, dirs: text)
    assert v.prepare(request(), privacy()) == c.InvalidHost("/privacy_sha256")


def test_cyclic_model_and_origin_records_fail_closed():
    cycle = []
    cycle.append(cycle)
    req = ready("sufficiency")
    assert sufficiency_disposition(req, c.Completed(c.Valid(cycle))) == c.HoldUnavailable("bad_output")
    assert intake_disposition(c.OriginGap(cycle), None) == c.HoldUnavailable("invalid_host")


def test_dict_subclass_cannot_smuggle_an_egress_mapping():
    class Hidden(dict):
        def __iter__(self):
            return iter(())

    req = ready("permission").value
    invocation = msgspec.structs.replace(req.invocation, input=Hidden(path="/private/login"))
    forged = msgspec.structs.replace(req, invocation=invocation)
    assert isinstance(v.egress(forged, privacy()), c.Invalid)
