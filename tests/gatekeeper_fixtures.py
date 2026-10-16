"""Pure host snapshots: no native resolver/model/transport is constructed."""

import hashlib
from typing import Any

from heterodyne.gatekeeper import contracts as c
from heterodyne.gatekeeper.validate import RULE_REVISION, canonical, digest, prepare, text_hash


def privacy() -> c.PrivacyContext:
    body: dict[str, Any] = dict(format=1, login_dirs=["/private/login"], rule_revision=RULE_REVISION)
    return c.PrivacyContext(1, tuple(body["login_dirs"]), RULE_REVISION, digest(body))


def seal(raw: Any) -> dict[str, Any]:
    raw = dict(raw)
    raw.pop("snapshot_sha256", None)
    raw["snapshot_sha256"] = digest(raw)
    return raw


def request(kind="intake") -> dict[str, Any]:
    raw: dict[str, Any] = dict(
        format=1,
        kind=kind,
        request_id="req",
        workstream_id="work",
        subject_id="submit",
        revision=1,
        policy_revision=dict(id="policy", sha256="b" * 64),
        privacy_sha256=privacy().sha256,
        context=[],
        snapshot_sha256="",
        public_bindings=dict(target=None, ref_repositories=[], cwd=None),
    )
    origin = dict(event_ref="event", creator_ref="creator", session_ref="session")
    if kind == "intake":
        raw.update(
            original="Clarify a reference",
            origin=origin,
            target=dict(workstream_id="work", repo_id="private-repository"),
            context_complete=True,
            open_tasks=[dict(id="btq-old.1", title="Existing work", description_digest="c" * 64)],
        )
        raw["public_bindings"]["target"] = dict(workstream="Project", repo=dict(label="Product"))
    elif kind == "sufficiency":
        text = "Explicit safe evidence αβγ."
        blob = hashlib.sha1(
            b"blob " + str(len(text.encode())).encode() + b"\0" + text.encode(), usedforsecurity=False
        ).hexdigest()
        ref = dict(kind="file", id=blob, repo="/private/source", path="docs/proof.md")
        candidate = dict(
            title="Bounded approval",
            description="What, context and alternatives are explicit.",
            ask=dict(
                effect="Offline work",
                excludes="No deployment",
                why="Fix a bug",
                risks="Limited scope",
                refs=[ref],
            ),
        )
        raw.update(
            subject_id="btq-appro",
            approval_id="btq-appro",
            native_context_digest="d" * 64,
            lint=dict(status="passed", gaps=[]),
            candidate=candidate,
            resolved_refs=[dict(ref=ref, resolved_id=blob)],
            origin=origin,
            submission_id="submission",
            submission_revision=1,
            base_candidate_sha256=digest(candidate),
            enrichment=dict(status="unused"),
            context=[
                dict(
                    source_id="private-source",
                    kind="doc",
                    content_id=text_hash(text),
                    text=text,
                    ref=ref,
                    resolved_id=blob,
                )
            ],
        )
        raw["public_bindings"]["ref_repositories"] = [dict(ref_index=0, repo=dict(label="Product"))]
    else:
        subject = dict(task_id="btq-abcde", session_ref="private-session", tool_name="Edit")
        policy = raw["policy_revision"]
        invocation = dict(
            subject=subject, cwd_id="private-cwd", input={"path": "src/a.py"}, policy_revision=policy
        )
        raw.update(
            subject_id="btq-abcde",
            subject=subject,
            intent="Fix a local file",
            invocation=dict(
                cwd_id=invocation["cwd_id"], input=invocation["input"], sha256=digest(invocation)
            ),
            effective_budget_ms=60000,
            elapsed_ms=0,
        )
        raw["public_bindings"]["cwd"] = dict(repo=dict(label="Product"), path="src")
    return seal(raw)


def ready(kind="intake") -> c.Ready[Any]:
    result = prepare(request(kind), privacy())
    assert isinstance(result, c.Ready), result
    return result


def outcome(kind="intake") -> dict[str, Any]:
    if kind == "intake":
        return dict(
            status="proposed",
            materiality="minor",
            title="Clarify a reference",
            description="Keep the original request",
            acceptance=["Reference is clear"],
            dependencies=[],
            change_flags={
                key: False
                for key in (
                    "target_changed",
                    "requirements_changed",
                    "question_to_action",
                    "destructive",
                    "outward_facing",
                    "context_incomplete",
                )
            },
            reason="Only clarification",
        )
    if kind == "sufficiency":
        return dict(status="sufficient", reason="Complete card", additions=[], gaps=[])
    return dict(status="allow", reason="Advisory grey zone")


def judgment(req: Any, chosen=None) -> dict[str, Any]:
    return dict(
        format=1,
        kind=req.__struct_config__.tag,
        request_id=req.request_id,
        revision=req.revision,
        snapshot_sha256=req.snapshot_sha256,
        outcome=chosen or outcome(req.__struct_config__.tag),
    )


def wire(value: Any) -> bytes:
    return canonical(value)
