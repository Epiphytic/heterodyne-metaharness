"""Advisory totality/precedence, quote proof and bounded transformations without effects."""

import copy
from typing import Any, cast

import msgspec
import pytest
from gatekeeper_fixtures import judgment, outcome, privacy, ready, request, seal, wire

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.project import disposition, groom_disposition
from heterodyne.gatekeeper.validate import (
    HOST_CODES,
    aliases,
    decode_result,
    egress,
    native_lint_gap,
    prepare,
    task_aliases,
)

KINDS = ("intake", "sufficiency", "permission")


def project(kind, selected=None, raw=None):
    prepared = ready(kind) if raw is None else prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready), prepared
    result = decode_result(wire(judgment(prepared.value, selected)), prepared.value, privacy())
    return disposition(kind, prepared, c.Completed(result), privacy())


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("hostcode", sorted(HOST_CODES))
def test_all_host_failures_preserve_provenance(kind, hostcode):
    req = ready(kind)
    before = wire(req)
    result = disposition(kind, req, c.HostFailed(hostcode), privacy())
    if kind == "intake" and hostcode in ("timeout", "unavailable"):
        assert result == c.RawUnreviewedProposal()
    else:
        cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
        assert result == cls(hostcode)
    assert wire(req) == before
    # Nonready host failures never become the ready intake raw fallback.
    result = disposition(kind, c.HostFailed(hostcode), None, privacy())
    cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    assert result == cls(hostcode)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("code", ("schema", "unsafe", "oversize", "binding", "invariant"))
def test_all_invalid_model_codes_no_origin_charge(kind, code):
    req = ready(kind)
    result = disposition(kind, req, c.Completed(c.Invalid("result", code, "")), privacy())
    expected = (
        c.RawConfirmProposal()
        if kind == "intake"
        else (c.EscalateProposal("bad_output") if kind == "permission" else c.HoldUnavailable("bad_output"))
    )
    assert result == expected
    result = disposition(kind, req, c.Completed(c.Invalid("request", code, "")), privacy())
    cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    assert result == cls("invalid_host")


@pytest.mark.parametrize("kind", KINDS)
def test_null_attempt_and_nonready_precedence(kind):
    req = ready(kind)
    cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    assert disposition(kind, req, None, privacy()) == cls("missing_attempt")
    gap = c.OriginGap((c.Gap("profile", "/original", None),))
    expected = {
        "intake": c.HoldInvalidInput(gap.gaps),
        "sufficiency": c.NeedsGrooming(gap.gaps),
        "permission": c.EscalateProposal("input_profile"),
    }[kind]
    assert disposition(kind, gap, None, privacy()) == expected
    for nonready in (gap, c.InvalidHost(""), c.HostFailed("timeout"), object()):
        assert disposition(kind, nonready, c.HostFailed("timeout"), privacy()) == cls("invalid_host")
    assert disposition(kind, c.InvalidHost(""), None, privacy()) == cls("invalid_host")


@pytest.mark.parametrize("kind", KINDS)
def test_unavailable_and_wrong_typed_host_tokens(kind):
    expected = (
        c.RawConfirmProposal()
        if kind == "intake"
        else (
            c.EscalateProposal("model_unavailable")
            if kind == "permission"
            else c.HoldUnavailable("model_unavailable")
        )
    )
    assert project(kind, dict(status="unavailable")) == expected
    cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    for attempt in (
        object(),
        c.HostFailed(cast(Any, "invented")),
        c.Completed(cast(Any, object())),
        c.Completed(c.Invalid(cast(Any, "bad"), "schema", "")),
    ):
        assert disposition(kind, ready(kind), attempt, privacy()) == cls("invalid_host")


@pytest.mark.parametrize("flag", tuple(outcome()["change_flags"]))
def test_every_intake_flag_is_material(flag):
    selected = outcome()
    selected["change_flags"][flag] = True
    assert project("intake", selected) == c.ConfirmMaterialProposal()


def test_intake_minor_material_and_host_incomplete():
    assert project("intake") == c.CreateMinorProposal()
    selected = outcome()
    selected["materiality"] = "material"
    assert project("intake", selected) == c.ConfirmMaterialProposal()
    raw = request()
    raw["context_complete"] = False
    assert project("intake", raw=raw) == c.ConfirmMaterialProposal()


@pytest.mark.parametrize("deps", [["missing"], ["01"], ["btq-old.1"], ["1", "1"]])
def test_intake_unknown_private_or_duplicate_alias_is_invalid(deps):
    selected = outcome()
    selected["dependencies"] = [dict(task_id=key, type="blocks") for key in deps]
    assert project("intake", selected) == c.RawConfirmProposal()


