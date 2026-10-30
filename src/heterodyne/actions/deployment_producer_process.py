"""Private fixed producer process contract; its output is never consent.

The native owning factory must enroll the interpreter/import closure and open
all three descriptors itself. This module installs no executable or public
opener. Descriptor custody checks do not prove that the caller is that factory.
"""

# pyright: reportPrivateUsage=false

import fcntl
import hashlib
import json
import os
import stat
import sys
from typing import Literal

import msgspec

from heterodyne.actions.codec import (
    MAX_BYTES,
    InvalidAction,
    _decode,
    _pairs,
    _reject_constant,
    encode_record,
)
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_authority import _PACKET
from heterodyne.actions.deployment_card import _derive, _prepare, _PreparedCard
from heterodyne.actions.deployment_outage import decode_packet, packet_digest

_PROFILE_FD, _ARCHIVE_FD, _COMMIT_FD = 3, 4, 5
_OUTPUT_LIMIT = 256 * 1024


class _Profile(Record, frozen=True):
    schema: Literal["heterodyne-owned-card-profile.v1"]
    operation_original_sha256: str
    packet_sha256: str
    group_hex: str
    publication_url: str
    targets: tuple[Target, ...]


class _Output(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["heterodyne-owned-card.v1"]
    operation_original: bytes
    operation_original_sha256: str
    operation_canonical: bytes
    packet_sha256: str
    ask_id: str
    group_hex: str
    request_original: bytes
    request_sha256: str
    publication_url: str
    publication_commit: str
    publication_path: str
    published_original_sha256: str
    card: str
    cards_sha256: str


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(fd: int) -> tuple[int, ...]:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o400
        or info.st_uid not in {0, os.geteuid()} or info.st_nlink != 1
        or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
    ):
        raise InvalidAction("producer-descriptor-custody")
    return tuple(getattr(info, key) for key in (
        "st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink",
        "st_size", "st_mtime_ns", "st_ctime_ns",
    ))


def _profile(fd: int) -> _Profile:
    before = _identity(fd)
    raw = os.pread(fd, MAX_BYTES + 1, 0)
    if len(raw) > MAX_BYTES or len(raw) != before[6] or _identity(fd) != before:
        raise InvalidAction("producer-profile-changed-or-bound")
    value = _decode(raw, _Profile)
    for digest in (value.operation_original_sha256, value.packet_sha256, value.group_hex):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise InvalidAction("producer-profile-digest")
    if not _PACKET.fullmatch(value.publication_url):
        raise InvalidAction("producer-profile-url")
    return value


def _bound_operation(original: bytes, profile: _Profile) -> bytes:
    if not original or len(original) > MAX_BYTES or _sha(original) != profile.operation_original_sha256:
        raise InvalidAction("producer-original-binding")
    packet = decode_packet(original, profile.targets)
    if packet_digest(packet) != profile.packet_sha256:
        raise InvalidAction("producer-canonical-binding")
    return encode_record(packet)


def _output(prepared: _PreparedCard) -> _Output:
    return _Output(
        "heterodyne-owned-card.v1", prepared.operation_original,
        prepared.operation_original_sha256, prepared.operation_canonical,
        prepared.packet_sha256, prepared.ask_id, prepared.group_hex,
        prepared.request_original, _sha(prepared.request_original),
        prepared.publication_url, prepared.publication_commit, prepared.publication_path,
        prepared.published_original_sha256, prepared.card,
        _sha(msgspec.json.encode([prepared.card])),
    )


def _produce(original: bytes, profile_fd: int, archive_fd: int, commit_fd: int) -> bytes:
    """Owned-descriptor mechanics only; no capability to publish or approve."""
    if len({profile_fd, archive_fd, commit_fd}) != 3:
        raise InvalidAction("producer-descriptor-alias")
    identities = tuple(_identity(fd) for fd in (profile_fd, archive_fd, commit_fd))
    if len({identity[:2] for identity in identities}) != 3:
        raise InvalidAction("producer-object-alias")
    profile = _profile(profile_fd)
    _bound_operation(original, profile)
    prepared = _prepare(
        original, profile.targets, profile.group_hex, profile.publication_url, archive_fd, commit_fd,
    )
    if identities != tuple(_identity(fd) for fd in (profile_fd, archive_fd, commit_fd)):
        raise InvalidAction("producer-descriptor-changed")
    raw = msgspec.json.encode(_output(prepared))
    if len(raw) > _OUTPUT_LIMIT:
        raise InvalidAction("producer-output-bound")
    return raw


def _check_output(  # pyright: ignore[reportUnusedFunction] - private native owning launcher only
    raw: bytes, original: bytes, profile: _Profile,
) -> _Output:
    """Correlation check for the owning process, never an authority importer.

    This must only inspect stdout of the factory's own pinned invocation. Even a
    correctly correlated arbitrary blob does not establish invocation provenance.
    """
    if not raw or len(raw) > _OUTPUT_LIMIT:
        raise InvalidAction("producer-output-bound")
    try:
        # This output includes the rendered multiline card; the action codec's
        # safe-text walk deliberately forbids those strings. Reuse its strict
        # JSON hooks, then enforce the flat typed output and canonical bytes.
        value = msgspec.convert(json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant,
        ), type=_Output, strict=True)
    except (ValueError, TypeError, RecursionError, MemoryError, msgspec.ValidationError) as exc:
        raise InvalidAction("producer-output-shape") from exc
    if raw != msgspec.json.encode(value):
        raise InvalidAction("producer-output-encoding")
    _bound_operation(original, profile)
    expected = _output(_derive(original, profile.targets, profile.group_hex, profile.publication_url))
    if value != expected:
        raise InvalidAction("producer-output-correlation")
    return value


def _entry() -> int:  # pyright: ignore[reportUnusedFunction] - private fixed subprocess entry only
    """Fixed fd3 profile/fd4 archive/fd5 commit; exact original packet on stdin.

    Native owns a total child deadline and bounded stdout/stderr collection. No
    argv, environment, path, transport callback or card selector is accepted.
    """
    try:
        if len(sys.argv) != 1:
            raise InvalidAction("producer-argv")
        original = sys.stdin.buffer.read(MAX_BYTES + 1)
        raw = _produce(original, _PROFILE_FD, _ARCHIVE_FD, _COMMIT_FD)
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # A closed or malformed stdio stream, recursion or allocation failure
        # must not turn this private child boundary into a source/path traceback.
        # The owning launcher treats every nonzero exit as refusal regardless.
        try:
            os.write(2, b"owned-card-producer-refused\n")
        except OSError:
            pass
        # Do not let interpreter shutdown retry a failed buffered stdout flush.
        # This entry is only executed in the factory-owned subprocess.
        os._exit(2)
