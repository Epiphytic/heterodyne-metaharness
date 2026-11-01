"""Pure correlation of the private native handoff; this never grants authority.

The live source-qualified root parent attests native SQL/header/card facts.
Only the independently retained descriptors establish expected operation and
source bindings here. No arbitrary blob or successful call establishes consent.
"""

# pyright: reportPrivateUsage=false

import hashlib
import json
from typing import Literal, cast

import msgspec

from heterodyne.actions.codec import InvalidAction, _pairs, _reject_constant
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment_admission_peer import _ParentChannel
from heterodyne.actions.deployment_card import _derive
from heterodyne.actions.deployment_outage import decode_packet
from heterodyne.actions.deployment_producer_process import _bound_operation, _profile_bytes
from heterodyne.actions.deployment_root_helper import _HASH, _read


class _UseKey(Record, frozen=True):
    header_sha256: str
    card_complete_sequence: int
    ask_id: str
    original_operation_sha256: str


class _Original(Record, frozen=True):
    ask_id: str
    expected_group: tuple[int, ...]
    operation: str
    operation_canonical: str
    original_request: str
    card_chunks: tuple[str, ...]


class _Receipt(Record, frozen=True):
    sequence: int
    ask_id: str
    operation_sha256: str
    packet_sha256: str
    request_sha256: str
    cards_sha256: str
    policy_sha256: str
    operator: tuple[int, ...]
    decision: Literal["Approve"]
    authenticated_candidate: object


class _Consumed(Record, frozen=True):
    schema: Literal["heterodyne-owned-consumed.v3"]
    nonce_hex: str
    startup_profile_sha256: str
    enrollment_sha256: str
    journal_profile_sha256: str
    installation_index_sha256: str
    installation_source_sha256: str
    capability_template: str
    capability_policy_sha256: str
    original: _Original
    consumption_key: _UseKey
    receipt: _Receipt


# Exact native Enrollment struct declaration order, not installation canonical
# sorted-key order. The native ABI consists exclusively of ASCII values here.
_ENROLLMENT_FIELDS = (
    "schema", "account", "group", "operators", "expected_welcomer", "source_profile_sha256",
    "storage_generation", "initial_native_state_sha256", "welcome_provenance_sha256",
    "expiry_profile_sha256", "installation_index_sha256", "installation_source_sha256",
    "capability_template", "capability_policy_sha256",
)


def _json(raw: bytes) -> object:
    if not raw or len(raw) > 262144:
        raise InvalidAction("admission-native-bound")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=_reject_constant, parse_float=_reject_constant)
    except (ValueError, TypeError, RecursionError, MemoryError) as exc:
        raise InvalidAction("admission-native-json") from exc


