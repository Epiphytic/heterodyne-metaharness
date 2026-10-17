"""Total, inert proposal selection. No proposal grants permission or mutates native state.

The host must apply deterministic hard-deny/operator-only rules before considering a
permission port. This module deliberately exposes no execution/authorization hook.
"""

from typing import Literal, cast

import msgspec

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import (
    HOST_CODES,
    aliases,
    appended,
    canonical,
    check_result,
    data,
    native_lint_gap,
    prepare,
    prepared_privacy,
    public_path,
    request_kind,
)


def _hold(kind: str, reason: c.ReasonCode) -> c.Projection:
    return c.EscalateProposal(reason) if kind == "permission" else c.HoldUnavailable(reason)


def _nonready(kind: str, prepared: object) -> c.Projection:
    if isinstance(prepared, c.OriginGap):
        try:
            prepared = msgspec.convert(data(prepared), type=c.OriginGap, strict=True)
            if not 1 <= len(prepared.gaps) <= 64 or any(
                not public_path(gap.field) or gap.detail is not None for gap in prepared.gaps
            ):
                return _hold(kind, "invalid_host")
        except (msgspec.ValidationError, TypeError, ValueError, UnicodeError, RecursionError):
            return _hold(kind, "invalid_host")
        if kind == "intake":
            return c.HoldInvalidInput(prepared.gaps)
        if kind == "sufficiency":
            return c.NeedsGrooming(prepared.gaps)
        return c.EscalateProposal("input_profile")
    if isinstance(prepared, c.HostFailed) and prepared.reason_code in HOST_CODES:
        return _hold(kind, prepared.reason_code)
    return _hold(kind, "invalid_host")


def _precheck(request: c.AnyRequest) -> c.Projection | None:
    if isinstance(request, c.PermissionRequest) and request.elapsed_ms >= request.effective_budget_ms:
        return c.EscalateProposal("deadline")
    if isinstance(request, c.SufficiencyRequest):
        if isinstance(request.lint, c.Failed):
            return c.NeedsGrooming(native_lint_gap(True))
        if isinstance(request.lint, c.UnavailableLint):
            return c.HoldUnavailable(request.lint.reason_code)
    return None


def _sufficiency(request: c.SufficiencyRequest, outcome: c.Sufficient | c.Enrich | c.Groom) -> c.Projection:
    if isinstance(outcome, c.Sufficient):
        return c.SufficientProposal()
    if isinstance(outcome, c.Groom):
        fields = {"what": "/candidate/title", "context": "/context", "alternatives": "/candidate/description"}
        return c.NeedsGrooming(
            tuple(
                c.Gap("semantic", fields.get(gap.field, "/candidate/ask/" + gap.field), gap.detail)
                for gap in outcome.gaps
            )
        )
    gap = c.NeedsGrooming((c.Gap("invariant", "/candidate/description", None),))
    if isinstance(request.enrichment, c.Used):
        return gap
    suffix = appended(request, outcome.additions)
    description = request.candidate.description + suffix
    if len(suffix.encode("utf-8")) > 8192 or len(description.encode("utf-8")) > 16384:
        return c.NeedsGrooming((c.Gap("oversize", "/candidate/description", None),))
    candidate = msgspec.structs.replace(request.candidate, description=description)
    evidence = c.Used(request.candidate.description, tuple(sorted(aliases(request))), outcome.additions)
    rebuilt = msgspec.structs.replace(request, candidate=candidate, enrichment=evidence)
    if len(canonical(rebuilt)) > 131072:
        return c.NeedsGrooming((c.Gap("oversize", "/candidate/description", None),))
    return c.EnrichmentProposal(candidate)


def _valid(request: c.AnyRequest, result: c.AnyJudgment) -> c.Projection:
    outcome = result.outcome
    if isinstance(outcome, c.ModelUnavailable):
        return (
            c.RawConfirmProposal()
            if isinstance(request, c.IntakeRequest)
            else _hold(request_kind(request), "model_unavailable")
        )
    if isinstance(request, c.IntakeRequest) and isinstance(outcome, c.Proposed):
        flags = msgspec.to_builtins(outcome.change_flags)
        material = outcome.materiality == "material" or any(flags.values()) or not request.context_complete
        return c.ConfirmMaterialProposal() if material else c.CreateMinorProposal()
    if isinstance(request, c.SufficiencyRequest) and isinstance(outcome, (c.Sufficient, c.Enrich, c.Groom)):
        return _sufficiency(request, outcome)
    if isinstance(outcome, c.Allow):
        return c.AllowProposal()
    if isinstance(outcome, c.Deny):
        return c.DenyProposal()
    return c.EscalateProposal("model_escalate")


