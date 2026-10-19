"""Closed gateway custody contracts. Parsing proves shape, never native consent.

Independent enrolled transport, snapshot, policy and queue authority are required
before these contracts may be served. Maintenance recovery authority is distinct.
"""

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

import msgspec

from heterodyne.admind import recovery
from heterodyne.admind.store import Store

Invalid = recovery.Invalid
MAX_REQUEST = 256 * 1024
MAX_DESCRIPTOR = 16 * 1024
MAX_BUNDLE = 32 * 1024
# Bound routine diagnostics independently and reserve 8 KiB of every bundle
# for all three closed permits and two certificates. Descriptor/notice/decision
# must fit the remaining bytes; physical WAL/index reserve is a separate gate.
MAX_COMMAND_BYTES = 8 * 1024
PERMIT_RESERVE = 8 * 1024
MAX_TOTAL = 128 * 1024 * 1024
DAY_NS = 86_400_000_000_000
WINDOW_NS = 30_000_000_000
UINT64 = 2**64 - 1
CLOCK = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z")
COMMAND = re.compile(r"!custody (settle|decline|revoke) ([0-9a-f]{24})\Z")
Action = Literal[
    "retain",
    "retry-positive-not-issued",
    "resume-sealed-result",
    "consume-proven-delivery",
    "archive-unknown",
    "export-terminal-epoch",
]