def _correlate(  # pyright: ignore[reportUnusedFunction] - private composition, no public authority opener
    channel: _ParentChannel,
) -> _Consumed:
    """Require a live owned channel; returned data remains correlation only.

    Original fixed descriptors FD7/13/18 were retained by the native parent.
    This function is private composition for the admission entry, not an import
    API for receipt files. Callers must retain/check the parent lifetime before
    each journal issue/effect, including after this function returns.
    """
    channel.check()
    process_raw = _read(7, 65536)
    producer_raw = _read(13, 32768)
    original = _read(18, 32768)
    if (
        hashlib.sha256(process_raw).hexdigest() != channel.handoff.process_profile_sha256
        or hashlib.sha256(producer_raw).hexdigest() != channel.profile.producer_profile_sha256
    ):
        raise InvalidAction("admission-retained-profile-binding")
    producer = _profile_bytes(producer_raw)
    canonical = _bound_operation(original, producer)
    if (
        producer.packet_sha256 != channel.profile.packet_sha256
        or decode_packet(original, producer.targets).semantic_factory_source_sha256
        != channel.profile.semantic_factory_source_sha256
    ):
        raise InvalidAction("admission-retained-packet-binding")
    parsed = _json(process_raw)
    if not isinstance(parsed, dict):
        raise InvalidAction("admission-process-profile")
    process = cast(dict[str, object], parsed)
    if process.get("schema") != "heterodyne-protected-receiver-process.v3":
        raise InvalidAction("admission-process-profile")
    enrolled = process.get("enrollment")
    if not isinstance(enrolled, dict):
        raise InvalidAction("admission-enrollment-fields")
    enrollment = cast(dict[str, object], enrolled)
    if set(enrollment) != set(_ENROLLMENT_FIELDS):
        raise InvalidAction("admission-enrollment-fields")
    if (
        enrollment.get("schema") != "heterodyne-receiver-enrollment.v3"
        or enrollment.get("group") != producer.group_hex
        or any(type(enrollment[key]) is not str or not str(enrollment[key]).isascii()
               for key in _ENROLLMENT_FIELDS if key != "operators")
        or enrollment.get("installation_index_sha256") != channel.profile.installation_index_sha256
        or enrollment.get("installation_source_sha256") != channel.profile.installation_source_sha256
    ):
        raise InvalidAction("admission-enrollment-installation")
    try:
        consumed = msgspec.convert(_json(channel.consumed_raw), type=_Consumed, strict=True)
    except (TypeError, msgspec.ValidationError) as exc:
        raise InvalidAction("admission-consumed-shape") from exc
    expected = _derive(original, producer.targets, producer.group_hex, producer.publication_url)
    expected_original = _Original(
        expected.ask_id, tuple(bytes.fromhex(expected.group_hex)), original.hex(), canonical.hex(),
        expected.request_original.hex(), (expected.card,),
    )
    enrollment_bytes = json.dumps(
        {key: enrollment[key] for key in _ENROLLMENT_FIELDS}, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    if (
        not _HASH.fullmatch(consumed.nonce_hex)
        or consumed.nonce_hex != channel.handoff.receiver_nonce_hex
        or consumed.nonce_hex == channel.nonce.hex()
        or consumed.startup_profile_sha256 != channel.handoff.process_profile_sha256
        or consumed.enrollment_sha256 != hashlib.sha256(enrollment_bytes).hexdigest()
        or consumed.journal_profile_sha256 != enrollment.get("source_profile_sha256")
        or consumed.installation_index_sha256 != channel.profile.installation_index_sha256
        or consumed.installation_source_sha256 != channel.profile.installation_source_sha256
        or consumed.capability_template != enrollment.get("capability_template")
        or consumed.capability_policy_sha256 != enrollment.get("capability_policy_sha256")
        or consumed.original != expected_original
    ):
        raise InvalidAction("admission-consumed-original-or-source")
    # Receipt.sequence is the authenticated candidate sequence; the card key
    # uses the ordered journal sequence. Native consumption verifies ordering;
    # comparing these independent counters would reject valid native consent.
    key, receipt = consumed.consumption_key, consumed.receipt
    operators = enrollment.get("operators")
    if not isinstance(operators, list):
        raise InvalidAction("admission-enrollment-operators")
    allowed = cast(list[object], operators)
    if not 1 <= len(allowed) <= 64 or any(type(op) is not str or not _HASH.fullmatch(op) for op in allowed):
        raise InvalidAction("admission-enrollment-operators")
    # Native SemanticFactory::policy serializes (GroupId, Vec<MemberId>).
    # This is the semantic operator policy, DISTINCT from the MLS capability
    # template digest. Preserve enrolled order exactly, never compare the two.
    semantic_policy = hashlib.sha256(msgspec.json.encode([
        list(bytes.fromhex(producer.group_hex)), [list(bytes.fromhex(str(op))) for op in allowed],
    ])).hexdigest()
    if (
        key.ask_id != expected.ask_id or key.original_operation_sha256 != expected.operation_original_sha256
        or not 0 < key.card_complete_sequence < 2**64
        or len(key.header_sha256) != 64 or any(c not in "0123456789abcdef" for c in key.header_sha256)
        or not 0 < receipt.sequence < 2**64
        or receipt.ask_id != expected.ask_id or receipt.operation_sha256 != expected.operation_original_sha256
        or receipt.packet_sha256 != expected.packet_sha256
        or receipt.request_sha256 != hashlib.sha256(expected.request_original).hexdigest()
        or receipt.cards_sha256 != hashlib.sha256(msgspec.json.encode([expected.card])).hexdigest()
        or any(type(n) is not int or not 0 <= n <= 255 for n in receipt.operator)
        or receipt.policy_sha256 != semantic_policy
        or len(receipt.operator) != 32
        or bytes(receipt.operator).hex() not in allowed
    ):
        raise InvalidAction("admission-consumed-key-or-receipt")
    channel.check()
    return consumed
