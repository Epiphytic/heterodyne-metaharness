"""Synthetic captures exercised by complete pinned native source; never live authority."""
import hashlib
import os
from pathlib import Path

import msgspec
import pytest

from heterodyne.acceptance import source_adapters as s
from heterodyne.acceptance.codec import Hold


@pytest.fixture
def native() -> s.CapturedNative:
    # Explicit test configuration, never an evidence-supplied source locator. Missing source HOLDs.
    path = os.environ.get("PLAN9_NATIVE_SOURCE")
    if path is None:
        pytest.skip("complete pinned native source unavailable: configure PLAN9_NATIVE_SOURCE")
    return s.CapturedNative(Path(path).read_bytes())


def capture() -> bytes:
    return msgspec.json.encode({
        "issue": {"id": "btq-abcde", "title": "Synthetic approval", "description": "context " * 50,
                  "status": "closed", "metadata": {"ask": {
                      "effect": "offline", "excludes": "live", "why": "test", "risks": "none",
                      "refs": [{"kind": "file", "repo": "synthetic-repo", "id": "a" * 40,
                                "path": "docs/adr/example.md"}]}}},
        "policy": {"approvers": ["op"]}, "linked": {},
        "repos": ["synthetic-repo"], "git": [
            {"repo": "synthetic-repo", "args": ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
             "stdin": "a" * 40 + "\n", "stdout": "a" * 40 + " commit\n"},
            {"repo": "synthetic-repo", "args": ["--literal-pathspecs", "ls-tree", "-z", "--full-tree",
                                                     "a" * 40, "--", "docs/adr/example.md"],
             "stdin": None, "stdout": "100644 blob " + "b" * 40 + "\tdocs/adr/example.md\0"}]})


def approved(native: s.CapturedNative) -> bytes:
    raw = capture()
    digest = native.validate(raw, "a" * 40).digest
    value = msgspec.json.decode(raw)
    value["issue"]["metadata"].update({
        "decision": "approve", "approved_by": "op", "approved_at": "2026-10-16T20:00:00Z",
        "approved_digest": digest, "context_digest": digest, "adr_revision": "a" * 40,
        "via": "marmot", "via_ref": "marmot:id:" + "c" * 64,
        "design_review": "reviewer=opus author=codex mode=cross-model"})
    return msgspec.json.encode(value)


def test_exact_native_digest_and_gate(native: s.CapturedNative) -> None:
    first = native.validate(capture(), "a" * 40)
    assert first.digest is not None and not first.gaps and not first.gate_valid
    result = native.validate(approved(native), "a" * 40)
    assert result.gate_valid and result.digest == first.digest
    assert result.source_sha256 == s.NATIVE_SOURCE_SHA256
    with pytest.raises(Hold, match="historical_authority_and_fence_unavailable"):
        result.require_authority()


def test_source_pin_rejects_before_execution() -> None:
    with pytest.raises(Hold, match="native_source_unavailable"):
        s.CapturedNative(b'raise AssertionError("must not execute")')
    with pytest.raises(Hold, match="native_source_unavailable"):
        s.CapturedNative(None)


@pytest.mark.parametrize("change", ["uncaptured", "repo", "duplicate", "extra", "float", "large"])
def test_capture_refusals(native: s.CapturedNative, change: str) -> None:
    raw = capture()
    value = msgspec.json.decode(raw)
    if change == "uncaptured":
        value["git"] = []
    elif change == "repo":
        value["repos"] = []
    elif change == "duplicate":
        value["git"].append(value["git"][0])
    elif change == "extra":
        value["authenticate"] = True
    elif change == "float":
        raw = raw.replace(b'"policy":{', b'"policy":{"x":1.0,')
    else:
        raw = b" " * (s.MAX_CAPTURE_BYTES + 1)
    if change not in ("float", "large"):
        raw = msgspec.json.encode(value)
    with pytest.raises(Hold):
        native.validate(raw, "a" * 40)


