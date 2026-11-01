"""Private pure profile corroboration; output is never admission or consent.

The owning launcher must supply its exact retained FD3 and original bytes under
its qualified interpreter, isolation and resource policy. This module installs
no public command, owner constructor or runtime isolation mechanism.
"""

# pyright: reportPrivateUsage=false

import os
import sys
from typing import Literal

import msgspec

from heterodyne.actions.codec import MAX_BYTES, InvalidAction
from heterodyne.actions.deployment_producer_process import _bound_operation, _identity, _profile


class _Result(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["heterodyne-owned-profile-check.v1"]
    operation_original_sha256: str
    packet_sha256: str


def _profile_check(original: bytes, profile_fd: int) -> bytes:
    """Reuse exact production profile/packet codecs without preparing a card.

    No archive/commit descriptor, URL fetch, rendering policy, publication or
    native state is opened or consulted. Typed target checks are the existing
    decode_packet checks reached through _bound_operation, not a second codec.
    """
    before = _identity(profile_fd)
    profile = _profile(profile_fd)
    _bound_operation(original, profile)
    if _identity(profile_fd) != before:
        raise InvalidAction("profile-check-descriptor-changed")
    return msgspec.json.encode(_Result(
        "heterodyne-owned-profile-check.v1", profile.operation_original_sha256, profile.packet_sha256,
    ))


def _entry() -> int:  # pyright: ignore[reportUnusedFunction] - private fixed owner subprocess entry
    """Exact original on stdin and profile FD3; no arguments or path selectors."""
    try:
        if len(sys.argv) != 1:
            raise InvalidAction("profile-check-argv")
        raw = _profile_check(sys.stdin.buffer.read(MAX_BYTES + 1), 3)
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        try:
            os.write(2, b"owned-profile-check-refused\n")
        except OSError:
            pass
        # Prevent shutdown retry of an uncertain buffered stdout write.
        os._exit(2)
