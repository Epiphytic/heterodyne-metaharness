"""Plan8's strict scheduled wake wire contract, separate from legacy control requests.

These are consistency identities under the existing same-service-UID socket trust
boundary. A decoded request proves no authority, admission or completed effect.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import msgspec

MAX_WAKE_PAYLOAD = 1024
MAX_WAKE_REPLY = 4096
WAKE_SECONDS = 5.0
_COUNT = re.compile(r"0|[1-9][0-9]{0,2}")
_REASONS = frozenset({"none", "owner_mismatch", "generation_mismatch", "manifest_mismatch",
                      "maintenance", "stopping", "unsupported_job"})


class InvalidWake(ValueError):
    """A scheduled request is outside the finite, bounded wire contract."""


class ScheduledWakeRequest(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    op: Literal["scheduled-wake"]
    version: Literal[1]
    installation_id: str
    generation: str
    request_id: str
    job: Literal["pickup", "reconcile"]


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidWake("duplicate request field")
        result[key] = value
    return result


def decode_wake(payload: bytes) -> ScheduledWakeRequest:
    """Decode at most1024 payload bytes; framing strips its single newline first."""
    if type(payload) is not bytes or len(payload) > MAX_WAKE_PAYLOAD:
        raise InvalidWake("request payload limit")
    try:
        raw: object = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_pairs)
        # JSON's bool and float values must not alias the integer protocol version.
        if not isinstance(raw, dict) or type(cast(dict[str, object], raw).get("version")) is not int:
            raise InvalidWake("invalid request version")
        request = msgspec.convert(raw, type=ScheduledWakeRequest, strict=True)
        for identity in (request.installation_id, request.generation, request.request_id):
            if str(UUID(identity)) != identity:
                raise InvalidWake("noncanonical request identity")
    except (ValueError, UnicodeError, RecursionError, msgspec.ValidationError) as exc:
        raise InvalidWake("invalid scheduled request") from exc
    return request


def encode_wake(request: ScheduledWakeRequest) -> bytes:
    """Validate even directly constructed structs; return payload without framing."""
    payload = msgspec.json.encode(request)
    decode_wake(payload)
    return payload


@dataclass(frozen=True)
class WakeReceipt:
    """Admission only: never evidence that the queued operation completed."""
    status: str
    reason: str
    targets: int
    joined: int


class _ReceiptFields(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: str
    request_id: str
    status: str
    reason: str
    targets: str
    joined: str


class _ReplyFields(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: Literal["ok", "refused", "failed"]
    message: str
    data: dict[str, _ReceiptFields]


def _receipt(data: _ReceiptFields, result: str, request_id: str) -> WakeReceipt:
    if data.version != "1" or data.request_id != request_id:
        raise InvalidWake("scheduled receipt identity mismatch")
    if (data.reason not in _REASONS or not _COUNT.fullmatch(data.targets)
            or not _COUNT.fullmatch(data.joined)):
        raise InvalidWake("invalid scheduled receipt values")
    targets, joined = int(data.targets), int(data.joined)
    if not 0 <= joined <= targets <= 128:
        raise InvalidWake("scheduled receipt target bounds")
    status, reason = data.status, data.reason
    if not ((status == "queued" and result == "ok" and reason == "none")
            or (status == "refused" and result == "refused" and reason != "none"
                and targets == joined == 0)):
        raise InvalidWake("inconsistent scheduled receipt")
    return WakeReceipt(status, reason, targets, joined)


def decode_wake_reply(frame: bytes, request_id: str) -> WakeReceipt | None:
    """Validate a complete legacy envelope; generic refusals/failures are unconfirmed.

    No prose is interpreted as a definite-unsent proof or a stable refusal code.
    """
    if (type(frame) is not bytes or len(frame) > MAX_WAKE_REPLY
            or not frame.endswith(b"\n") or b"\n" in frame[:-1]):
        raise InvalidWake("invalid scheduled reply frame")
    try:
        raw: object = json.loads(frame.decode("utf-8"), object_pairs_hook=unique_pairs)
        reply = msgspec.convert(raw, type=_ReplyFields, strict=True)
        if not reply.data and reply.result in ("refused", "failed"):
            return None
        if set(reply.data) != {"scheduled"}:
            raise InvalidWake("invalid scheduled reply namespace")
        return _receipt(reply.data["scheduled"], reply.result, request_id)
    except (ValueError, UnicodeError, RecursionError, TypeError, msgspec.ValidationError) as exc:
        raise InvalidWake("invalid scheduled reply") from exc


async def scheduled_request(path: Path, request: ScheduledWakeRequest) -> WakeReceipt | None:
    """Send once within a total five-second deadline; None means wake_unconfirmed.

    The next timer may try again. This client never retries or opens effect stores.
    """
    payload = encode_wake(request)
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(WAKE_SECONDS):
            reader, writer = await asyncio.open_unix_connection(str(path), limit=MAX_WAKE_REPLY + 1)
            writer.write(payload + b"\n")
            await writer.drain()
            line = await reader.readline()
            return decode_wake_reply(line, request.request_id)
    except (OSError, TimeoutError, ValueError):
        return None
    finally:
        if writer is not None:
            # No unbounded close handshake may extend the activation deadline.
            writer.close()
            writer.transport.abort()