def test_intake_task_alias_resolves_only_frozen_map():
    selected = outcome()
    selected["dependencies"] = [dict(task_id="1", type="blocks")]
    assert project("intake", selected) == c.CreateMinorProposal()
    req = ready().value
    assert task_aliases(req) == {"btq-old.1": "1"}
    checked = decode_result(wire(judgment(req, selected)), req, privacy())
    assert isinstance(checked, c.Valid)
    assert isinstance(checked.value.outcome, c.Proposed)
    assert checked.value.outcome.dependencies[0].task_id == "btq-old.1"


@pytest.mark.parametrize("status", ["failed", "unavailable"])
def test_lint_precheck_refuses_model_attempt(status):
    raw = request("sufficiency")
    raw["lint"] = (
        dict(status="failed", gaps=msgspec.to_builtins(native_lint_gap(True)))
        if status == "failed"
        else dict(status="unavailable", reason_code="unavailable")
    )
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    expected = (
        c.NeedsGrooming(native_lint_gap(True)) if status == "failed" else c.HoldUnavailable("unavailable")
    )
    assert disposition("sufficiency", prepared, None, privacy()) == expected
    assert disposition("sufficiency", prepared, c.HostFailed("timeout"), privacy()) == c.HoldUnavailable(
        "invalid_host"
    )


def enrich(quote="Explicit", start=0, end=8, source="1"):
    return dict(
        status="enrich",
        additions=[dict(source_id=source, start=start, end=end, quote=quote)],
        reason="Useful exact context",
        gaps=[],
    )


def test_enrichment_exact_whole_candidate_and_preserves_ask_title():
    req = ready("sufficiency").value
    result = project("sufficiency", enrich())
    assert isinstance(result, c.EnrichmentProposal)
    assert result.candidate.title == req.candidate.title
    assert result.candidate.ask == req.candidate.ask
    assert result.candidate.description == req.candidate.description + (
        "\n\nGatekeeper-added context (source alias 1; ask reference 1):\nExplicit"
    )
    assert "private-source" not in result.candidate.description
    # Reassessment uses the same external submission identity and original-candidate hash.
    raw = request("sufficiency")
    raw["candidate"] = msgspec.to_builtins(result.candidate)
    raw["enrichment"] = dict(
        status="used",
        base_description=req.candidate.description,
        context_source_ids=["private-source"],
        additions=[dict(source_id="private-source", start=0, end=8, quote="Explicit")],
    )
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready), prepared
    assert project("sufficiency", raw=raw) == c.SufficientProposal()
    repeated = project("sufficiency", enrich(), raw=raw)
    assert repeated == c.NeedsGrooming((c.Gap("invariant", "/candidate/description", None),))
    raw["candidate"]["description"] += " forged"
    assert isinstance(prepare(seal(raw), privacy()), c.InvalidHost)


@pytest.mark.parametrize(
    "selected",
    [
        enrich("Wrong"),
        enrich(source="private-source"),
        enrich(source="01"),
        enrich(start=True),
        enrich(end=999),
        enrich(quote=""),
        enrich(quote="a" * 1025),
    ],
)
def test_invalid_quotes_offsets_and_aliases_hold_not_groom(selected):
    assert project("sufficiency", selected) == c.HoldUnavailable("bad_output")


def test_unicode_quote_and_duplicate_tuple():
    raw = request("sufficiency")
    text = raw["context"][0]["text"]
    start = text.index("α")
    selected = enrich("αβγ", start=start, end=start + 3)
    assert isinstance(project("sufficiency", selected), c.EnrichmentProposal)
    selected["additions"] *= 2
    assert project("sufficiency", selected) == c.HoldUnavailable("bad_output")


@pytest.mark.parametrize(
    "field", ["what", "effect", "excludes", "why", "context", "risks", "alternatives", "refs"]
)
def test_semantic_groom_paths(field: str):
    selected = dict(
        status="groom",
        gaps=[dict(field=field, detail="Missing decision context")],
        reason="Incomplete",
        additions=[],
    )
    result = project("sufficiency", selected)
    assert isinstance(result, c.NeedsGrooming)
    expected: dict[str, str] = {
        "what": "/candidate/title",
        "context": "/context",
        "alternatives": "/candidate/description",
    }
    assert result.gaps == (
        c.Gap("semantic", expected.get(field, "/candidate/ask/" + field), "Missing decision context"),
    )


@pytest.mark.parametrize("count", [0, 1, 2])
def test_groom_counter_is_injected_saturating_and_pure(count):
    expected = c.CannotApproveNotice() if count == 2 else c.ReturnToOrigin()
    assert groom_disposition(count) == expected
    assert groom_disposition(count) == expected  # duplicate/outage does not increment anything


