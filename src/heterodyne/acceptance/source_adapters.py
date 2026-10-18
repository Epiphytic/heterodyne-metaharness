"""Captured native diagnostics, deliberately insufficient for operational Plan9 authority.

The configured complete source is verified before execution. Only captured I/O is injected into a
fresh private namespace; approval_valid and ask_digest remain the exact native implementations.
Captures are untrusted consistency evidence, not authenticated observations. No constructor,
credential loader, live Git, database, socket, policy loader, or queue command is used.
"""

import hashlib
import io
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import msgspec

from heterodyne.admind import binding

from . import codec, models

NATIVE_SOURCE_SHA256 = "ec2bc67b227326c80bf5d9a32eb661b220ea9af6942ef31bff04d8c06941bd2e"
MAX_CAPTURE_BYTES = 1_048_576
MAX_READS = 128


class GitRead(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    repo: str
    args: tuple[str, ...]
    stdin: str | None
    stdout: str


class Capture(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    issue: dict[str, Any]
    policy: dict[str, Any]
    linked: dict[str, dict[str, Any]]
    repos: tuple[str, ...]
    git: tuple[GitRead, ...]


@dataclass(frozen=True)
class NativeDiagnostic:
    """Consistency result only. Even gate_valid=True never establishes captured authenticity."""

    digest: str | None
    gaps: tuple[str, ...]
    gate_valid: bool
    source_sha256: str

    def require_authority(self) -> None:
        raise codec.Hold("historical_authority_and_fence_unavailable")


def _unavailable(*_args: Any, **_kwargs: Any) -> Any:
    raise codec.Hold("uncaptured_native_io")


def _capture(raw: bytes) -> Capture:
    if len(raw) > MAX_CAPTURE_BYTES:
        raise codec.Hold("native_capture_bound")
    try:
        value = msgspec.convert(codec.load(raw), type=Capture, strict=True)
    except (codec.InvalidEvidence, msgspec.ValidationError):
        raise codec.Hold("invalid_native_capture") from None
    if (len(value.git) > MAX_READS or len(value.linked) > MAX_READS
            or len(value.repos) > MAX_READS or len(set(value.repos)) != len(value.repos)):
        raise codec.Hold("native_capture_bound")
    for read in value.git:
        if (read.repo not in value.repos or not read.args or len(read.args) > 16
                or any(len(arg) > 4096 for arg in read.args)):
            raise codec.Hold("invalid_git_capture")
    return value


class CapturedNative:
    """Trusted configuration supplies source bytes, never a path or source digest in evidence.

    Only the explicitly supported complete native revision is executable. An absent/different
    installation is unavailable, with no fallback to a vendored subset or a rewritten algorithm.
    """

    def __init__(self, source: bytes | None) -> None:
        if source is None or hashlib.sha256(source).hexdigest() != NATIVE_SOURCE_SHA256:
            raise codec.Hold("native_source_unavailable")
        self._source = bytes(source)

    def _namespace(self, capture: Capture) -> dict[str, Any]:
        reads: dict[tuple[str, tuple[str, ...], str | None], str] = {}
        for read in capture.git:
            key = (read.repo, read.args, read.stdin)
            if key in reads:
                raise codec.Hold("duplicate_git_capture")
            reads[key] = read.stdout

        def git(repo: str, *args: str, stdin: str | None = None) -> str:
            try:
                return reads[(repo, args, stdin)]
            except KeyError:
                raise codec.Hold("uncaptured_native_io") from None

        def repo_present(repo: str) -> bool:
            if repo not in capture.repos:
                raise codec.Hold("uncaptured_native_repo")
            return True

        namespace: dict[str, Any] = {"__name__": "_plan9_captured_native", "__file__": "/captured/bin/btq"}
        # Verified complete source, not an evidence-supplied executable. Top-level code only defines
        # the native module; the guarded CLI main is never entered. No module registry mutation.
        exec(compile(self._source, "<pinned-native-btq>", "exec"), namespace)  # noqa: S102
        namespace.update(_git=git, _git_repo_present=repo_present, _git_confirm_absent=_unavailable,
                         load_policy=_unavailable, sys=SimpleNamespace(stderr=io.StringIO()),
                         subprocess=SimpleNamespace(run=_unavailable,
                         Popen=_unavailable, check_output=_unavailable))
        namespace["Queue"].__init__ = _unavailable
        return namespace

    def validate(self, raw: bytes, revision: str) -> NativeDiagnostic:
        """Recompute native ask and gate solely over this bounded captured-read transcript."""
        capture = _capture(raw)
        namespace = self._namespace(capture)

        def show(bead: str) -> dict[str, Any]:
            try:
                return capture.linked[bead]
            except KeyError:
                raise codec.Hold("uncaptured_native_bead") from None

        queue = namespace["Queue"]
        context = SimpleNamespace(show=show, policy=capture.policy,
                                  _MALFORMED_EVIDENCE_EXCEPTIONS=queue._MALFORMED_EVIDENCE_EXCEPTIONS)
        # Native diagnostics can include arbitrary bead text. Keep it bounded and private to this
        # calculation, never expose raw prose through the operational diagnostic interface.
        try:
            digest, gaps = namespace["ask_digest"](capture.issue, show)
            valid = queue.approval_valid(context, capture.issue, revision)
        except codec.Hold:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, UnicodeError):
            raise codec.Hold("native_capture_unavailable") from None
        return NativeDiagnostic(digest, tuple(gaps), valid, NATIVE_SOURCE_SHA256)

    def correlate(self, raw: bytes, reply: bytes, authority: models.NativeAuthority) -> NativeDiagnostic:
        """Check exact native fields and the source-owned binding DTO; still not authority proof.

        Read-time current_policy is intentionally NOT treated as historical authorization. The
        caller must separately establish immutable source provenance and freshness; this slice
        provides neither and require_authority always HOLDs.
        """
        diagnostic = self.validate(raw, authority.adr_revision)
        capture = _capture(raw)
        metadata = capture.issue.get("metadata")
        if not isinstance(metadata, dict) or not diagnostic.gate_valid:
            raise codec.Hold("native_binding_mismatch")
        metadata = cast(dict[str, Any], metadata)
        expected = {
            "decision": authority.decision, "approved_by": authority.approver_ref,
            "approved_at": authority.approved_at, "approved_digest": authority.approved_digest,
            "context_digest": authority.context_digest, "adr_revision": authority.adr_revision,
            "via": authority.via, "via_ref": authority.via_ref,
        }
        if (capture.issue.get("id") != authority.native_bead_id
                or diagnostic.digest != authority.approved_digest
                or any(metadata.get(key) != value for key, value in expected.items())):
            raise codec.Hold("native_binding_mismatch")
        envelope = _binding(reply)
        attempts = [item for item in envelope.decision_attempts if item.ref == authority.via_ref]
        if (authority.via != "marmot" or envelope.inputs.kind != "approval"
                or envelope.inputs.bead != authority.native_bead_id
                or envelope.inputs.digest != authority.approved_digest
                or envelope.status != "approved" or not envelope.outcome
                or envelope.decided_by != authority.approver_ref or len(attempts) != 1):
            raise codec.Hold("native_binding_mismatch")
        attempt = attempts[0]
        if (attempt.operator != authority.approver_ref or attempt.digest != authority.approved_digest
                or attempt.action != "approve" or attempt.settled != "recorded"
                or envelope.current_attempt is not None):
            raise codec.Hold("native_binding_mismatch")
        return diagnostic


def _binding(raw: bytes) -> binding.Envelope:
    if len(raw) > binding.MAX_BYTES:
        raise codec.Hold("binding_capture_bound")
    try:
        reply = msgspec.convert(codec.load(raw), type=binding.AskBindingReply, strict=True)
    except (codec.InvalidEvidence, msgspec.ValidationError):
        raise codec.Hold("invalid_binding_capture") from None
    value = reply.binding
    if (reply.result != "ok" or reply.code is not None or value is None
            or len(value.answers) > 50 or len(value.decision_attempts) > 50
            or not 1 <= len(value.card_parts) <= binding.MAX_PARTS or not value.delivered
            or value.render_input_sha256 != binding.sha256(value.inputs)
            or value.policy_sha256 != binding.sha256(value.policy)):
        raise codec.Hold("invalid_binding_capture")
    return value