def test_denial_and_policy_are_native_rejections(native: s.CapturedNative) -> None:
    value = msgspec.json.decode(approved(native))
    value["issue"]["metadata"]["denied_by"] = None
    assert not native.validate(msgspec.json.encode(value), "a" * 40).gate_valid
    del value["issue"]["metadata"]["denied_by"]
    value["policy"]["approvers"] = ["other"]
    assert not native.validate(msgspec.json.encode(value), "a" * 40).gate_valid


def test_native_hash_is_not_acceptance_canonical(native: s.CapturedNative) -> None:
    value = msgspec.json.decode(capture())
    value["issue"]["title"] = "Unicode é"
    raw = msgspec.json.encode(value)
    result = native.validate(raw, "a" * 40)
    hashed = {"title": "Unicode é", "description": value["issue"]["description"],
              "ask": value["issue"]["metadata"]["ask"], "resolved": ["b" * 40]}
    from heterodyne.admind.binding import canonical
    assert result.digest == hashlib.sha256(canonical(hashed)).hexdigest()


def test_native_io_and_constructor_traps(native: s.CapturedNative, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import socket
    import subprocess

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected live I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert native.validate(approved(native), "a" * 40).gate_valid
    namespace = native._namespace(s._capture(capture()))
    with pytest.raises(Hold, match="uncaptured_native_io"):
        namespace["Queue"]()
    with pytest.raises(Hold, match="uncaptured_native_io"):
        namespace["load_policy"]()


def bound(native: s.CapturedNative):
    """Synthetic DTO, not an authenticated daemon capture or rendered delivery proof."""
    from acceptance_fixtures import NATIVE

    from heterodyne.admind import binding

    raw = approved(native)
    digest = native.validate(raw, "a" * 40).digest
    assert digest is not None
    authority = msgspec.structs.replace(
        NATIVE, native_bead_id="btq-abcde", approver_ref="op", approved_at="2026-10-16T20:00:00Z",
        approved_at_utc="2026-10-16T20:00:00.000000Z", approved_digest=digest,
        context_digest=digest, adr_revision="a" * 40, via_ref="marmot:id:" + "c" * 64)
    inputs = binding.RenderInputs(
        "k7m2", "approval", "Synthetic", "Synthetic captured body", "controller", None, [], None,
        [], None, None, "btq-abcde", digest, None, 4000, (1, 0))
    policy = [binding.PolicyPrincipal("op", "f" * 64)]
    assert authority.via_ref is not None
    attempt = binding.DecisionAttempt("c" * 64, "op", "d" * 64, False, "approve",
                                      authority.via_ref, digest, None, authority.approved_at,
                                      "recorded", 0)
    envelope = binding.Envelope(
        1, inputs, "approved", "Approved synthetic bead.", "op", None,
        binding.sha256(inputs), True, "original",
        [binding.CardPart("ask:k7m2:0", "Synthetic card", "sent", "e" * 64)], False, [], True,
        [attempt], True, policy, binding.sha256(policy), 9007199254740993,
        "00000000-0000-0000-0000-000000000001")
    reply = binding.AskBindingReply("ok", "ask.binding.v1 complete", binding=envelope)
    return raw, msgspec.json.encode(reply), authority


def test_correlates_but_never_promotes_current_policy_to_authority(native: s.CapturedNative) -> None:
    raw, reply, authority = bound(native)
    diagnostic = native.correlate(raw, reply, authority)
    assert diagnostic.gate_valid
    # Captured current_policy=False is retained, not silently converted to historical consent.
    with pytest.raises(Hold, match="historical_authority_and_fence_unavailable"):
        diagnostic.require_authority()


@pytest.mark.parametrize("field", ["actor", "ref", "digest", "attempt", "settled", "render", "policy",
                                   "duplicate", "float", "unknown", "refused", "native-time"])
def test_binding_strict_correlation(native: s.CapturedNative, field: str) -> None:
    raw, reply, authority = bound(native)
    value = msgspec.json.decode(reply)
    envelope = value["binding"]
    if field == "actor":
        envelope["decision_attempts"][0]["operator"] = "other"
    elif field == "ref":
        envelope["decision_attempts"][0]["ref"] = "other"
    elif field == "digest":
        envelope["inputs"]["digest"] = "f" * 64
    elif field == "attempt":
        envelope["current_attempt"] = "f" * 64
    elif field == "settled":
        envelope["decision_attempts"][0]["settled"] = "uncertain"
    elif field == "render":
        envelope["render_input_sha256"] = "f" * 64
    elif field == "policy":
        envelope["policy_sha256"] = "f" * 64
    elif field == "duplicate":
        envelope["decision_attempts"].append(envelope["decision_attempts"][0])
    elif field == "float":
        envelope["read_at_boottime_ns"] = 1.0
    elif field == "unknown":
        value["authenticated"] = True
    elif field == "refused":
        value["result"] = "refused"
    else:
        authority = msgspec.structs.replace(authority, approved_at="2026-10-16T21:00:00Z")
    with pytest.raises(Hold):
        native.correlate(raw, msgspec.json.encode(value), authority)


def test_capture_namespaces_do_not_share_beads_or_policy(native: s.CapturedNative) -> None:
    good = approved(native)
    bad = msgspec.json.decode(good)
    bad["policy"]["approvers"] = ["other"]
    assert not native.validate(msgspec.json.encode(bad), "a" * 40).gate_valid
    assert native.validate(good, "a" * 40).gate_valid


def test_unverified_missing_git_object_holds(native: s.CapturedNative) -> None:
    value = msgspec.json.decode(capture())
    value["git"][0]["stdout"] = "a" * 40 + " missing\n"
    with pytest.raises(Hold, match="uncaptured_native_io"):
        native.validate(msgspec.json.encode(value), "a" * 40)


def test_linked_bead_reads_are_captured_and_missing_read_holds(native: s.CapturedNative) -> None:
    value = msgspec.json.decode(capture())
    linked = value["issue"]
    linked["metadata"]["context_digest"] = native.validate(capture(), "a" * 40).digest
    import copy
    root = copy.deepcopy(linked)
    root["id"] = "btq-fghij"
    root["metadata"]["ask"]["refs"] = [{"kind": "bead", "id": "btq-abcde"}]
    value["issue"] = root
    value["linked"] = {"btq-abcde": linked}
    assert not native.validate(msgspec.json.encode(value), "a" * 40).gaps
    value["linked"] = {}
    with pytest.raises(Hold, match="uncaptured_native_bead"):
        native.validate(msgspec.json.encode(value), "a" * 40)


def test_duplicate_json_and_invalid_binding_are_closed(native: s.CapturedNative) -> None:
    with pytest.raises(Hold, match="invalid_native_capture"):
        native.validate(capture().replace(b'"linked":{}', b'"linked":{},"linked":{}'), "a" * 40)
    raw, reply, authority = bound(native)
    with pytest.raises(Hold, match="invalid_binding_capture"):
        native.correlate(raw, reply.replace(b'"version":1', b'"version":true'), authority)
    with pytest.raises(Hold, match="binding_capture_bound"):
        native.correlate(raw, b" " * (s.MAX_CAPTURE_BYTES + 1), authority)


def test_recorded_binding_clears_current_attempt_and_retains_unknown_exit(native: s.CapturedNative) -> None:
    # Actual Store.close_attempt clears asks.attempt for recorded outcomes. Reconcile may not
    # know the child exit code; native readback, not a successful subprocess code, establishes it.
    raw, reply, authority = bound(native)
    value = msgspec.json.decode(reply)
    value["binding"]["current_attempt"] = None
    value["binding"]["decision_attempts"][0]["exit_status"] = None
    assert native.correlate(raw, msgspec.json.encode(value), authority).gate_valid


def test_native_stderr_capture_is_namespace_local(
    native: s.CapturedNative, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    raw = approved(native)
    original = sys.stderr
    factory = native._namespace

    def namespace(captured):
        result = factory(captured)
        git = result["_git"]

        def check(*args, **kwargs):
            assert sys.stderr is original, "native validation changed process-global stderr"
            return git(*args, **kwargs)

        result["_git"] = check
        return result

    monkeypatch.setattr(native, "_namespace", namespace)
    assert native.validate(raw, "a" * 40).gate_valid
