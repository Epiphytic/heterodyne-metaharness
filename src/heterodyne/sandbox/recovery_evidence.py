"""Bounded, retained proof for an exact deadline reconciliation; no lifecycle effects."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from heterodyne.tmux import DEAD_PANE
from heterodyne.wsd.runtime import RuntimeUnavailable

if TYPE_CHECKING:
    from heterodyne.sandbox.runtime import SessionRecord

MARKER_LIMIT = 4096
RECEIPT_LIMIT = 65536
TAIL_LIMIT = 8192


class Evidence(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    original: str
    marker: str
    marker_sha256: str
    pane: str
    tail: str


def read_bounded(path: Path, limit: int) -> bytes | None:
    """Never follow a symlink, block on a FIFO, or accept truncated evidence."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeUnavailable("recovery evidence can't be opened") from None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeUnavailable("recovery evidence is not a regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RuntimeUnavailable("recovery evidence exceeds its bound")
    return data


def validate_marker(data: bytes, rec: SessionRecord, now: int) -> None:
    try:
        fields: object = json.loads(data)
    except ValueError:
        raise RuntimeUnavailable("the deadline marker is malformed") from None
    if not isinstance(fields, dict):
        raise RuntimeUnavailable("the deadline marker is not an object")
    marker: dict[str, object] = fields  # pyright: ignore[reportUnknownVariableType]
    generation, at = marker.get("generation"), marker.get("at")
    if (set(marker) != {"generation", "sandbox", "at"} or type(generation) is not int
            or generation != rec.generation
            or marker.get("sandbox") != rec.sandbox or type(at) is not int
            or rec.started_at <= 0 or rec.deadline <= rec.started_at
            or now < rec.deadline or not rec.deadline <= at <= now):
        raise RuntimeUnavailable("the deadline marker does not prove this generation expired")


def receipt(data: bytes, expected: SessionRecord, now: int) -> Evidence:
    try:
        evidence = msgspec.json.decode(data, type=Evidence)
        marker = bytes.fromhex(evidence.marker)
    except (ValueError, msgspec.DecodeError):
        raise RuntimeUnavailable("the retained recovery receipt is malformed") from None
    if (evidence.original != msgspec.json.encode(expected).decode()
            or len(marker) > MARKER_LIMIT or hashlib.sha256(marker).hexdigest() != evidence.marker_sha256
            or len(evidence.tail.encode()) > TAIL_LIMIT
            or not DEAD_PANE.fullmatch(evidence.pane)):
        raise RuntimeUnavailable("the retained recovery receipt differs from this operation")
    validate_marker(marker, expected, now)
    return evidence


def same_generation(current: SessionRecord, original: SessionRecord) -> bool:
    """Only normal ending's journal fields may advance under a retained receipt."""
    return msgspec.structs.replace(current, phase=original.phase, error=original.error,
                                  stop_reason=original.stop_reason, wip_mark=original.wip_mark,
                                  unlanded=original.unlanded, end_cause=original.end_cause,
                                  ended_at=original.ended_at) == original


def build(expected: SessionRecord, marker: bytes, pane: str, tail: str) -> bytes:
    bounded_tail = tail.encode()[-TAIL_LIMIT:].decode("utf-8", "ignore")
    evidence = Evidence(msgspec.json.encode(expected).decode(), marker.hex(),
                        hashlib.sha256(marker).hexdigest(), pane, bounded_tail)
    raw = msgspec.json.encode(evidence)
    if len(raw) > RECEIPT_LIMIT:
        raise RuntimeUnavailable("recovery receipt exceeds its bound")
    return raw
