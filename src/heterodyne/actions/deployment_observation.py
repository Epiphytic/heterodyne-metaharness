"""Finite deployment predicates; records and hashes convey no effect authority.

Volatile process/time/command evidence must be retained separately. A protected
observer, not a packet or caller, supplies these values to the owning journal.
"""

# pyright: reportPrivateUsage=false

import hashlib
import re
from typing import Literal

from heterodyne.actions.codec import InvalidAction, _decode, encode_record
from heterodyne.actions.contracts import Record
from heterodyne.admind.binding import canonical

_DOMAIN = b'heterodyne-deployment-observation.v1\x00'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
_RELEASE = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
_SHA256 = re.compile(r'[0-9a-f]{64}')


class HostObservation(Record, frozen=True):
    """Only predictable predicates participate in the transition digest.

    unchecked is a measured unit/tree state with no guard verdict yet. A guard
    pass requires the corresponding actual source-owned guard evaluation; this
    codec never upgrades a record into proof that such an evaluation happened.
    """

    schema: Literal['heterodyne-deployment-observation.v1']
    target_id: str
    enabled: Literal['enabled', 'disabled']
    active: Literal['active', 'inactive']
    selected_release: str
    guard_verdict: Literal['unchecked', 'online-pass', 'stopped-pass', 'held-pass', 'hold']
    quarantine_count: int
    installed_tree_sha256: str


def decode_observation(raw: bytes) -> HostObservation:
    value = _decode(raw, HostObservation, max_bytes=4096)
    if (
        not _ID.fullmatch(value.target_id)
        or not _RELEASE.fullmatch(value.selected_release)
        or not _SHA256.fullmatch(value.installed_tree_sha256)
        or not 0 <= value.quarantine_count <= 2**63 - 1
    ):
        raise InvalidAction('deployment-observation-predicate')
    return value


def observation_bytes(value: HostObservation) -> bytes:
    """Sorted compact UTF-8 JSON, no newline; finite fields are ASCII-only."""
    checked = decode_observation(encode_record(value))
    return canonical(checked)


def observation_digest(value: HostObservation) -> str:
    return hashlib.sha256(_DOMAIN + observation_bytes(value)).hexdigest()
