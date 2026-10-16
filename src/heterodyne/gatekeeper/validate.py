"""Bounded, pure Plan7A checks. Caller-supplied host facts never authenticate themselves.

prepare consumes host-assembled values, validating origin-owned fields before aggregate
host egress. decode_request separately checks the bounded wire grammar. Neither resolves
Git/native refs, reads configuration or invokes a model.
"""

import hashlib
import json
import re
from typing import Literal, Never, cast

import msgspec

from heterodyne.admind.redact import redact_with_dirs
from heterodyne.gatekeeper import contracts as c

RULE_REVISION = "07637745fe75dc614109a0b8ba72d3489f262a97"
_ID = re.compile(r"[A-Za-z0-9_:.+\-]{1,128}")
_BEAD = re.compile(r"[a-z0-9]{1,16}-[a-z0-9.]{1,32}")
_TARGET = re.compile(r"[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
HOST_CODES = {
    "timeout",
    "unavailable",
    "unreadable_policy",
    "stale_digest",
    "egress_unsafe",
    "egress_oversize",
}
REQUEST_TYPES = {
    "intake": c.IntakeRequest,
    "sufficiency": c.SufficiencyRequest,
    "permission": c.PermissionRequest,
}
RESULT_TYPES = {
    "intake": c.IntakeJudgment,
    "sufficiency": c.SufficiencyJudgment,
    "permission": c.PermissionJudgment,
}
# Public paths are generated locally, never copied from rejected JSON keys.
_FIELDS = frozenset(
    "format kind request_id workstream_id subject_id revision policy_revision id sha256 "
    "privacy_sha256 context public_bindings snapshot_sha256 source_id content_id text ref "
    "resolved_id repo path target ref_repositories ref_index cwd label workstream original "
    "origin event_ref creator_ref session_ref context_complete open_tasks title description_digest "
    "workstream_id repo_id approval_id native_context_digest lint gaps status reason_code candidate "
    "description ask effect excludes why risks refs targets resolved_refs submission_id "
    "submission_revision base_candidate_sha256 enrichment base_description context_source_ids "
    "additions start end quote subject task_id tool_name intent invocation cwd_id input "
    "effective_budget_ms elapsed_ms outcome materiality acceptance dependencies type change_flags "
    "target_changed requirements_changed question_to_action destructive outward_facing "
    "context_incomplete reason field detail code".split()
)


class Fault(ValueError):
    """Safe content-free local diagnostic; private rejected bytes are never interpolated."""

    def __init__(self, code: c.CheckCode | Literal["profile"], field: str = ""):
        self.code: c.CheckCode | Literal["profile"] = code
        self.field = field
        super().__init__(code)


def fail(code: c.CheckCode | Literal["profile"], field: str = "") -> Never:
    raise Fault(code, field)


def data(value: object) -> object:
    return msgspec.to_builtins(value)


def canonical(value: object) -> bytes:
    return json.dumps(
        data(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _match(value: str, pattern: re.Pattern[str], field: str) -> None:
    if not pattern.fullmatch(value):
        fail("schema", field)


def _integer(value: int, maximum: int, field: str, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        fail("schema", field)


def _count(value: tuple[object, ...], maximum: int, field: str, minimum: int = 0) -> None:
    if not minimum <= len(value) <= maximum:
        fail("oversize" if len(value) > maximum else "invariant", field)


def _text(value: str, privacy: c.PrivacyContext, field: str, limit: int = 4096) -> None:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        fail("unsafe", field)
    if not value.strip():
        fail("schema", field)
    if size > limit:
        fail("oversize", field)
    if redact_with_dirs(value, privacy.login_dirs) != value:
        fail("unsafe", field)


def _title(value: str, privacy: c.PrivacyContext, field: str) -> None:
    _text(value, privacy, field)
    if len(value) > 200:
        fail("oversize", field)


def _locator(value: str, field: str) -> None:
    try:
        if not value or len(value.encode("utf-8")) > 4096:
            fail("schema", field)
    except UnicodeError:
        fail("unsafe", field)
    if any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in value):
        fail("unsafe", field)


def privacy_context(value: object) -> c.PrivacyContext:
    context = msgspec.convert(value, type=c.PrivacyContext, strict=True)
    if context.rule_revision != RULE_REVISION:
        fail("binding")
    if len(context.login_dirs) > 16 or tuple(sorted(set(context.login_dirs))) != context.login_dirs:
        fail("invariant")
    for directory in context.login_dirs:
        _locator(directory, "")
        if len(directory.encode()) > 512:
            fail("oversize")
    body = cast(dict[str, object], data(context))
    del body["sha256"]
    if context.sha256 != digest(body):
        fail("binding")
    return context


def public_path(value: str) -> bool:
    if len(value.encode("utf-8")) > 512:
        return False
    if value == "":
        return True
    return value.startswith("/") and all(
        p in _FIELDS or (p.isascii() and p.isdecimal() and str(int(p)) == p and int(p) <= 511)
        for p in value[1:].split("/")
    )


def schema_path(error: msgspec.ValidationError, prefix: str = "") -> str:
    """Project compiler-owned schema locations; rejected values/unknown keys never leave.

    msgspec's diagnostic text stays private. Only its final structural location is
    interpreted, one fixed field/array index at a time, stopping at NativeValue roots.
    """
    found = re.search(r" - at `(?P<path>\$[^`]*)`$", str(error))
    if found is None:
        return prefix
    location = found.group("path")[1:]
    result = prefix
    while location:
        if result in (
            "/candidate/ask/effect",
            "/candidate/ask/excludes",
            "/candidate/ask/why",
            "/candidate/ask/risks",
            "/invocation/input",
        ):
            break
        part = re.match(r"(?:\.([a-z_]+)|\[([0-9]+)\])", location)
        if part is None:
            break
        segment = part.group(1) or part.group(2)
        candidate = result + "/" + segment
        if not public_path(candidate):
            break
        result = candidate
        location = location[part.end() :]
    return result


def _relative(value: str, privacy: c.PrivacyContext, field: str, *, dot: bool = False) -> None:
    _text(value, privacy, field)
    if dot and value == ".":
        return
    if value.startswith(("/", "~")) or "\\" in value or any(p in ("", ".", "..") for p in value.split("/")):
        fail("profile", field)


def _native(value: object, privacy: c.PrivacyContext, field: str, depth: int = 0) -> int:
    if isinstance(value, (list, tuple, dict)):
        if depth >= 8 or len(cast(list[object] | tuple[object, ...] | dict[object, object], value)) > 32:
            fail("oversize", field)
        children: list[object]
        if isinstance(value, dict):
            items = cast(dict[object, object], value)
            for key in items:
                if type(key) is not str or not _ID.fullmatch(key):
                    fail("profile", field)
                _text(key, privacy, field)
            children = list(items.values())
        else:
            children = list(cast(list[object], value))
        count = 1 + sum(_native(child, privacy, field, depth + 1) for child in children)
    elif type(value) is str:
        _text(value, privacy, field)
        count = 1
    elif type(value) is int:
        if not -(2**63) <= value < 2**63:
            fail("profile", field)
        count = 1
    elif type(value) in (bool, type(None)):
        count = 1
    else:
        fail("profile", field)
    if count > 512:
        fail("oversize", field)
    return count


def _ref(ref: c.NativeRef, field: str) -> None:
    if isinstance(ref, c.BeadRef):
        _match(ref.id, _BEAD, field + "/id")
        return
    if isinstance(ref, c.RangeRef):
        if not re.fullmatch(r"[0-9a-f]{40}\.\.[0-9a-f]{40}", ref.id):
            fail("schema", field + "/id")
    else:
        _match(ref.id, _SHA, field + "/id")
    _locator(ref.repo, field + "/repo")
    if isinstance(ref, c.FileRef):
        _locator(ref.path, field + "/path")


def _resolved(ref: c.NativeRef, value: str | None, field: str) -> None:
    if value is not None:
        if isinstance(ref, c.BeadRef):
            _match(value, _DIGEST, field)
        elif isinstance(ref, c.FileRef):
            _match(value, _SHA, field)
        elif value != ref.id:
            fail("binding", field)


def _candidate(value: c.Candidate, privacy: c.PrivacyContext) -> None:
    _title(value.title, privacy, "/candidate/title")
    _text(value.description, privacy, "/candidate/description", 16384)
    for key in ("effect", "excludes", "why", "risks"):
        v = getattr(value.ask, key)
        _native(v, privacy, "/candidate/ask/" + key)
        if not v:
            fail("invariant", "/candidate/ask/" + key)
    _count(value.ask.refs, 32, "/candidate/ask/refs", 1)
    for i, ref in enumerate(value.ask.refs):
        path = f"/candidate/ask/refs/{i}"
        _ref(ref, path)
        if isinstance(ref, c.FileRef):
            _relative(ref.path, privacy, path + "/path")
    if value.ask.targets is not msgspec.UNSET:
        _count(value.ask.targets, 32, "/candidate/ask/targets", 1)
        if len(set(value.ask.targets)) != len(value.ask.targets):
            fail("invariant", "/candidate/ask/targets")
        for i, target in enumerate(value.ask.targets):
            _match(target, _TARGET, f"/candidate/ask/targets/{i}")


def _origin_fields(raw: dict[str, object], kind: str, privacy: c.PrivacyContext) -> None:
    if kind == "intake":
        original = raw.get("original")
        if not isinstance(original, str):
            fail("schema", "/original")
        _text(original, privacy, "/original", 16384)
    elif kind == "sufficiency":
        try:
            candidate = msgspec.convert(raw.get("candidate"), type=c.Candidate, strict=True)
        except msgspec.ValidationError as error:
            code = "profile" if "unknown field" in str(error) else "schema"
            fail(code, schema_path(error, "/candidate"))
        _candidate(candidate, privacy)
    elif kind == "permission":
        intent, invocation = raw.get("intent"), raw.get("invocation")
        if not isinstance(intent, str):
            fail("schema", "/intent")
        _text(intent, privacy, "/intent")
        if not isinstance(invocation, dict) or "input" not in invocation:
            fail("schema", "/invocation/input")
        _native(cast(dict[str, object], invocation)["input"], privacy, "/invocation/input")


def aliases(request: c.AnyRequest) -> dict[str, str]:
    return {key: str(i + 1) for i, key in enumerate(sorted(item.source_id for item in request.context))}


def native_lint_gap(failed: bool) -> tuple[c.Gap, ...]:
    """Only a trusted nonempty-diagnostics fact is accepted; never parse diagnostic prose."""
    if type(failed) is not bool:
        raise ValueError("native-lint-fact")
    return (c.Gap("native_lint", "/candidate/ask", None),) if failed else ()


def _origin(value: c.Origin) -> None:
    _match(value.event_ref, _ID, "/origin/event_ref")
    _match(value.creator_ref, _ID, "/origin/creator_ref")
    if value.session_ref is not None:
        _match(value.session_ref, _ID, "/origin/session_ref")


def _bindings(request: c.AnyRequest, privacy: c.PrivacyContext) -> None:
    bindings = request.public_bindings
    if isinstance(request, c.IntakeRequest):
        if bindings.target is None or bindings.cwd is not None or bindings.ref_repositories:
            fail("invariant", "/public_bindings")
        _text(bindings.target.workstream, privacy, "/public_bindings/target/workstream")
        _text(bindings.target.repo.label, privacy, "/public_bindings/target/repo/label")
    elif isinstance(request, c.PermissionRequest):
        if bindings.target is not None or bindings.cwd is None or bindings.ref_repositories:
            fail("invariant", "/public_bindings")
        _text(bindings.cwd.repo.label, privacy, "/public_bindings/cwd/repo/label")
        _relative(bindings.cwd.path, privacy, "/public_bindings/cwd/path", dot=True)
    else:
        if bindings.target is not None or bindings.cwd is not None:
            fail("invariant", "/public_bindings")
        expected = {i for i, ref in enumerate(request.candidate.ask.refs) if not isinstance(ref, c.BeadRef)}
        actual = [row.ref_index for row in bindings.ref_repositories]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            fail("invariant", "/public_bindings/ref_repositories")
        for row in bindings.ref_repositories:
            _integer(row.ref_index, 31, "/public_bindings/ref_repositories")
            _text(row.repo.label, privacy, "/public_bindings/ref_repositories")


def _contexts(request: c.AnyRequest, privacy: c.PrivacyContext) -> None:
    _count(request.context, 32, "/context")
    if len({item.source_id for item in request.context}) != len(request.context):
        fail("invariant", "/context")
    for item in request.context:
        _match(item.source_id, _ID, "/context")
        _text(item.text, privacy, "/context")
        _match(item.content_id, _DIGEST, "/context")
        _ref(item.ref, "/context")
        _resolved(item.ref, item.resolved_id, "/context")
        if item.content_id != text_hash(item.text):
            fail("binding", "/context")
        if (
            (item.kind == "task" and not isinstance(item.ref, c.BeadRef))
            or (item.kind == "doc" and not isinstance(item.ref, (c.FileRef, c.CommitRef)))
            or (item.kind == "diff" and not isinstance(item.ref, c.RangeRef))
        ):
            fail("invariant", "/context")
        if isinstance(request, c.SufficiencyRequest):
            if (
                item.kind not in ("doc", "excerpt")
                or not isinstance(item.ref, c.FileRef)
                or item.ref not in request.candidate.ask.refs
            ):
                fail("invariant", "/context")
            row = next(row for row in request.resolved_refs if row.ref == item.ref)
            payload = item.text.encode("utf-8")
            blob = hashlib.sha1(
                b"blob " + str(len(payload)).encode() + b"\0" + payload, usedforsecurity=False
            ).hexdigest()
            if blob != item.resolved_id or row.resolved_id != item.resolved_id:
                fail("binding", "/context")


def _additions(
    request: c.SufficiencyRequest, additions: tuple[c.Addition, ...], privacy: c.PrivacyContext
) -> None:
    _count(additions, 8, "/outcome/additions", 1)
    seen: set[tuple[str, int, int]] = set()
    sources = {item.source_id: item for item in request.context}
    for i, addition in enumerate(additions):
        path = f"/outcome/additions/{i}"
        _integer(addition.start, 2147483647, path + "/start")
        _integer(addition.end, 2147483647, path + "/end")
        _text(addition.quote, privacy, path + "/quote", 1024)
        source = sources.get(addition.source_id)
        key = (addition.source_id, addition.start, addition.end)
        if source is None or key in seen:
            fail("invariant", path)
        if not 0 <= addition.start < addition.end <= len(source.text):
            fail("invariant", path)
        if addition.quote != source.text[addition.start : addition.end]:
            fail("invariant", path)
        seen.add(key)


def appended(request: c.SufficiencyRequest, additions: tuple[c.Addition, ...]) -> str:
    sources = {item.source_id: item for item in request.context}
    mapping = aliases(request)
    pieces: list[str] = []
    for addition in additions:
        source = sources[addition.source_id]
        position = request.candidate.ask.refs.index(source.ref) + 1
        pieces.append(
            f"\n\nGatekeeper-added context (source alias {mapping[source.source_id]}; "
            f"ask reference {position}):\n{addition.quote}"
        )
    return "".join(pieces)


def _sufficiency(request: c.SufficiencyRequest, privacy: c.PrivacyContext) -> None:
    _match(request.approval_id, _BEAD, "/approval_id")
    if request.subject_id != request.approval_id:
        fail("binding", "/subject_id")
    _origin(request.origin)
    _match(request.submission_id, _ID, "/submission_id")
    _integer(request.submission_revision, 2147483647, "/submission_revision", 1)
    if request.native_context_digest is not None:
        _match(request.native_context_digest, _DIGEST, "/native_context_digest")
    if len(request.resolved_refs) != len(request.candidate.ask.refs):
        fail("invariant", "/resolved_refs")
    for row, ref in zip(request.resolved_refs, request.candidate.ask.refs, strict=True):
        if row.ref != ref:
            fail("binding", "/resolved_refs")
        _resolved(ref, row.resolved_id, "/resolved_refs")
    if isinstance(request.lint, c.Passed):
        if (
            request.lint.gaps
            or request.native_context_digest is None
            or any(row.resolved_id is None for row in request.resolved_refs)
        ):
            fail("invariant", "/lint")
    elif isinstance(request.lint, c.Failed) and request.lint.gaps != native_lint_gap(True):
        fail("invariant", "/lint")
    original = request.candidate
    if isinstance(request.enrichment, c.Used):
        used = request.enrichment
        _text(used.base_description, privacy, "/enrichment/base_description", 16384)
        if used.context_source_ids != tuple(sorted(aliases(request))):
            fail("invariant", "/enrichment")
        _additions(request, used.additions, privacy)
        suffix = appended(request, used.additions)
        if len(suffix.encode()) > 8192 or request.candidate.description != used.base_description + suffix:
            fail("invariant", "/enrichment")
        original = msgspec.structs.replace(request.candidate, description=used.base_description)
    if digest(original) != request.base_candidate_sha256:
        fail("binding", "/enrichment")


def _host(request: c.AnyRequest, privacy: c.PrivacyContext) -> None:
    for name in ("request_id", "workstream_id", "subject_id"):
        _match(getattr(request, name), _ID, "/" + name)
    _integer(request.revision, 2147483647, "/revision", 1)
    _match(request.policy_revision.id, _ID, "/policy_revision/id")
    _match(request.policy_revision.sha256, _DIGEST, "/policy_revision/sha256")
    if request.privacy_sha256 != privacy.sha256:
        fail("binding", "/privacy_sha256")
    raw = cast(dict[str, object], data(request))
    del raw["snapshot_sha256"]
    if request.snapshot_sha256 != digest(raw):
        fail("binding", "/snapshot_sha256")
    _bindings(request, privacy)
    if isinstance(request, c.IntakeRequest):
        _origin(request.origin)
        _match(request.target.workstream_id, _ID, "/target/workstream_id")
        _match(request.target.repo_id, _ID, "/target/repo_id")
        if request.target.workstream_id != request.workstream_id:
            fail("binding", "/target")
        _count(request.open_tasks, 32, "/open_tasks")
        if len({task.id for task in request.open_tasks}) != len(request.open_tasks):
            fail("invariant", "/open_tasks")
        for task in request.open_tasks:
            _match(task.id, _BEAD, "/open_tasks")
            _title(task.title, privacy, "/open_tasks")
            _match(task.description_digest, _DIGEST, "/open_tasks")
    elif isinstance(request, c.SufficiencyRequest):
        _sufficiency(request, privacy)
    else:
        _match(request.subject.task_id, _BEAD, "/subject/task_id")
        _match(request.subject.session_ref, _ID, "/subject/session_ref")
        _match(request.subject.tool_name, _ID, "/subject/tool_name")
        _match(request.invocation.cwd_id, _ID, "/invocation/cwd_id")
        _integer(request.effective_budget_ms, 60000, "/effective_budget_ms", 1)
        _integer(request.elapsed_ms, 2147483647, "/elapsed_ms")
        if request.subject_id != request.subject.task_id:
            fail("binding", "/subject_id")
        invocation = dict(
            subject=data(request.subject),
            cwd_id=request.invocation.cwd_id,
            input=request.invocation.input,
            policy_revision=data(request.policy_revision),
        )
        if request.invocation.sha256 != digest(invocation):
            fail("binding", "/invocation/sha256")
    _contexts(request, privacy)
    if len(canonical(request)) > 131072:
        fail("oversize")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail("schema")
        result[key] = value
    return result


def _constant(_: str) -> Never:
    fail("schema")


def _wire(data_bytes: bytes, maximum: int) -> object:
    if type(data_bytes) is not bytes:
        fail("schema")
    if len(data_bytes) > maximum:
        fail("oversize")
    try:
        value: object = json.loads(
            data_bytes.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except (UnicodeError, ValueError, RecursionError):
        fail("schema")
    return value


def prepare(
    raw: object, privacy: c.PrivacyContext, *, native_digest: str | None = None
) -> c.Prepared[c.AnyRequest]:
    """Trusted-host assembly; no globals, native resolution or effects. Raw origin remains caller-owned."""
    try:
        privacy = privacy_context(data(privacy))
    except (Fault, msgspec.ValidationError, TypeError):
        return c.InvalidHost("/privacy_sha256")
    if isinstance(raw, c.Request):
        raw = data(raw)
    if not isinstance(raw, dict):
        return c.InvalidHost("")
    raw = cast(dict[str, object], raw)
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in REQUEST_TYPES:
        return c.InvalidHost("")
    try:
        _origin_fields(raw, kind, privacy)
    except Fault as error:
        return c.OriginGap((c.Gap(error.code, error.field, None),))
    if kind == "permission" and (
        type(raw.get("effective_budget_ms")) is not int
        or not 1 <= cast(int, raw["effective_budget_ms"]) <= 60000
    ):
        return c.HostFailed("unreadable_policy")
    try:
        request = msgspec.convert(raw, type=REQUEST_TYPES[kind], strict=True)
        _host(request, privacy)
        if native_digest is not None and isinstance(request, c.SufficiencyRequest):
            if request.native_context_digest != native_digest:
                return c.HostFailed("stale_digest")
    except msgspec.ValidationError as error:
        path = schema_path(error)
        return c.InvalidHost("/context" if path.startswith("/context") else path)
    except (UnicodeError, RecursionError, TypeError, ValueError) as error:
        if isinstance(error, Fault):
            if error.field == "/context" and error.code == "binding":
                return c.HostFailed("stale_digest")
            if error.code == "unsafe":
                return c.HostFailed("egress_unsafe")
            if error.code == "oversize":
                return c.HostFailed("egress_oversize")
            return c.InvalidHost(error.field if public_path(error.field) else "")
        return c.InvalidHost("")
    return c.Ready(request)


def decode_request(encoded: bytes, privacy: c.PrivacyContext) -> c.Check[c.AnyRequest]:
    try:
        raw = _wire(encoded, 131072)
    except Fault as error:
        return c.Invalid("request", cast(c.CheckCode, error.code), error.field)
    checked = prepare(raw, privacy)
    if isinstance(checked, c.Ready):
        return c.Valid(checked.value)
    if isinstance(checked, c.OriginGap):
        gap = checked.gaps[0]
        return c.Invalid(
            "request", cast(c.CheckCode, "schema" if gap.code == "profile" else gap.code), gap.field
        )
    return c.Invalid("request", "binding" if isinstance(checked, c.HostFailed) else "invariant", "")


def task_aliases(request: c.IntakeRequest) -> dict[str, str]:
    return {key: str(i + 1) for i, key in enumerate(sorted(task.id for task in request.open_tasks))}


def _outcome(request: c.AnyRequest, result: c.AnyJudgment, privacy: c.PrivacyContext) -> None:
    if (
        result.request_id != request.request_id
        or result.revision != request.revision
        or result.snapshot_sha256 != request.snapshot_sha256
    ):
        fail("binding")
    outcome = result.outcome
    if isinstance(outcome, c.ModelUnavailable):
        return
    _text(outcome.reason, privacy, "/outcome/reason")
    if isinstance(request, c.IntakeRequest) and isinstance(outcome, c.Proposed):
        _title(outcome.title, privacy, "/outcome/title")
        _text(outcome.description, privacy, "/outcome/description", 16384)
        _count(outcome.acceptance, 16, "/outcome/acceptance", 1)
        for i, item in enumerate(outcome.acceptance):
            _text(item, privacy, f"/outcome/acceptance/{i}")
        _count(outcome.dependencies, 16, "/outcome/dependencies")
        ids = [row.task_id for row in outcome.dependencies]
        if (
            len(set(ids)) != len(ids)
            or request.subject_id in ids
            or not set(ids) <= {task.id for task in request.open_tasks}
        ):
            fail("invariant", "/outcome/dependencies")
    elif isinstance(request, c.SufficiencyRequest):
        if isinstance(outcome, c.Sufficient):
            if outcome.additions or outcome.gaps:
                fail("invariant", "/outcome")
        elif isinstance(outcome, c.Enrich):
            if outcome.gaps:
                fail("invariant", "/outcome/gaps")
            _additions(request, outcome.additions, privacy)
        elif isinstance(outcome, c.Groom):
            if outcome.additions:
                fail("invariant", "/outcome/additions")
            _count(outcome.gaps, 64, "/outcome/gaps", 1)
            for i, gap in enumerate(outcome.gaps):
                _text(gap.detail, privacy, f"/outcome/gaps/{i}/detail")
        else:
            fail("schema", "/outcome")
    elif not (
        isinstance(request, c.PermissionRequest) and isinstance(outcome, (c.Allow, c.Deny, c.Escalate))
    ):
        fail("schema", "/outcome")


def request_kind(request: c.AnyRequest) -> str:
    return (
        "intake"
        if isinstance(request, c.IntakeRequest)
        else ("sufficiency" if isinstance(request, c.SufficiencyRequest) else "permission")
    )


def check_result(raw: object, request: c.AnyRequest, privacy: c.PrivacyContext) -> c.Check[c.AnyJudgment]:
    """Validate a host-normalized result; never treats model output as host failure."""
    try:
        result = msgspec.convert(data(raw), type=RESULT_TYPES[request_kind(request)], strict=True)
        _outcome(request, result, privacy)
    except msgspec.ValidationError as error:
        return c.Invalid("result", "schema", schema_path(error))
    except (Fault, UnicodeError, RecursionError, TypeError, ValueError) as error:
        if isinstance(error, Fault):
            return c.Invalid("result", cast(c.CheckCode, error.code), error.field)
        return c.Invalid("result", "schema", "")
    return c.Valid(result)


def decode_result(encoded: bytes, request: c.AnyRequest, privacy: c.PrivacyContext) -> c.Check[c.AnyJudgment]:
    """Decode the model-facing alias grammar, then normalize with frozen request maps.

    A private ID in place of an alias is rejected even if it names a real source/task.
    Only bytes enter this seam, so no caller-supplied object can masquerade as wire output.
    """
    try:
        raw_value = _wire(encoded, 32768)
        if not isinstance(raw_value, dict):
            fail("schema")
        raw = cast(dict[str, object], raw_value)
        outcome_value = raw.get("outcome")
        outcome = cast(dict[str, object], outcome_value) if isinstance(outcome_value, dict) else None
        if isinstance(outcome, dict):
            if isinstance(request, c.IntakeRequest) and outcome.get("status") == "proposed":
                reverse = {alias: key for key, alias in task_aliases(request).items()}
                rows = outcome.get("dependencies")
                if isinstance(rows, list):
                    for row_value in cast(list[object], rows):
                        if isinstance(row_value, dict):
                            row = cast(dict[str, object], row_value)
                            alias = row.get("task_id")
                            if not isinstance(alias, str) or alias not in reverse:
                                fail("invariant", "/outcome/dependencies")
                            row["task_id"] = reverse[alias]
            elif isinstance(request, c.SufficiencyRequest) and outcome.get("status") == "enrich":
                reverse = {alias: key for key, alias in aliases(request).items()}
                rows = outcome.get("additions")
                if isinstance(rows, list):
                    for row_value in cast(list[object], rows):
                        if isinstance(row_value, dict):
                            row = cast(dict[str, object], row_value)
                            alias = row.get("source_id")
                            if not isinstance(alias, str) or alias not in reverse:
                                fail("invariant", "/outcome/additions")
                            row["source_id"] = reverse[alias]
        return check_result(raw, request, privacy)
    except (Fault, RecursionError, TypeError, ValueError) as error:
        if isinstance(error, Fault):
            return c.Invalid("result", cast(c.CheckCode, error.code), error.field)
        return c.Invalid("result", "schema", "")


def egress(request: c.AnyRequest, privacy: c.PrivacyContext) -> c.Check[dict[str, object]]:
    """Project only approved public fields. Repo locators/host provenance never leave."""
    prepared = prepare(request, privacy)
    if not isinstance(prepared, c.Ready):
        return c.Invalid("request", "invariant", "")
    common: dict[str, object] = dict(
        format=1,
        kind=request_kind(request),
        request_id=request.request_id,
        revision=request.revision,
        snapshot_sha256=request.snapshot_sha256,
    )
    context = [dict(alias=aliases(request)[item.source_id], text=item.text) for item in request.context]
    if isinstance(request, c.IntakeRequest):
        mapping = task_aliases(request)
        common.update(
            original=request.original,
            target=data(request.public_bindings.target),
            context_complete=request.context_complete,
            open_tasks=[dict(alias=mapping[task.id], title=task.title) for task in request.open_tasks],
            context=context,
        )
    elif isinstance(request, c.SufficiencyRequest):
        candidate = request.candidate
        card = dict(
            title=candidate.title,
            description=candidate.description,
            effect=candidate.ask.effect,
            excludes=candidate.ask.excludes,
            why=candidate.ask.why,
            risks=candidate.ask.risks,
        )
        repos = {row.ref_index: row.repo for row in request.public_bindings.ref_repositories}
        refs: list[dict[str, object]] = []
        for i, ref in enumerate(candidate.ask.refs):
            view: dict[str, object] = dict(
                alias=str(i + 1), kind=cast(dict[str, object], data(ref))["kind"], id=ref.id
            )
            if not isinstance(ref, c.BeadRef):
                view["repo"] = data(repos[i])
            if isinstance(ref, c.FileRef):
                view["path"] = ref.path
            refs.append(view)
        common.update(
            card=card,
            refs=refs,
            targets=[] if candidate.ask.targets is msgspec.UNSET else list(candidate.ask.targets),
            context=context,
        )
    else:
        common.update(
            tool_name=request.subject.tool_name,
            intent=request.intent,
            cwd=data(request.public_bindings.cwd),
            input=request.invocation.input,
            invocation_sha256=request.invocation.sha256,
        )
    return c.Valid(common)
