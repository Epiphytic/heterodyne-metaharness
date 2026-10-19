"""Pure Plan6C identity and delivery policy (approved d4 §§3–5).

No I/O, persistence, renderer, transport or source adapter is installed here. Owner-supplied gates,
proofs and receipt correlation are *inputs*, not authentication or durability certificates. A future
owner must qualify those facts under the approved protocol before applying any returned transition.
The immutable in-memory history does not replace the required recovery-log/dispatch barriers.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Literal, get_args

SourceKind = Literal["question", "approval", "task", "digest"]
Reason = Literal["initial", "update", "mirror", "reminder", "digest", "recovery", "join", "reissue"]
Profile = Literal["canonical-v1", "display-v1"]
Phase = Literal["reserved", "dispatch-marked", "confirmed", "proven_unissued", "uncertain"]
ProofKind = Literal["prewrite-refusal", "reserved-no-dispatch"]
Uncertainty = Literal["write-started", "response-lost", "invalid-receipt", "recovered-dispatch-mark"]


HeldCode = Literal[
    "source-unavailable",
    "source-changed",
    "trust-unavailable",
    "trust-denied",
    "clock-unready",
    "contention",
    "capacity",
    "parent-incomplete",
    "source-closed",
    "exhausted",
    "recovery-required",
]
RefusedCode = Literal["invalid-shape", "binding-conflict", "unsupported-policy", "integrity"]


class PolicyError(ValueError):
    """Closed tag/code plus diagnostic text; never a native outcome certificate."""

    def __init__(self, message: str, *, code: HeldCode | RefusedCode) -> None:
        if code not in (*get_args(HeldCode), *get_args(RefusedCode)):
            code = "invalid-shape"
        self.code = code
        self.tag = "refused" if self.code in get_args(RefusedCode) else "held"
        super().__init__(message)


def _text(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise PolicyError("invalid-shape: bounded nonempty text required", code="invalid-shape")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise PolicyError("invalid-shape: UTF-8 required", code="invalid-shape") from exc


def _hex(value: object, size: int) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value) is None:
        raise PolicyError("invalid-shape: lowercase hex required", code="invalid-shape")


def _clock(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PolicyError("clock-unready", code="clock-unready")


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Source:
    producer: str
    kind: SourceKind
    canonical_id: str
    logical_id: str
    revision: str

    def __post_init__(self) -> None:
        for value in (self.producer, self.canonical_id, self.logical_id, self.revision):
            _text(value)
        if self.kind not in get_args(SourceKind):
            raise PolicyError("unsupported-policy: source kind", code="unsupported-policy")


@dataclass(frozen=True)
class Destination:
    """Frozen identity values only; this DTO does not register or authenticate a 6B route."""

    agent: str
    workstream: str
    queue: str
    repository: str
    epoch: str
    account: str
    group: str

    def __post_init__(self) -> None:
        for value in (self.agent, self.workstream, self.queue, self.repository, self.epoch):
            _text(value)
        _hex(self.account, 64)
        if type(self.group) is not str or not 2 <= len(self.group) <= 512 or len(self.group) % 2:
            raise PolicyError("invalid-shape: group bound", code="invalid-shape")
        _hex(self.group, len(self.group))


@dataclass(frozen=True)
class CopyRequest:
    source: Source
    destination: Destination
    reason: Reason
    occurrence: str
    renderer: str
    redactor: str
    config: str

    def __post_init__(self) -> None:
        for value in (self.occurrence, self.renderer, self.redactor, self.config):
            _text(value)
        if self.reason not in get_args(Reason):
            raise PolicyError("unsupported-policy: reason", code="unsupported-policy")
        if self.reason in {"initial", "update", "mirror"} and self.occurrence != self.source.revision:
            raise PolicyError(
                "binding-conflict: occurrence must name exact revision", code="binding-conflict"
            )
        if self.reason == "reminder" and self.occurrence != self.source.logical_id:
            raise PolicyError(
                "binding-conflict: reminder occurrence must name logical decision", code="binding-conflict"
            )
        if self.reason in {"join", "reissue"}:
            _hex(self.occurrence, 64)
        if self.reason == "digest":
            try:
                day = date.fromisoformat(self.occurrence)
            except ValueError as exc:
                raise PolicyError("invalid-shape: UTC digest date", code="invalid-shape") from exc
            if day.isoformat() != self.occurrence:
                raise PolicyError("invalid-shape: canonical UTC digest date", code="invalid-shape")
        if self.reason == "recovery":
            raise PolicyError(
                "unsupported-policy: sealed recovery epoch unavailable", code="unsupported-policy"
            )
        # Inbound occurrences still require the actual owner: this pure DTO neither seals
        # a loss epoch nor authenticates an inbound request or allocates a lifetime reminder budget.

    @property
    def key(self) -> str:
        return _digest({"version": 1, **asdict(self)})

    @property
    def reminder_key(self) -> str:
        """Lifetime owner budget, deliberately separate from immutable copy-payload identity."""
        if self.reason != "reminder":
            raise PolicyError("unsupported-policy: not a reminder", code="unsupported-policy")
        return _digest(("reminder-v1", self.source.producer, self.source.kind, self.source.logical_id))


@dataclass(frozen=True)
class ReminderBudget:
    logical_key: str
    consumed: bool = False

    def __post_init__(self) -> None:
        _hex(self.logical_key, 64)
        if type(self.consumed) is not bool:
            raise PolicyError("invalid-shape: reminder consumed flag", code="invalid-shape")


def consume_reminder(budget: ReminderBudget, request: CopyRequest) -> ReminderBudget:
    """Propose consuming BEFORE reservation, retaining consumption on uncertainty/refresh/loss.

    The future owner must durably compare-and-commit this lifetime budget with copy admission.
    This pure value is neither durable state nor authentication of logical lineage.
    """
    if request.reminder_key != budget.logical_key:
        raise PolicyError("binding-conflict: reminder lifetime", code="binding-conflict")
    if budget.consumed:
        raise PolicyError("held: lifetime reminder consumed", code="capacity")
    return replace(budget, consumed=True)


@dataclass(frozen=True)
class CanonicalRoute:
    kind: Literal["ask", "askr", "askd"]
    ask_id: str
    request_id: str = ""

    def __post_init__(self) -> None:
        if type(self.ask_id) is not str or re.fullmatch(r"[2-9a-hjkmnp-z]{4}", self.ask_id) is None:
            raise PolicyError("invalid-shape: canonical ask ID", code="invalid-shape")
        if self.kind in {"askr", "askd"}:
            _hex(self.request_id, 64)
        elif self.kind != "ask" or self.request_id:
            raise PolicyError("invalid-shape: canonical route", code="invalid-shape")

    def validate(self, request: CopyRequest) -> None:
        """Validate routing values only; the owner must still authenticate lineage and admission."""
        if request.source.kind not in {"question", "approval"}:
            raise PolicyError(
                "binding-conflict: canonical route requires deciding source", code="binding-conflict"
            )
        if self.ask_id != request.source.canonical_id:
            raise PolicyError("binding-conflict: canonical ask identity", code="binding-conflict")
        if self.kind == "ask":
            if request.reason not in {"initial", "update"}:
                raise PolicyError("binding-conflict: noncard reason on ask route", code="binding-conflict")
        elif request.reason not in {"join", "reissue"} or self.request_id != request.occurrence:
            raise PolicyError(
                "binding-conflict: exact canonical inbound request required", code="binding-conflict"
            )

    def key(self, index: int) -> str:
        middle = f":{self.request_id}" if self.request_id else ""
        return f"{self.kind}:{self.ask_id}{middle}:{index}"


@dataclass(frozen=True)
class FrozenCopy:
    request: CopyRequest
    copy_id: str
    profile: Profile
    parts: tuple[str, ...]
    chunk_chars: int
    frame_bytes: int
    route: CanonicalRoute | None = None
    source_token_override: str | None = None
    source_token: str = field(init=False)
    _parts_hash: str = field(init=False, repr=False, compare=False)
    copy_digest: str = field(init=False, repr=False, compare=False)
    _native_keys: tuple[str, ...] = field(init=False, repr=False, compare=False)
    text_hashes: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _hex(self.copy_id, 32)
        token = self.source_token_override
        if token is None:
            token = _digest(asdict(self.request.source))
        object.__setattr__(self, "source_token", token)
        _text(self.source_token)
        if self.profile not in get_args(Profile):
            raise PolicyError("unsupported-policy: profile", code="unsupported-policy")
        for limit in (self.chunk_chars, self.frame_bytes):
            if type(limit) is not int or limit <= 0:
                raise PolicyError("invalid-shape: configured bounds", code="invalid-shape")
        count, total_limit = (1024, 512 * 1024) if self.profile == "canonical-v1" else (20, 64 * 1024)
        if type(self.parts) is not tuple or not 1 <= len(self.parts) <= count:
            raise PolicyError("invalid-shape: part count", code="invalid-shape")
        sizes = tuple(self._part_size(part) for part in self.parts)
        if sum(sizes) > total_limit:
            raise PolicyError("invalid-shape: total UTF-8 bound", code="invalid-shape")
        if self.route is not None and self.profile != "canonical-v1":
            raise PolicyError(
                "binding-conflict: canonical route requires canonical profile", code="binding-conflict"
            )
        if self.route is not None:
            self.route.validate(self.request)
        object.__setattr__(self, "_parts_hash", _digest(self.parts))
        object.__setattr__(
            self,
            "copy_digest",
            _digest(
                {
                    "request": asdict(self.request),
                    "copy_id": self.copy_id,
                    "parts_hash": self.parts_hash,
                    "profile": self.profile,
                    "source_token": self.source_token,
                    "chunk_chars": self.chunk_chars,
                    "frame_bytes": self.frame_bytes,
                    "route": asdict(self.route) if self.route is not None else None,
                }
            ),
        )
        object.__setattr__(self, "_native_keys", tuple(self._native_key(i) for i in range(len(self.parts))))
        object.__setattr__(
            self, "text_hashes", tuple(hashlib.sha256(p.encode("utf-8")).hexdigest() for p in self.parts)
        )

    def _part_size(self, part: object) -> int:
        if not isinstance(part, str) or not part or len(part) > self.chunk_chars:
            raise PolicyError("invalid-shape: final part character bound", code="invalid-shape")
        try:
            size = len(part.encode("utf-8"))
        except UnicodeError as exc:
            raise PolicyError("invalid-shape: UTF-8 part", code="invalid-shape") from exc
        if size > self.frame_bytes or (self.profile == "canonical-v1" and size > 16 * 1024):
            raise PolicyError("invalid-shape: final part byte bound", code="invalid-shape")
        return size

    @property
    def parts_hash(self) -> str:
        return self._parts_hash

    def native_key(self, index: int) -> str:
        if type(index) is not int or not 0 <= index < len(self.parts):
            raise PolicyError("invalid-shape: part index", code="invalid-shape")
        return self._native_keys[index]

    def _native_key(self, index: int) -> str:
        if self.route is not None:
            return self.route.key(index)
        binding = {
            "version": 1,
            "request": asdict(self.request),
            "copy": self.copy_id,
            "parts_hash": self.parts_hash,
            "profile": self.profile,
            "part": index,
        }
        return "d6c:" + _digest(binding)[:48]


def check_duplicate(existing: FrozenCopy, candidate: FrozenCopy) -> None:
    """A duplicate request reuses the ORIGINAL copy; differing immutable payload refuses."""
    # A caller proposing a fresh random ID cannot replace an existing admitted copy's ID.
    if replace(candidate, copy_id=existing.copy_id) != existing:
        raise PolicyError("integrity: immutable copy conflict", code="integrity")


def check_native_collision(existing: FrozenCopy, candidate: FrozenCopy) -> None:
    """Owner admission must check its native-key index as well as immutable request-key dedup.

    Native legacy keys intentionally omit revision. Another payload under a retained native key is
    an integrity refusal, never permission to relabel an old receipt or let native dedup drop an update.
    This pairwise check is not the deferred atomic owner index or lineage authentication.
    """
    old_keys = {existing.native_key(i) for i in range(len(existing.parts))}
    if any(candidate.native_key(i) in old_keys for i in range(len(candidate.parts))):
        check_duplicate(existing, candidate)


@dataclass(frozen=True)
class AttemptIdentity:
    owner_store_uuid: str
    copy_id: str
    part_index: int
    ordinal: int

    def __post_init__(self) -> None:
        _text(self.owner_store_uuid)
        _hex(self.copy_id, 32)
        if type(self.part_index) is not int or not 0 <= self.part_index < 1024:
            raise PolicyError("invalid-shape: attempt part index", code="invalid-shape")
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 3:
            raise PolicyError("invalid-shape: bounded attempt ordinal", code="invalid-shape")


@dataclass(frozen=True)
class AttemptBinding:
    reply_target: str | None
    source_token: str
    registration_epoch: str
    copy_digest: str

    def __post_init__(self) -> None:
        if self.reply_target is not None:
            _hex(self.reply_target, 64)
        _text(self.source_token)
        _text(self.registration_epoch)
        _hex(self.copy_digest, 64)


@dataclass(frozen=True)
class Attempt:
    identity: AttemptIdentity
    phase: Phase
    message_id: str | None = None
    proof: ProofKind | None = None
    uncertainty: Uncertainty | None = None
    raw_ids: tuple[str, ...] = ()
    binding: AttemptBinding = field(kw_only=True)
    uncertainties: tuple[Uncertainty, ...] = ()
    proof_fact: "NonDispatchProof | None" = None
    accepted_receipt: "Receipt | None" = None
    dispatch_marked: bool = False
    contradicted: bool = False


def _validate_attempt(attempt: Attempt) -> None:
    if attempt.phase not in get_args(Phase) or type(attempt.binding) is not AttemptBinding:
        raise PolicyError("invalid-shape: attempt phase/binding", code="invalid-shape")
    _validate_dispatch_mark(attempt)
    if attempt.phase == "confirmed":
        _hex(attempt.message_id, 64)
    elif attempt.message_id is not None:
        raise PolicyError("integrity: nonconfirmed message ID", code="integrity")
    if attempt.uncertainty is not None and attempt.uncertainty not in get_args(Uncertainty):
        raise PolicyError("invalid-shape: uncertainty kind", code="invalid-shape")
    if attempt.proof is not None and attempt.proof not in get_args(ProofKind):
        raise PolicyError("invalid-shape: non-dispatch proof kind", code="invalid-shape")
    if (attempt.proof is not None) != (attempt.phase == "proven_unissued"):
        raise PolicyError("integrity: non-dispatch proof phase", code="integrity")
    _validate_settlement(attempt)
    if (attempt.uncertainty is not None) != (attempt.phase == "uncertain"):
        raise PolicyError("integrity: uncertainty phase", code="integrity")
    _validate_diagnosis(attempt)


def _validate_dispatch_mark(attempt: Attempt) -> None:
    if type(attempt.dispatch_marked) is not bool or type(attempt.contradicted) is not bool:
        raise PolicyError("invalid-shape: retained mark/contradiction flags", code="invalid-shape")
    if attempt.phase in {"dispatch-marked", "confirmed", "uncertain"} and not attempt.dispatch_marked:
        raise PolicyError("integrity: retained dispatch mark required", code="integrity")
    if attempt.phase == "reserved" and attempt.dispatch_marked:
        raise PolicyError("integrity: dispatch mark cannot become a reservation", code="integrity")


def _validate_settlement(attempt: Attempt) -> None:
    if attempt.proof is not None:
        fact = attempt.proof_fact
        if (
            not isinstance(fact, NonDispatchProof)
            or fact.identity != attempt.identity
            or fact.kind != attempt.proof
            or (fact.binding is not None and fact.binding != attempt.binding)
            or (
                attempt.dispatch_marked
                and not (
                    fact.kind == "prewrite-refusal"
                    and fact.write_not_invoked is True
                    and fact.binding == attempt.binding
                )
            )
        ):
            raise PolicyError("integrity: retained exact non-dispatch fact required", code="integrity")
    elif attempt.proof_fact is not None:
        raise PolicyError("integrity: unexpected non-dispatch fact", code="integrity")
    if attempt.phase == "confirmed":
        receipt = attempt.accepted_receipt
        if (
            not isinstance(receipt, Receipt)
            or receipt.identity != attempt.identity
            or receipt.binding != attempt.binding
            or receipt.message_ids != (attempt.message_id,)
        ):
            raise PolicyError("integrity: retained exact confirmed receipt required", code="integrity")
    elif attempt.accepted_receipt is not None:
        raise PolicyError("integrity: unexpected confirmed receipt", code="integrity")


def _validate_diagnosis(attempt: Attempt) -> None:
    if (
        type(attempt.uncertainties) is not tuple
        or len(attempt.uncertainties) > len(get_args(Uncertainty))
        or any(kind not in get_args(Uncertainty) for kind in attempt.uncertainties)
        or len(set(attempt.uncertainties)) != len(attempt.uncertainties)
    ):
        raise PolicyError("invalid-shape: bounded uncertainty history", code="invalid-shape")
    if attempt.phase == "uncertain" and attempt.uncertainty not in attempt.uncertainties:
        raise PolicyError("integrity: current uncertainty missing from retained history", code="integrity")
    if attempt.contradicted and "invalid-receipt" not in attempt.uncertainties:
        raise PolicyError("integrity: contradiction diagnosis required", code="integrity")
    if (
        attempt.phase in {"reserved", "dispatch-marked", "proven_unissued"}
        and attempt.uncertainties
        and not attempt.contradicted
    ):
        raise PolicyError("integrity: uncertainty cannot be erased by phase relabeling", code="integrity")
    if _bounded_ids(attempt.raw_ids) != attempt.raw_ids:
        raise PolicyError("invalid-shape: bounded retained receipt IDs", code="invalid-shape")


@dataclass(frozen=True)
class PartState:
    copy: FrozenCopy
    owner_store_uuid: str
    index: int
    eligible_at: float
    history: tuple[Attempt, ...] = ()
    cancelled: bool = False
    superseded: bool = False
    reply_target: str | None = None

    def __post_init__(self) -> None:
        _text(self.owner_store_uuid)  # owner identity is opaque; no new UUID wire grammar is assumed
        self.copy.native_key(self.index)
        _clock(self.eligible_at)
        if type(self.history) is not tuple or len(self.history) > 3:
            raise PolicyError("invalid-shape: bounded immutable attempt history", code="invalid-shape")
        if type(self.cancelled) is not bool or type(self.superseded) is not bool:
            raise PolicyError("invalid-shape: lifecycle flags", code="invalid-shape")
        for ordinal, attempt in enumerate(self.history, 1):
            expected = AttemptIdentity(self.owner_store_uuid, self.copy.copy_id, self.index, ordinal)
            if attempt.identity != expected or attempt.binding != self.binding:
                raise PolicyError("binding-conflict: attempt identity/history", code="binding-conflict")
            if ordinal < len(self.history) and attempt.phase != "proven_unissued":
                raise PolicyError("integrity: retry without prior positive non-dispatch", code="integrity")
            _validate_attempt(attempt)
        if self.cancelled and self.history and self.attempt.phase != "proven_unissued":
            raise PolicyError("integrity: cancelled issued/uncertain history", code="integrity")

    @property
    def binding(self) -> AttemptBinding:
        token = self.copy.source_token
        return AttemptBinding(
            self.reply_target, token, self.copy.request.destination.epoch, self.copy.copy_digest
        )

    @property
    def attempt(self) -> Attempt:
        if not self.history:
            raise PolicyError("held: no reserved attempt", code="contention")
        return self.history[-1]

    @property
    def native_key(self) -> str:
        return self.copy.native_key(self.index)

    @property
    def text_hash(self) -> str:
        return self.copy.text_hashes[self.index]


@dataclass(frozen=True)
class Gates:
    """Owner facts for THIS frozen copy, not a callback or a route authorization API."""

    source_current: bool
    trust_current: bool
    parent_confirmed: bool
    binding: AttemptBinding | None = None
    parent: PartState | None = None


@dataclass(frozen=True)
class NonDispatchProof:
    """A correlated owner-supplied fact. No file/transport proof is produced by this module.

    Healthy-absence and settlement of recovered uncertainty require the deferred durable owner
    protocol; they are deliberately unavailable through this partial in-memory reducer.
    """

    identity: AttemptIdentity
    kind: ProofKind
    write_not_invoked: bool = False
    binding: AttemptBinding | None = None

    def __post_init__(self) -> None:
        if self.kind not in get_args(ProofKind) or type(self.write_not_invoked) is not bool:
            raise PolicyError("invalid-shape: positive non-dispatch proof required", code="invalid-shape")


@dataclass(frozen=True)
class Receipt:
    identity: AttemptIdentity
    account: str
    group: str
    native_key: str
    text_hash: str
    message_ids: tuple[str, ...]
    binding: AttemptBinding | None = None


@dataclass(frozen=True)
class ReceiptClaim:
    message_id: str
    owner_store_uuid: str
    copy_id: str
    part_index: int
    ordinal: int | None = None


def _effect_policy(state: PartState) -> None:
    if state.copy.request.reason == "reminder":
        raise PolicyError(
            "unsupported-policy: durable reminder owner budget unavailable", code="unsupported-policy"
        )
    if any(attempt.contradicted for attempt in state.history):
        raise PolicyError("held: contradictory native evidence", code="recovery-required")


def reserve(state: PartState, now: float, gates: Gates) -> PartState:
    """Pure reservation proposal. The owner must durably commit it BEFORE any dispatch mark/write."""
    _clock(now)
    _effect_policy(state)
    if state.cancelled or state.superseded:
        raise PolicyError("held: inactive copy", code="source-closed")
    if gates.source_current is not True:
        raise PolicyError("held: current source required", code="source-changed")
    if gates.trust_current is not True:
        raise PolicyError("held: current trust required", code="trust-denied")
    if gates.parent_confirmed is not True or (state.index > 0 and not _parent_matches(state, gates.parent)):
        raise PolicyError("held: exact confirmed parent required", code="parent-incomplete")
    if state.history and state.attempt.phase != "proven_unissued":
        raise PolicyError("held: no positive non-dispatch for next attempt", code="contention")
    ordinal = len(state.history) + 1
    if ordinal > 3:
        raise PolicyError("exhausted-held", code="exhausted")
    if now < state.eligible_at + (0, 5, 30)[ordinal - 1]:
        raise PolicyError("not-due", code="contention")
    _validate_gate_binding(state, gates.binding)
    identity = AttemptIdentity(state.owner_store_uuid, state.copy.copy_id, state.index, ordinal)
    return replace(state, history=(*state.history, Attempt(identity, "reserved", binding=state.binding)))


def _validate_gate_binding(state: PartState, binding: AttemptBinding | None) -> None:
    if binding is None:
        raise PolicyError("held: current binding unavailable", code="source-unavailable")
    if binding.source_token != state.binding.source_token:
        raise PolicyError("held: current source binding changed", code="source-changed")
    if binding.registration_epoch != state.binding.registration_epoch:
        raise PolicyError("held: current registration changed", code="trust-denied")
    if binding.reply_target != state.binding.reply_target:
        raise PolicyError("held: current parent changed", code="parent-incomplete")
    if binding.copy_digest != state.binding.copy_digest:
        raise PolicyError("binding-conflict: exact immutable copy gates required", code="binding-conflict")


def _parent_matches(state: PartState, parent: PartState | None) -> bool:
    return (
        parent is not None
        and parent.index == 0
        and parent.copy == state.copy
        and parent.owner_store_uuid == state.owner_store_uuid
        and not parent.cancelled
        and not parent.superseded
        and bool(parent.history)
        and parent.attempt.phase == "confirmed"
        and not any(a.contradicted for a in parent.history)
        and state.reply_target == parent.attempt.message_id
    )


def _update(state: PartState, attempt: Attempt) -> PartState:
    return replace(state, history=(*state.history[:-1], attempt))


def mark_dispatch(state: PartState) -> PartState:
    """Propose the mark the owner MUST commit through its recovery barrier before writer.write."""
    _effect_policy(state)
    if state.cancelled or state.superseded or state.attempt.phase != "reserved":
        raise PolicyError("held: dispatch requires an active reservation", code="contention")
    return _update(state, replace(state.attempt, phase="dispatch-marked", dispatch_marked=True))


def prove_unissued(state: PartState, proof: NonDispatchProof) -> PartState:
    same = proof.identity == state.attempt.identity
    reserved = state.attempt.phase == "reserved"
    prewrite = (
        state.attempt.phase == "dispatch-marked"
        and proof.kind == "prewrite-refusal"
        and proof.write_not_invoked is True
        and proof.binding == state.attempt.binding
    )
    if not same or not (reserved or prewrite):
        raise PolicyError("held: proof conflicts with attempt or dispatch evidence", code="binding-conflict")
    return _update(state, replace(state.attempt, phase="proven_unissued", proof=proof.kind, proof_fact=proof))


def mark_uncertain(state: PartState, phase: Uncertainty) -> PartState:
    if phase not in get_args(Uncertainty):
        raise PolicyError("invalid-shape: uncertainty phase", code="invalid-shape")
    if state.attempt.phase not in {"dispatch-marked", "uncertain"}:
        raise PolicyError("held: uncertainty requires dispatch evidence", code="contention")
    kinds = tuple(dict.fromkeys((*state.attempt.uncertainties, phase)))
    return _update(state, replace(state.attempt, phase="uncertain", uncertainty=phase, uncertainties=kinds))


def _correlated(state: PartState, attempt: Attempt, receipt: Receipt) -> bool:
    return (
        receipt.identity,
        receipt.account,
        receipt.group,
        receipt.native_key,
        receipt.text_hash,
        receipt.binding,
    ) == (
        attempt.identity,
        state.copy.request.destination.account,
        state.copy.request.destination.group,
        state.native_key,
        state.text_hash,
        attempt.binding,
    )


def _receipt_matches(
    state: PartState, attempt: Attempt, receipt: Receipt, claims: tuple[ReceiptClaim, ...]
) -> bool:
    ids = receipt.message_ids
    if not _correlated(state, attempt, receipt) or type(ids) is not tuple or len(ids) != 1:
        return False
    try:
        _hex(ids[0], 64)
    except PolicyError:
        return False
    own = ReceiptClaim(
        ids[0], state.owner_store_uuid, state.copy.copy_id, state.index, attempt.identity.ordinal
    )
    return all(claim == own for claim in claims if claim.message_id == ids[0])


def _bounded_ids(ids: tuple[object, ...] | None) -> tuple[str, ...]:
    # Keep a bounded raw prefix for diagnosis, never convert it into a success or a proof.
    if type(ids) is not tuple:
        return ()
    kept: list[str] = []
    remaining = 16 * 1024
    for value in ids[:64]:
        if type(value) is not str:
            break
        raw = value[:remaining].encode("utf-8", errors="replace")[:remaining]
        kept.append(raw.decode("utf-8", errors="ignore"))
        remaining -= len(raw)
        if not remaining:
            break
    return tuple(kept)


def _diagnose(attempt: Attempt, receipt: Receipt) -> Attempt:
    raw = _bounded_ids((*attempt.raw_ids, *_bounded_ids(receipt.message_ids)))
    kinds = tuple(dict.fromkeys((*attempt.uncertainties, "invalid-receipt")))
    return replace(attempt, raw_ids=raw, uncertainties=kinds, contradicted=True)


def _retained_receipt(
    state: PartState, receipt: object, claims: tuple[ReceiptClaim, ...]
) -> PartState | None:
    if isinstance(receipt, Receipt):
        for index, attempt in enumerate(state.history):
            if not _correlated(state, attempt, receipt):
                continue
            if attempt.phase in {"confirmed", "proven_unissued"}:
                if (
                    attempt.phase == "confirmed"
                    and receipt == attempt.accepted_receipt
                    and _receipt_matches(state, attempt, receipt, claims)
                ):
                    return state
                history = list(state.history)
                history[index] = _diagnose(attempt, receipt)
                return replace(state, history=tuple(history))
    return None


def accept_receipt(state: PartState, receipt: object, claims: tuple[ReceiptClaim, ...] = ()) -> PartState:
    """Retain exact correlated contradictions on their original bounded ordinal; never replays."""
    current = state.attempt
    retained = _retained_receipt(state, receipt, claims)
    if retained is not None:
        return retained
    if current.phase not in {"dispatch-marked", "uncertain"}:
        raise PolicyError("held: receipt without dispatch evidence", code="contention")
    if not isinstance(receipt, Receipt):
        return mark_uncertain(state, "invalid-receipt")
    valid = _receipt_matches(state, current, receipt, claims)
    raw = (
        _bounded_ids((*current.raw_ids, *_bounded_ids(receipt.message_ids)))
        if _correlated(state, current, receipt)
        else current.raw_ids
    )
    kinds = (
        current.uncertainties if valid else tuple(dict.fromkeys((*current.uncertainties, "invalid-receipt")))
    )
    attempt = replace(
        current,
        phase="confirmed" if valid else "uncertain",
        message_id=receipt.message_ids[0] if valid else None,
        uncertainty=None if valid else "invalid-receipt",
        raw_ids=raw,
        uncertainties=kinds,
        accepted_receipt=receipt if valid else None,
    )
    return _update(state, attempt)


def cancel(state: PartState) -> PartState:
    if any(attempt.contradicted for attempt in state.history):
        raise PolicyError("held: contradictory native evidence", code="recovery-required")
    if state.history and state.attempt.phase != "proven_unissued":
        raise PolicyError(
            "held: cancellation needs positive non-dispatch or never-issued evidence",
            code="recovery-required",
        )
    return replace(state, cancelled=True)


def supersede(state: PartState) -> PartState:
    """Retain issued/uncertain history; never relabel it cancelled or restart the attempt budget."""
    return replace(state, superseded=True)


def part_status(state: PartState) -> str:
    if any(attempt.contradicted for attempt in state.history):
        return "uncertain-held"
    if state.cancelled:
        return "cancelled"
    if state.superseded:
        return (
            "superseded-uncertain-held"
            if state.history and state.attempt.phase in {"dispatch-marked", "uncertain"}
            else "superseded"
        )
    if not state.history:
        return "prepared"
    if state.attempt.phase in {"dispatch-marked", "uncertain"}:
        return "uncertain-held"
    if state.attempt.phase == "proven_unissued" and len(state.history) == 3:
        return "exhausted-held"
    return state.attempt.phase


def copy_status(states: tuple[PartState, ...]) -> str:
    if not states:
        raise PolicyError("invalid-shape: empty copy", code="invalid-shape")
    first = states[0]
    if (
        len(states) != len(first.copy.parts)
        or tuple(state.index for state in states) != tuple(range(len(states)))
        or any(
            state.copy != first.copy or state.owner_store_uuid != first.owner_store_uuid for state in states
        )
    ):
        raise PolicyError("binding-conflict: all exact parts of one copy required", code="binding-conflict")
    ids = [
        state.attempt.message_id for state in states if state.history and state.attempt.phase == "confirmed"
    ]
    wrong_parent = any(
        state.history and state.attempt.phase == "confirmed" and not _parent_matches(state, first)
        for state in states[1:]
    )
    unresolved = (
        wrong_parent
        or any(attempt.contradicted for state in states for attempt in state.history)
        or len(ids) != len(set(ids))
        or any(state.history and state.attempt.phase in {"dispatch-marked", "uncertain"} for state in states)
    )
    if any(state.superseded for state in states):
        return "superseded-uncertain-held" if unresolved else "superseded"
    if unresolved:
        return "uncertain-held"
    if all(state.cancelled for state in states):
        return "cancelled"
    if any(part_status(state) == "exhausted-held" for state in states):
        return "exhausted-held"
    if len(ids) == len(states) and not any(state.cancelled for state in states):
        return "complete"
    return "partial-held"
