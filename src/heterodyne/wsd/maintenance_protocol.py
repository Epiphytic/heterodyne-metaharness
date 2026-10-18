"""Closed effect and paired-backup request variants. No caller chooses paths, SQL or effect slots."""

import asyncio
import json
import re
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

import msgspec

from heterodyne.wsd.scheduled import InvalidWake, unique_pairs


class EffectWake(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    op: Literal["effect-wake"]
    version: Literal[1]
    installation_id: str
    generation: str
    request_id: str
    job: str


class PairedBackup(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    op: Literal["paired-backup"]
    version: Literal[1]
    installation_id: str
    generation: str
    request_id: str
    owner_hash: str
    cycle_sequence: int
    native_status: Literal["succeeded", "failed", "unknown"]


Request = EffectWake | PairedBackup


def decode(payload: bytes) -> Request:
    if len(payload) > 1024:
        raise InvalidWake("maintenance payload limit")
    try:
        raw = json.loads(payload, object_pairs_hook=unique_pairs)
        if not isinstance(raw, dict) or type(cast(dict[str, object], raw).get("version")) is not int:
            raise InvalidWake("maintenance version")
        result = (
            msgspec.json.decode(payload, type=EffectWake)
            if cast(dict[str, object], raw).get("op") == "effect-wake"
            else msgspec.json.decode(payload, type=PairedBackup)
        )
        for identity in (result.installation_id, result.generation, result.request_id):
            if str(UUID(identity)) != identity:
                raise InvalidWake("maintenance identity")
        if isinstance(result, EffectWake):
            if not re.fullmatch("[a-z][a-z0-9_]{0,63}", result.job):
                raise InvalidWake("maintenance job")
        elif not re.fullmatch("[0-9a-f]{64}", result.owner_hash) or not 1 <= result.cycle_sequence < 1 << 64:
            raise InvalidWake("maintenance cycle")
        return result
    except (ValueError, msgspec.DecodeError, UnicodeError) as exc:
        raise InvalidWake("invalid maintenance request") from exc


class Receipt(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    request_id: str
    status: Literal["queued", "joined", "completed", "refused"]
    reason: Literal["none", "not_ready", "producer_disabled", "owner_unavailable", "busy", "unresolved",
                    "local_failed", "local_partial", "result_unavailable"]


class Envelope(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: Literal["ok", "refused", "failed"]
    message: str
    data: dict[str, Receipt]


async def request(path: Path, value: Request) -> Receipt | None:
    """One five-second enqueue attempt; no automatic retry, no native backup invocation."""
    payload = msgspec.json.encode(value)
    decode(payload)
    writer = None
    try:
        async with asyncio.timeout(5):
            reader, writer = await asyncio.open_unix_connection(str(path), limit=4097)
            writer.write(payload + b"\n")
            await writer.drain()
            frame = await reader.readline()
            if len(frame) > 4096 or not frame.endswith(b"\n") or b"\n" in frame[:-1]:
                return None
            raw = json.loads(frame, object_pairs_hook=unique_pairs)
            envelope = msgspec.convert(raw, type=Envelope, strict=True)
            if set(envelope.data) != {"maintenance"}:
                return None
            receipt = envelope.data["maintenance"]
            if (
                receipt.request_id != value.request_id
                or (receipt.status == "refused") != (envelope.result == "refused")
                or envelope.result == "failed"
                or (receipt.reason == "none") != (receipt.status != "refused")
            ):
                return None
            return receipt
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError):
        return None
    finally:
        if writer is not None:
            writer.close()
            writer.transport.abort()