class Descriptor(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["gateway-custody-resolution.v1"]
    proposal: str
    event: str
    identity_sha256: str
    chain_root: str
    attempts_sha256: str
    proofs_sha256: str
    source_closure: str
    enrollment_sha256: str
    profile_sha256: str
    boot_id: str
    created_boottime_ns: str
    expires_boottime_ns: str
    action: Action
    work_generation: int


class PermitRecord(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["gateway-custody-permit.v1"]
    permit_id: str
    proposal_sha256: str
    descriptor_sha256: str
    decision_message_id: str
    operator: str
    policy_sha256: str
    boot_id: str
    issued_boottime_ns: str
    initiate_before_boottime_ns: str
    permit_generation: int
    previous_permit_sha256: str | None


class Nonconsumption(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["gateway-custody-nonconsumption.v1"]
    proposal_sha256: str
    permit_sha256: str
    permit_generation: int
    descriptor_sha256: str
    chain_root: str
    boot_id: str
    observed_boottime_ns: str


class GatewayCustodyRegister(msgspec.Struct, tag="register", frozen=True, forbid_unknown_fields=True):
    descriptor: Descriptor


class GatewayCustodyStatus(msgspec.Struct, tag="status", frozen=True, forbid_unknown_fields=True):
    proposal_sha256: str


class GatewayCustodyPermit(msgspec.Struct, tag="permit", frozen=True, forbid_unknown_fields=True):
    proposal_sha256: str
    nonconsumption_sha256: str | None


Request = GatewayCustodyRegister | GatewayCustodyStatus | GatewayCustodyPermit


def _tree(value: object, depth: int = 0) -> None:
    """Apply the common closed grammar before typed decoding (including ignored values)."""
    if depth > 12 or isinstance(value, float):
        raise Invalid("json-grammar")
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _tree(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            _tree(key, depth + 1)
            _tree(item, depth + 1)


def decode[T](raw: bytes, kind: type[T], *, limit: int = MAX_REQUEST) -> T:
    if len(raw) > limit:
        raise Invalid("oversized")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=recovery.pairs)
        _tree(value)
        return msgspec.convert(value, type=kind, strict=True)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise Invalid("json-grammar") from exc


def canonical(value: object) -> bytes:
    """Exact shared canonical encoding; no normalization, newline or nonfinite values."""
    obj = msgspec.to_builtins(value)
    _tree(obj)
    return recovery.canonical(obj)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def clock(value: str) -> int:
    if not CLOCK.fullmatch(value) or int(value) > UINT64:
        raise Invalid("clock")
    return int(value)


def _uuid(value: str) -> None:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise Invalid("uuid") from None


def _hash(value: object) -> None:
    if not isinstance(value, str) or not recovery.HEX.fullmatch(value):
        raise Invalid("hash")


def _generation(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 3:
        raise Invalid("generation")


def _identifier(value: str) -> None:
    if not 1 <= len(value.encode("utf-8")) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Invalid("identifier")


def descriptor(raw: bytes) -> Descriptor:
    obj = decode(raw, Descriptor, limit=MAX_DESCRIPTOR)
    for value in (obj.proposal, obj.event, obj.boot_id):
        _uuid(value)
    for value in (
        obj.identity_sha256,
        obj.chain_root,
        obj.attempts_sha256,
        obj.proofs_sha256,
        obj.source_closure,
        obj.enrollment_sha256,
        obj.profile_sha256,
    ):
        _hash(value)
    _generation(obj.work_generation)
    if clock(obj.expires_boottime_ns) != clock(obj.created_boottime_ns) + DAY_NS:
        raise Invalid("proposal-window")
    return obj


def permit(raw: bytes) -> PermitRecord:
    obj = decode(raw, PermitRecord, limit=MAX_BUNDLE)
    _uuid(obj.permit_id)
    _uuid(obj.boot_id)
    for value in (obj.proposal_sha256, obj.descriptor_sha256, obj.policy_sha256):
        _hash(value)
    _identifier(obj.operator)
    _identifier(obj.decision_message_id)
    _generation(obj.permit_generation)
    if (obj.previous_permit_sha256 is None) != (obj.permit_generation == 1):
        raise Invalid("permit-link")
    if obj.previous_permit_sha256 is not None:
        _hash(obj.previous_permit_sha256)
    if clock(obj.initiate_before_boottime_ns) != clock(obj.issued_boottime_ns) + WINDOW_NS:
        raise Invalid("permit-window")
    return obj


def check(req: Request) -> str | None:
    try:
        decoded = decode(canonical(req), cast(type[Request], Request))
        if isinstance(decoded, GatewayCustodyRegister):
            descriptor(canonical(decoded.descriptor))
        else:
            _hash(decoded.proposal_sha256)
            if isinstance(decoded, GatewayCustodyPermit) and decoded.nonconsumption_sha256 is not None:
                _hash(decoded.nonconsumption_sha256)
    except (Invalid, ValueError, TypeError):
        return "invalid-custody-request"
    return None


def command(text: str) -> tuple[str, str] | None:
    """Exact grammar only. Actual account/thread/principal checks remain mandatory."""
    found = COMMAND.fullmatch(text)
    return None if found is None else (found[1], found[2])


def renewal(
    obj: Descriptor,
    previous: PermitRecord,
    certificate: bytes,
    superseded: bytes,
    *,
    boot_id: str,
    boottime_ns: int,
) -> Nonconsumption:
    """Compare source-read EX certificates. This pure check cannot mint a permit.

    Both files must be read through the enrolled queue owner. Passing caller JSON
    here does not authenticate either a queue observation or a human decision.
    """
    descriptor(canonical(obj))
    permit(canonical(previous))
    if certificate != superseded:
        raise Invalid("nonconsumption-tombstone-mismatch")
    proof = decode(certificate, Nonconsumption, limit=MAX_BUNDLE)
    if canonical(proof) != certificate:
        raise Invalid("noncanonical-certificate")
    _generation(proof.permit_generation)
    _uuid(proof.boot_id)
    for value in (proof.proposal_sha256, proof.permit_sha256, proof.descriptor_sha256, proof.chain_root):
        _hash(value)
    if type(boottime_ns) is not int or not 0 <= boottime_ns <= UINT64:
        raise Invalid("clock")
    if (
        previous.permit_generation >= 3
        or proof.permit_generation != previous.permit_generation
        or proof.permit_sha256 != digest(previous)
        or proof.proposal_sha256 != digest(obj)
        or proof.proposal_sha256 != previous.proposal_sha256
        or proof.descriptor_sha256 != digest(obj)
        or proof.descriptor_sha256 != previous.descriptor_sha256
        or proof.chain_root != obj.chain_root
        or boot_id != obj.boot_id
        or boot_id != previous.boot_id
        or boot_id != proof.boot_id
    ):
        raise Invalid("nonconsumption-scope")
    if not (
        clock(obj.created_boottime_ns)
        <= clock(previous.issued_boottime_ns)
        <= clock(previous.initiate_before_boottime_ns)
        <= clock(proof.observed_boottime_ns)
        <= boottime_ns
        < clock(obj.expires_boottime_ns)
    ):
        raise Invalid("nonconsumption-clock")
    # Reissuance may not extend beyond the immutable original proposal expiry.
    if boottime_ns + WINDOW_NS > clock(obj.expires_boottime_ns):
        raise Invalid("proposal-expiring")
    return proof


CONSEQUENCES: dict[Action, str] = {
    "retain": "Retain the original evidence; unknown work and publication holds remain.",
    "retry-positive-not-issued": (
        "Permit a bounded new generation only after proof that all prior effects never started."
    ),
    "resume-sealed-result": "Resume delivery of the original sealed invocation; never rerun the model.",
    "consume-proven-delivery": "Record consumption only with exact native consumer acknowledgment.",
    "archive-unknown": "Free active storage only; preserve unresolved obligations and publication holds.",
    "export-terminal-epoch": (
        "Transfer exact archives only with approved receiver, retention and verified readback."
    ),
}


def notice(obj: Descriptor, original_actor: str, evidence_url: str, max_chars: int) -> str:
    """One material notice. A source-owned enrollment must separately authenticate its link."""
    from heterodyne.admind import links
    from heterodyne.admind.redact import redact

    descriptor(canonical(obj))
    _identifier(original_actor)
    if (
        any(not " " <= char <= "~" for char in original_actor)
        or type(max_chars) is not int
        or max_chars <= 0
        or len(evidence_url) > links.MAX_URL
        or "%" in evidence_url
        or links.why(links.LinkSpec("Exact proposal", evidence_url)) is not None
        or evidence_url.split("/")[5] != "blob"
    ):
        raise Invalid("notice-material-unrepresentable")
    token = digest(obj)[:24]
    lines = [
        "APPROVAL: Gateway custody",
        f"Event: {obj.event}",
        f"Original actor: {original_actor}",
        f"Action: {obj.action}",
        f"Work generation: {obj.work_generation} of 3",
        CONSEQUENCES[obj.action],
        f"Exact proposal, source and proofs: {evidence_url}",
        f"Expires at BOOTTIME: {obj.expires_boottime_ns} ns on boot {obj.boot_id}",
        f"Approve: !custody settle {token}",
        f"Decline: !custody decline {token}",
        f"Revoke unissued authority: !custody revoke {token}",
        "An issued permit may initiate for 30 seconds after revocation.",
        "Only exact commands from a current independent operator in this notice thread count.",
        "Words, reactions and local caller claims do not authorize custody resolution.",
        "This does not approve deployment, replay unknown effects or clear unrelated holds.",
    ]
    text = "\n".join(lines)
    if len(lines) > 36 or len(text) > min(max_chars, 3200) or redact(text) != text:
        raise Invalid("notice-material-unrepresentable")
    return text


@dataclass(frozen=True)
class Observation:
    """Fixed enrolled source adapter output, never a wire/client assertion.

    Production source-root enrollment and independent transport are not supplied
    by this module. Disposable tests implement the port only as synthetic evidence.
    """

    descriptor: bytes
    original_actor: str
    evidence_url: str
    boot_id: str
    boottime_ns: int


@dataclass(frozen=True)
class Publication:
    """Installed source-owner locators, never RPC fields or maintenance authority.

    An enrolled adapter must reproduce these roots and pins from its own fixed
    source manifest. No production adapter is installed by this implementation.
    """

    root: Path
    queue_root: Path
    enrollment_sha256: str
    source_closure: str
    profile_sha256: str


def private_root(path: Path) -> None:
    """Refuse symlinks in every existing component before any publication effect."""
    if not path.is_absolute() or ".." in path.parts:
        raise Invalid("unsafe-root")
    for parent in [*reversed(path.parents), path]:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            raise Invalid("missing-root") from None
        if not stat.S_ISDIR(info.st_mode):
            raise Invalid("unsafe-root")
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Invalid("unsafe-root")


def publication_paths(enrolled: Publication, key: str, generation: int) -> tuple[Path, Path]:
    _hash(key)
    _generation(generation)
    private_root(enrolled.root)
    private_root(enrolled.queue_root)
    generation_path = enrolled.root / "generations" / key / f"{generation}.json"
    current_path = enrolled.root / "permits" / f"{key}.json"
    # Existing internal components must also be private directories. The shared
    # publisher creates missing descendants, but never follows an existing alias.
    for parent in (generation_path.parent.parent, generation_path.parent, current_path.parent):
        if os.path.lexists(parent):
            private_root(parent)
    return generation_path, current_path


REGISTRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_custody_proposals (
 hash TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, proposal TEXT NOT NULL UNIQUE,
 event TEXT NOT NULL, bundle BLOB NOT NULL, notice_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('awaiting-consent','consented','revoked','terminal')));
"""


class Status(TypedDict):
    proposal_sha256: str
    # Heterogeneous retained JSON readback, never an authority input. Issuance
    # separately decodes the closed descriptor/permit and verifies source ports.
    bundle: dict[str, Any]
    notice_key: str
    state: str
    notice_status: str


class Service:
    """Independent custody consent and retained permit owner; never an executor.

    Wired to dormant daemon control: production activation still requires independently
    enrolled source/transport adapters, native command custody and queue settlement.
    An absent source refuses before creation of any custody table or notice.
    """

    def __init__(
        self,
        store: "Store",
        *,
        source: Callable[[str], Observation] | None = None,
        authority: Callable[[str, str, str], str | None] | None = None,
        publication: Callable[[], Publication] | None = None,
    ):
        self.store = store
        self.source = source
        self.authority = authority
        self.publication = publication
        # Serializes only this service's writers; fsync must never hold Store.lock.
        self.publication_lock = threading.RLock()

    def observation(self, key: str, bundle: dict[str, object]) -> Observation:
        if self.source is None:
            raise Invalid("source-unavailable")
        obj = descriptor(canonical(bundle["descriptor"]))
        observed = self.source(key)
        self.validate_observation(obj, observed)
        if (
            observed.original_actor != bundle["original_actor"]
            or observed.evidence_url != bundle["evidence_url"]
        ):
            raise Invalid("source-mismatch")
        return observed

    @staticmethod
    def validate_observation(obj: Descriptor, observed: Observation) -> None:
        if (
            observed.descriptor != canonical(obj)
            or observed.boot_id != obj.boot_id
            or type(observed.boottime_ns) is not int
            or not clock(obj.created_boottime_ns) <= observed.boottime_ns < clock(obj.expires_boottime_ns)
        ):
            raise Invalid("source-mismatch")

    def issue_permit(self, req: GatewayCustodyPermit, *, uid: int | None) -> bytes:
        return self._issue_permit(req, uid=uid)

    def _issue_permit(self, req: GatewayCustodyPermit, *, uid: int | None) -> bytes:
        """Retain before publishing; an interrupted call repairs only identical bytes.

        New generations require queue-owned EX nonconsumption plus identical
        supersession tombstone. The local socket cannot supply a certificate body.
        This grants no queue execution authority unless its distinct enrolled
        consumer independently verifies the complete source and single-use chain.
        """
        self.peer(uid)
        if check(req) is not None:
            raise Invalid("invalid-custody-request")
        if self.publication is None:
            raise Invalid("publication-unavailable")
        enrolled = self.publication()
        query = "SELECT bundle,state FROM gateway_custody_proposals WHERE hash=?"
        with self.store.transaction(read_only=True):
            row = self.store.db.execute(query, (req.proposal_sha256,)).fetchone()
        if row is None or row[1] != "consented":
            raise Invalid("not-consented")
        bundle = cast(dict[str, object], json.loads(bytes(row[0])))
        obj = descriptor(canonical(bundle["descriptor"]))
        if (enrolled.enrollment_sha256, enrolled.source_closure, enrolled.profile_sha256) != (
            obj.enrollment_sha256,
            obj.source_closure,
            obj.profile_sha256,
        ) or enrolled.root == enrolled.queue_root:
            raise Invalid("publication-enrollment")
        decision = cast(dict[str, str], bundle["decision"])
        if self.authority is None:
            raise Invalid("authority-unavailable")
        policy = self.authority(decision["account"], decision["group"], decision["sender"])
        if policy is None or policy != decision["policy_sha256"]:
            raise Invalid("policy-drift")
        _hash(policy)
        observed = self.observation(req.proposal_sha256, bundle)
        records = cast(list[dict[str, object]], bundle.get("permits", []))
        previous = permit(canonical(records[-1])) if records else None
        immutable = current = None
        tombstone_proof = None
        tombstone_generation = None
        retained_proofs = cast(list[dict[str, object]], bundle.get("certificates", []))
        retained_proof = canonical(retained_proofs[-1]) if retained_proofs else None
        # A request naming the already retained predecessor proof retries the same
        # candidate. It cannot accidentally ask for generation N+1.
        retry = previous is not None and (
            req.nonconsumption_sha256 is None
            or (
                retained_proof is not None
                and req.nonconsumption_sha256 == hashlib.sha256(retained_proof).hexdigest()
            )
        )
        complete = False
        if retry and previous is not None:
            encoded = canonical(previous)
            generation = previous.permit_generation
            immutable, current = publication_paths(enrolled, req.proposal_sha256, generation)
            complete = self._published(immutable, encoded) and self._published(current, encoded)
            if generation > 1:
                tombstone_generation = generation - 1
                tombstone_proof = retained_proof
                complete = (
                    complete
                    and retained_proof is not None
                    and self._published(
                        immutable.parent / f"{generation - 1}.superseded.json", retained_proof
                    )
                )
            if not complete or generation == 1:
                if generation > 1:
                    # Incomplete publication needs the original queue observation,
                    # validated at the retained candidate's issuance time. No new
                    # 30-second window is created by repairing identical bytes.
                    predecessor = permit(canonical(records[-2]))
                    repair = GatewayCustodyPermit(
                        req.proposal_sha256, hashlib.sha256(cast(bytes, retained_proof)).hexdigest()
                    )
                    historical = Observation(
                        observed.descriptor,
                        observed.original_actor,
                        observed.evidence_url,
                        observed.boot_id,
                        clock(previous.issued_boottime_ns),
                    )
                    tombstone_proof = self.permit_certificate(repair, enrolled, obj, predecessor, historical)
                else:
                    self.permit_certificate(req, enrolled, obj, previous, observed)
                    prefix = enrolled.queue_root / req.proposal_sha256
                    if os.path.lexists(Path(f"{prefix}.superseded-1.json")):
                        raise Invalid("permit-superseded")
        else:
            proof = self.permit_certificate(req, enrolled, obj, previous, observed)
            encoded = self.new_permit(obj, bundle, observed, previous, proof)
            generation = permit(encoded).permit_generation
            tombstone_proof = proof
            tombstone_generation = None if previous is None else previous.permit_generation
            immutable, current = publication_paths(enrolled, req.proposal_sha256, generation)
        if self.publication() != enrolled:
            raise Invalid("publication-drift")
        self.observation(req.proposal_sha256, bundle)
        if self.authority(decision["account"], decision["group"], decision["sender"]) != policy:
            raise Invalid("authority-drift")
        updated = canonical(bundle)
        # Serialize pointer publication with every local consent/permit writer.
        # Policy/source callbacks remain outside Store locks. A crash after commit
        # retains the candidate for exact repair; it never creates another permit.
        with self.publication_lock:
            with self.store.transaction():
                if self.store.db.execute(query, (req.proposal_sha256,)).fetchone() != row:
                    raise Invalid("proposal-changed")
                if not self.capacity(updated, len(row[0])):
                    raise Invalid("bundle-capacity")
                if updated != bytes(row[0]):
                    self.store.db.execute(
                        "UPDATE gateway_custody_proposals SET bundle=? WHERE hash=?",
                        (updated, req.proposal_sha256),
                    )
            if complete:
                return encoded
            if tombstone_proof is not None and tombstone_generation is not None:
                # Copy authentic queue-owned certificate bytes, never invent evidence
                # and never destroy the immutable original generation.
                tombstone = immutable.parent / f"{tombstone_generation}.superseded.json"
                if not self._published(tombstone, tombstone_proof):
                    recovery.publish(tombstone, tombstone_proof)
                if recovery.private_read(tombstone) != tombstone_proof:
                    raise Invalid("publication-readback")
            if not self._published(immutable, encoded):
                recovery.publish(immutable, encoded)
            if recovery.private_read(immutable) != encoded:
                raise Invalid("publication-readback")
            if not self._published(current, encoded):
                recovery.replace_current(current, encoded)
            if recovery.private_read(current) != encoded:
                raise Invalid("publication-readback")
        return encoded

    @staticmethod
    def _published(path: Path, raw: bytes) -> bool:
        """Exact existing readback only; absence grants no execution authority."""
        if not os.path.lexists(path):
            return False
        return recovery.private_read(path) == raw

    def capacity(self, updated: bytes, old_size: int = 0) -> bool:
        """Call only inside the existing Store transaction; payload reserve is separate."""
        total = self.store.db.execute(
            "SELECT coalesce(sum(length(bundle)),0) FROM gateway_custody_proposals"
        ).fetchone()[0]
        return len(updated) <= MAX_BUNDLE and total - old_size + len(updated) <= MAX_TOTAL

    def notice_status(self, key: str) -> str:
        row = self.store.db.execute("SELECT status FROM outbox WHERE key=?", (key + ":0",)).fetchone()
        return "missing" if row is None else str(row[0])

    @staticmethod
    def new_permit(
        obj: Descriptor,
        bundle: dict[str, object],
        observed: Observation,
        previous: PermitRecord | None,
        proof: bytes | None,
    ) -> bytes:
        """Construct retained candidate bytes only; no authorization or publication."""
        decision = cast(dict[str, str], bundle["decision"])
        policy = decision["policy_sha256"]
        records = cast(list[dict[str, object]], bundle.get("permits", []))
        if observed.boottime_ns + WINDOW_NS > clock(obj.expires_boottime_ns):
            raise Invalid("proposal-expiring")
        generation = 1 if previous is None else previous.permit_generation + 1
        value = PermitRecord(
            "gateway-custody-permit.v1",
            str(uuid.uuid4()),
            digest(obj),
            digest(obj),
            decision["message_id"],
            decision["sender"],
            policy,
            observed.boot_id,
            str(observed.boottime_ns),
            str(observed.boottime_ns + WINDOW_NS),
            generation,
            None if previous is None else digest(previous),
        )
        encoded = canonical(permit(canonical(value)))
        records = [*records, cast(dict[str, object], msgspec.to_builtins(value))]
        bundle["permits"] = records
        if proof is not None:
            proofs = cast(list[dict[str, object]], bundle.get("certificates", []))
            bundle["certificates"] = [*proofs, cast(dict[str, object], json.loads(proof))]
        return encoded

    @staticmethod
    def permit_certificate(
        req: GatewayCustodyPermit,
        enrolled: Publication,
        obj: Descriptor,
        previous: PermitRecord | None,
        observed: Observation,
    ) -> bytes | None:
        """Read only the exact queue-owned certificate pair, never supplied JSON."""
        proof: bytes | None = None
        private_root(enrolled.queue_root)
        prefix = enrolled.queue_root / req.proposal_sha256
        for suffix in ("consumed", "issuing", "receipt"):
            if os.path.lexists(Path(f"{prefix}.{suffix}.json")):
                raise Invalid("consumption-unknown")
        if req.nonconsumption_sha256 is not None:
            if previous is None:
                raise Invalid("unexpected-certificate")
            if previous.permit_generation >= 3:
                raise Invalid("generation-cap")
            proof = recovery.private_read(Path(f"{prefix}.nonconsumption-{previous.permit_generation}.json"))
            superseded = recovery.private_read(Path(f"{prefix}.superseded-{previous.permit_generation}.json"))
            if hashlib.sha256(proof).hexdigest() != req.nonconsumption_sha256:
                raise Invalid("certificate-hash")
            renewal(
                obj, previous, proof, superseded, boot_id=observed.boot_id, boottime_ns=observed.boottime_ns
            )
        return proof

    @staticmethod
    def peer(uid: int | None) -> None:
        if uid != os.getuid():
            raise Invalid("peer")

    def get(self, proposal_sha256: str, *, uid: int | None) -> Status:
        self.peer(uid)
        _hash(proposal_sha256)
        with self.store.transaction(read_only=True):
            exists = self.store.db.execute(
                "SELECT name FROM sqlite_master WHERE name='gateway_custody_proposals'"
            ).fetchone()
            if exists is None:
                raise Invalid("not-registered")
            row = self.store.db.execute(
                "SELECT bundle,notice_key,state FROM gateway_custody_proposals WHERE hash=?",
                (proposal_sha256,),
            ).fetchone()
            if row is None:
                raise Invalid("not-registered")
            return {
                "proposal_sha256": proposal_sha256,
                "bundle": json.loads(bytes(row[0])),
                "notice_key": str(row[1]),
                "state": str(row[2]),
                "notice_status": self.notice_status(str(row[1])),
            }

    def register(self, obj: Descriptor, *, uid: int | None, max_chars: int) -> str:
        self.peer(uid)
        obj = descriptor(canonical(obj))
        if self.source is None:
            raise Invalid("source-unavailable")
        observed = self.source(digest(obj))
        self.validate_observation(obj, observed)
        text = notice(
            obj,
            original_actor=observed.original_actor,
            evidence_url=observed.evidence_url,
            max_chars=max_chars,
        )
        at = datetime.now(UTC)
        bundle = canonical(
            {
                "notice_created_at": at.isoformat(),
                "notice_boot_id": observed.boot_id,
                "notice_boottime_ns": str(observed.boottime_ns),
                "descriptor": obj,
                "original_actor": observed.original_actor,
                "evidence_url": observed.evidence_url,
                "notice": text,
            }
        )
        if len(bundle) > MAX_BUNDLE:
            raise Invalid("bundle-capacity")
        key = digest(obj)
        # Schema creation follows source validation. No arbitrary producer callback
        # runs inside the Store transaction; enqueue uses the same SQLite connection.
        with self.store.transaction():
            self.store.db.execute(REGISTRATION_SCHEMA)
        with self.store.transaction():
            old = self.store.db.execute(
                "SELECT bundle,notice_key FROM gateway_custody_proposals WHERE hash=?", (key,)
            ).fetchone()
            if old is not None:
                prior = json.loads(bytes(old[0]))
                if any(
                    prior.get(field) != value
                    for field, value in json.loads(bundle).items()
                    if field not in ("notice_created_at", "notice_boot_id", "notice_boottime_ns")
                ):
                    raise Invalid("immutable-proposal")
                delivery = self.notice_status(str(old[1]))
                mapped = self.store.db.execute(
                    "SELECT message_id FROM outbox WHERE key=?", (str(old[1]) + ":0",)
                ).fetchone()
                if delivery not in ("pending", "sent") or (delivery == "sent" and mapped[0] is None):
                    # No safe non-delivery evidence exists: retain exact row and HOLD,
                    # never auto-resend an ambiguous native notice.
                    raise Invalid("notice-" + delivery)
                return key
            if self.store.db.execute(
                "SELECT 1 FROM gateway_custody_proposals WHERE token=? OR proposal=?",
                (key[:24], obj.proposal),
            ).fetchone():
                raise Invalid("proposal-collision")
            count = self.store.db.execute("SELECT count(*) FROM gateway_custody_proposals").fetchone()[0]
            per_event = self.store.db.execute(
                "SELECT count(*) FROM gateway_custody_proposals WHERE event=?", (obj.event,)
            ).fetchone()[0]
            if count >= 4096 or per_event >= 16 or not self.capacity(bundle):
                raise Invalid("proposal-capacity")
            # Custody owns its rate history and fixed outbox namespace. Generic
            # notice pruning/capacity cannot hide or consume custody obligations.
            # Wall time is presentation only. Unknown/legacy/rebooted provenance
            # conservatively occupies a rate slot; no inferred hour rollover.
            stamps = self.store.db.execute(
                "SELECT json_extract(bundle,'$.notice_boot_id'),"
                "json_extract(bundle,'$.notice_boottime_ns') FROM gateway_custody_proposals"
            ).fetchall()
            recent = sum(
                boot != observed.boot_id
                or not isinstance(stamp, str)
                or CLOCK.fullmatch(stamp) is None
                or not 3_600_000_000_000 <= observed.boottime_ns - int(stamp)
                for boot, stamp in stamps
            )
            pending = self.store.db.execute(
                "SELECT count(*) FROM gateway_custody_proposals AS p WHERE NOT EXISTS "
                "(SELECT 1 FROM outbox AS o WHERE o.key=p.notice_key||':0' AND o.status='sent' "
                "AND o.message_id IS NOT NULL)"
            ).fetchone()[0]
            if recent >= 32 or pending >= 256:
                raise Invalid("notice-capacity")
            notice_key = "gateway-custody:" + key
            self.store.db.execute(
                "INSERT INTO gateway_custody_proposals VALUES(?,?,?,?,?,?,?)",
                (key, key[:24], obj.proposal, obj.event, bundle, notice_key, "awaiting-consent"),
            )
            if not self.store.enqueue(notice_key + ":0", text, None, ownership_kind="system"):
                raise Invalid("notice-collision")
        return key

    def message(
        self,
        *,
        account: str,
        group: str,
        mid: str,
        sender: str,
        target: str | None,
        text: str,
    ) -> str:
        """Native daemon entrypoint only; not an RPC and never permit authority.

        The independently enrolled authority port authenticates account, group and
        sender against current policy. Absent ports hold. An exact duplicate may
        read its immutable earlier receipt without creating a new decision.
        """
        parsed = command(text)
        if parsed is None or not all(recovery.HEX.fullmatch(v) for v in (account, mid, sender)):
            raise Invalid("command-auth")
        if not re.fullmatch(r"(?:[0-9a-f]{2}){1,128}", group):
            raise Invalid("group")
        if self.authority is None:
            raise Invalid("authority-unavailable")
        action, token = parsed
        event = dict(account=account, group=group, message_id=mid, sender=sender, reply_to=target, text=text)
        event_id = account + ":" + group + ":" + mid
        with self.store.transaction(read_only=True):
            row = self.store.db.execute(
                "SELECT hash,bundle,notice_key,state FROM gateway_custody_proposals WHERE token=?",
                (token,),
            ).fetchone()
            if row is None:
                raise Invalid("token")
        key, raw = row[:2]
        bundle = json.loads(bytes(raw))
        commands = bundle.get("commands", {})
        previous = commands.get(event_id)
        if previous is not None:
            if previous["event"] == event:
                return str(previous["outcome"])

        policy = self.authority(account, group, sender)
        if policy is None:
            raise Invalid("authority")
        _hash(policy)
        if action == "settle":
            self.observation(key, bundle)
        if self.authority(account, group, sender) != policy:
            raise Invalid("authority-drift")
        outcome, capacity_hold, conflict = self._retain_command(row, event, event_id, policy, action)
        if conflict:
            raise Invalid("replay-mismatch")
        if capacity_hold:
            raise Invalid("bundle-capacity")
        return outcome

    def _retain_command(
        self,
        row: tuple[Any, ...],
        event: dict[str, str | None],
        event_id: str,
        policy: str,
        action: str,
    ) -> tuple[str, bool, bool]:
        """Serialize native command custody against publication; no external callbacks."""
        key, raw, notice_key, state = row
        bundle = json.loads(bytes(raw))
        target = event["reply_to"]
        capacity_hold = False
        with self.publication_lock, self.store.transaction():
            current = self.store.db.execute(
                "SELECT hash,bundle,notice_key,state FROM gateway_custody_proposals WHERE hash=?", (key,)
            ).fetchone()
            if current != row:
                if action == "settle" or current is None or current[2] != notice_key:
                    raise Invalid("proposal-changed")
                # Rebase only an authenticated contrary event on the exact same
                # immutable proposal/thread. It must not disappear in a settle race.
                latest = json.loads(bytes(current[1]))
                if latest["descriptor"] != bundle["descriptor"] or latest["notice"] != bundle["notice"]:
                    raise Invalid("proposal-changed")
                bundle, raw, state = latest, current[1], current[3]

            deliveries = self.store.db.execute(
                "SELECT message_id,text FROM outbox WHERE key=? AND status='sent'",
                (notice_key + ":0",),
            ).fetchall()
            if (
                len(deliveries) != 1
                or target is None
                or deliveries[0][0] != target
                or deliveries[0][1] != bundle["notice"]
            ):
                raise Invalid("thread-unmapped")
            if state == "terminal":
                previous = cast(dict[str, Any], bundle.get("commands", {})).get(event_id)
                if previous is not None and previous["event"] != event:
                    raise Invalid("replay-mismatch")
                return "held", False, False
            outcome, after, conflict = self._record_command(bundle, state, event, event_id, policy, action)
            updated = canonical(bundle)
            diagnostics = canonical({name: bundle.get(name, {}) for name in ("commands", "conflicts")})
            without_permits = {k: v for k, v in bundle.items() if k not in ("permits", "certificates")}
            if (
                len(diagnostics) > MAX_COMMAND_BYTES
                or len(canonical(without_permits)) > MAX_BUNDLE - PERMIT_RESERVE
                or not self.capacity(updated, len(raw))
            ):
                # Capacity may refuse a new receipt, never preserve usable consent
                # after an authenticated contrary command. Retain the old bytes.
                if action in ("decline", "revoke") or conflict:
                    self.store.db.execute(
                        "UPDATE gateway_custody_proposals SET state='revoked' "
                        "WHERE hash=? AND state IN ('awaiting-consent','consented')",
                        (key,),
                    )
                capacity_hold = True
            else:
                self.store.db.execute(
                    "UPDATE gateway_custody_proposals SET bundle=?,state=? WHERE hash=?",
                    (updated, after, key),
                )
        return outcome, capacity_hold, conflict

    @staticmethod
    def _record_command(
        bundle: dict[str, Any],
        state: str,
        event: dict[str, str | None],
        event_id: str,
        policy: str,
        action: str,
    ) -> tuple[str, str, bool]:
        """Derive a closed state transition; caller proved current native context."""
        commands = bundle.get("commands", {})
        previous = commands.get(event_id)
        conflict = previous is not None and previous["event"] != event
        if previous is not None and not conflict:
            return str(previous["outcome"]), state, False
        if conflict:
            outcome, after = "revoked", "revoked"
            conflicts = bundle.get("conflicts", [])
            if event not in conflicts:
                bundle["conflicts"] = [*conflicts, event]
        elif state == "awaiting-consent" and action == "settle":
            outcome, after = "consented", "consented"
            bundle["decision"] = event | {"policy_sha256": policy}
        elif state == "consented" and action == "settle":
            outcome, after = "consented", "consented"
        elif state in ("awaiting-consent", "consented"):
            outcome, after = "revoked", "revoked"
        else:
            outcome, after = "held", state
        if not conflict:
            commands[event_id] = {"event": event, "outcome": outcome, "policy_sha256": policy}
        bundle["commands"] = commands
        return outcome, after, conflict