@pytest.mark.parametrize("bad", [True, -1, 3, 1.0, "1"])
def test_groom_rejects_unrepresentable_counters(bad):
    with pytest.raises(ValueError):
        groom_disposition(bad)


@pytest.mark.parametrize("elapsed", [59999, 60000, 60001])
def test_permission_total_budget_precedes_result(elapsed):
    raw = request("permission")
    raw["elapsed_ms"] = elapsed
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, c.Ready)
    if elapsed >= 60000:
        assert disposition("permission", prepared, None, privacy()) == c.EscalateProposal("deadline")
        assert disposition("permission", prepared, c.HostFailed("timeout"), privacy()) == c.EscalateProposal(
            "invalid_host"
        )
    else:
        assert project("permission", raw=raw) == c.AllowProposal()


@pytest.mark.parametrize("budget", [0, 60001, None, True, "60000"])
def test_bad_permission_policy_is_host_only(budget):
    raw = request("permission")
    raw["effective_budget_ms"] = budget
    assert prepare(seal(raw), privacy()) == c.HostFailed("unreadable_policy")


@pytest.mark.parametrize(
    "status,expected",
    [
        ("allow", c.AllowProposal()),
        ("deny", c.DenyProposal()),
        ("escalate", c.EscalateProposal("model_escalate")),
    ],
)
def test_permission_advisory_outcomes(status, expected):
    assert project("permission", dict(status=status, reason="Explicit advice")) == expected


@pytest.mark.parametrize("mutation", ["cwd", "tool", "session", "input", "policy"])
def test_permission_exact_invocation_binding(mutation):
    raw = request("permission")
    if mutation == "cwd":
        raw["invocation"]["cwd_id"] = "other"
    elif mutation in ("tool", "session"):
        raw["subject"]["tool_name" if mutation == "tool" else "session_ref"] = "other"
    elif mutation == "input":
        raw["invocation"]["input"] = {"path": "other.py"}
    else:
        raw["policy_revision"]["id"] = "other"
    assert isinstance(prepare(seal(raw), privacy()), c.InvalidHost)


def test_context_and_task_aliases_are_independent_order_stable():
    raw = request()
    raw["open_tasks"].insert(0, dict(id="aaa-older", title="Other work", description_digest="e" * 64))
    a = prepare(seal(raw), privacy())
    raw["open_tasks"].reverse()
    b = prepare(seal(raw), privacy())
    assert isinstance(a, c.Ready) and isinstance(b, c.Ready)
    assert a.value.snapshot_sha256 != b.value.snapshot_sha256
    assert isinstance(a.value, c.IntakeRequest) and isinstance(b.value, c.IntakeRequest)
    assert task_aliases(a.value) == task_aliases(b.value)
    source = request("sufficiency")["context"][0]
    raw["context"] = [copy.deepcopy(source), copy.deepcopy(source)]
    raw["context"][0]["source_id"] = "2"
    raw["context"][1]["source_id"] = "1"
    a = prepare(seal(raw), privacy())
    assert isinstance(a, c.Ready)
    assert aliases(a.value) == {"1": "1", "2": "2"}
    projected = egress(a.value, privacy())
    assert isinstance(projected, c.Valid)
    assert cast(Any, projected.value["context"])[0]["alias"] == "2"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "gap",
    [
        c.Gap("profile", "/private/login", None),
        c.Gap("unsafe", "/original", "private diagnostic"),
        c.Gap("schema", "/original", "a" * 64),
    ],
)
def test_forged_origin_tokens_do_not_release_private_diagnostics(kind, gap):
    cls = c.EscalateProposal if kind == "permission" else c.HoldUnavailable
    assert disposition(kind, c.OriginGap((gap,)), None) == cls("invalid_host")


def test_consumer_contract_grooming_duplicate_outage_and_origin_hold():
    # Recording future consumer, not a runtime route or durable accounting proof.
    counters = {"submission": 0}
    seen: set[tuple[str, int]] = set()
    routed = []
    for revision, origin_available, failure in [
        (1, True, "groom"),
        (1, True, "groom"),
        (2, True, "outage"),
        (2, False, "groom"),
        (3, True, "groom"),
        (4, True, "groom"),
    ]:
        key = ("submission", revision)
        if key in seen or failure == "outage" or not origin_available or counters["submission"] == 2:
            continue
        seen.add(key)
        counters["submission"] += 1
        routed.append(groom_disposition(counters["submission"]))
    assert counters == {"submission": 2}
    assert routed == [c.ReturnToOrigin(), c.CannotApproveNotice()]