def disposition(
    kind: Literal["intake", "sufficiency", "permission"],
    prepared: object,
    attempt: object | None,
    privacy: c.PrivacyContext | None = None,
) -> c.Projection:
    """Select an advisory outcome with host/precheck precedence before model results.

    Invalid supposedly typed host tokens fail closed. No clocks, retries, persistence,
    model calls, native calls, permission floors or approval decisions happen here.
    """
    if kind not in ("intake", "sufficiency", "permission"):
        return c.HoldUnavailable("invalid_host")
    if not isinstance(prepared, c.Ready):
        return _hold(kind, "invalid_host") if attempt is not None else _nonready(kind, prepared)
    request = cast(c.Ready[object], prepared).value
    if not isinstance(request, (c.IntakeRequest, c.SufficiencyRequest, c.PermissionRequest)):
        return _hold(kind, "invalid_host")
    if request_kind(request) != kind:
        return _hold(kind, "invalid_host")
    privacy = privacy if privacy is not None else prepared_privacy(cast(object, prepared))
    if privacy is None:
        return _hold(kind, "invalid_host")
    verified = prepare(request, privacy)
    if not isinstance(verified, c.Ready):
        return _hold(kind, "invalid_host")
    request = verified.value
    precheck = _precheck(request)
    if precheck is not None:
        return _hold(kind, "invalid_host") if attempt is not None else precheck
    if attempt is None:
        return _hold(kind, "missing_attempt")
    if isinstance(attempt, c.HostFailed):
        if attempt.reason_code not in HOST_CODES:
            return _hold(kind, "invalid_host")
        if kind == "intake" and attempt.reason_code in ("timeout", "unavailable"):
            return c.RawUnreviewedProposal()
        return _hold(kind, attempt.reason_code)
    if not isinstance(attempt, c.Completed):
        return _hold(kind, "invalid_host")
    result = cast(c.Completed[object], attempt).result
    if isinstance(result, c.Invalid):
        if result.stage != "result" or result.code not in (
            "schema",
            "unsafe",
            "oversize",
            "binding",
            "invariant",
        ):
            return _hold(kind, "invalid_host")
        return c.RawConfirmProposal() if kind == "intake" else _hold(kind, "bad_output")
    if type(result) is not c.Valid:
        return _hold(kind, "invalid_host")
    checked = check_result(result.value, request, privacy)
    if isinstance(checked, c.Invalid):
        return c.RawConfirmProposal() if kind == "intake" else _hold(kind, "bad_output")
    return _valid(request, checked.value)


def groom_disposition(groom_failures_after: int) -> c.ReturnToOrigin | c.CannotApproveNotice:
    """Caller owns distinct failed-round accounting; outages/duplicates do not increment."""
    if type(groom_failures_after) is not int or not 0 <= groom_failures_after <= 2:
        raise ValueError("groom-failures-after")
    return c.CannotApproveNotice() if groom_failures_after == 2 else c.ReturnToOrigin()


def intake_disposition(
    prepared: c.Prepared[c.IntakeRequest], attempt: c.JudgmentAttempt[c.IntakeJudgment] | None
) -> c.Projection:
    """Consume validated host preparation/check records, never raw model objects."""
    return disposition("intake", prepared, attempt)


def sufficiency_disposition(
    prepared: c.Prepared[c.SufficiencyRequest], attempt: c.JudgmentAttempt[c.SufficiencyJudgment] | None
) -> c.Projection:
    """Consume validated records; enrichment is one atomic advisory transform."""
    return disposition("sufficiency", prepared, attempt)


def permission_disposition(
    prepared: c.Prepared[c.PermissionRequest], attempt: c.JudgmentAttempt[c.PermissionJudgment] | None
) -> c.Projection:
    """Advisory only; deterministic hard-deny/operator-only floors precede this port."""
    return disposition("permission", prepared, attempt)
