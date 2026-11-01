"""Private live root-parent handoff, never a receipt-file authority importer.

Only the fixed source-qualified admission child may use this boundary. Its root
parent retains original descriptor custody and the launch lease until child
cessation. This module alone installs no owner, publishes no permit and performs
no host effect. Root-equivalent administrative principals remain trusted.
"""

# pyright: reportPrivateUsage=false

import array
import contextlib
import fcntl
import os
import select
import socket
import stat
import struct
from pathlib import Path
from typing import Literal

from heterodyne.actions.codec import InvalidAction, _decode
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment_root_helper import _binary, _read, _stat_key

_CREDENTIALS = struct.Struct("iII")
_SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
_MAX_FRAME = 262144


class _ParentProfile(Record, frozen=True):
    schema: Literal["heterodyne-deployment-parent-profile.v1"]
    parent_path: str
    parent_sha256: str
    installation_index_sha256: str
    installation_source_sha256: str
    semantic_factory_source_sha256: str
    packet_sha256: str
    producer_profile_sha256: str


class _Handoff(Record, frozen=True):
    schema: Literal["heterodyne-owned-deployment-admission.v1"]
    nonce_hex: str
    receiver_nonce_hex: str
    process_profile_sha256: str
    packet_sha256: str
    producer_profile_sha256: str


def _nonce(fd: int) -> bytes:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
        or before.st_size != 32 or before.st_nlink != 0
        or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
        or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & _SEALS != _SEALS
    ):
        raise InvalidAction("admission-nonce-custody")
    raw = os.pread(fd, 33, 0)
    if len(raw) != 32 or _stat_key(os.fstat(fd)) != _stat_key(before):
        raise InvalidAction("admission-nonce-changed")
    return raw


def _record(conn: socket.socket, parent: int, maximum: int) -> bytes:
    raw, ancillary, flags, _ = conn.recvmsg(
        maximum, socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(32),
        socket.MSG_CMSG_CLOEXEC,
    )
    # Unexpected rights must not leak installed descriptors on rejection.
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            received = array.array("i")
            received.frombytes(data[:len(data) - len(data) % received.itemsize])
            for fd in received:
                os.close(fd)
    if flags & ~socket.MSG_CMSG_CLOEXEC or not raw or len(ancillary) != 1:
        raise InvalidAction("admission-channel-frame")
    level, kind, credentials = ancillary[0]
    if (
        level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS
        or len(credentials) != _CREDENTIALS.size
        or _CREDENTIALS.unpack(credentials) != (parent, 0, 0)
    ):
        raise InvalidAction("admission-channel-credentials")
    return raw


class _ParentChannel:  # pyright: ignore[reportUnusedClass] - private fixed native child composition
    """Owned parent lifetime and original handoff bytes; no effect authority.

    Native consent binding and global journal admission are separate mandatory
    steps. A successful channel read alone must never publish a permit.
    """

    def __init__(self):
        self._stack = contextlib.ExitStack()
        try:
            if os.getuid() != 0 or os.geteuid() != 0:
                raise InvalidAction("admission-root-required")
            self.profile = _decode(_read(3, 16384), _ParentProfile)
            for value in (
                self.profile.parent_sha256, self.profile.installation_index_sha256,
                self.profile.installation_source_sha256, self.profile.semantic_factory_source_sha256,
                self.profile.packet_sha256, self.profile.producer_profile_sha256,
            ):
                if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise InvalidAction("admission-profile-digest")
            self._parent = os.getppid()
            self._pidfd = os.pidfd_open(self._parent)
            self._stack.callback(os.close, self._pidfd)
            self._binary = _binary(Path(self.profile.parent_path), self.profile.parent_sha256)
            self._stack.callback(os.close, self._binary)
            self.nonce = _nonce(6)
            lease = os.fstat(5)
            if (
                not stat.S_ISREG(lease.st_mode) or lease.st_uid != 0 or lease.st_gid != 0
                or stat.S_IMODE(lease.st_mode) != 0o600 or lease.st_nlink != 1
                or fcntl.fcntl(5, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
            ):
                raise InvalidAction("admission-launch-lease-custody")
            # The source-qualified parent retains the SAME OFD lock continuously.
            # Never write, unlock or acquire a replacement lock in this child.
            self._lease = _stat_key(lease)
            duplicate = os.dup(4)
            try:
                self._conn = socket.socket(fileno=duplicate)
            except OSError as exc:
                os.close(duplicate)
                raise InvalidAction("admission-channel-type") from exc
            self._stack.callback(self._conn.close)
            if (
                self._conn.family != socket.AF_UNIX
                or self._conn.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET
                or self._conn.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) != 1
            ):
                raise InvalidAction("admission-channel-type")
            self._conn.settimeout(30)
            self.check()
            self.handoff = _decode(_record(self._conn, self._parent, 4096), _Handoff, max_bytes=4096)
            if (
                self.handoff.nonce_hex != self.nonce.hex()
                or len(self.handoff.receiver_nonce_hex) != 64
                or any(c not in "0123456789abcdef" for c in self.handoff.receiver_nonce_hex)
                or self.handoff.receiver_nonce_hex == self.handoff.nonce_hex
                or self.handoff.packet_sha256 != self.profile.packet_sha256
                or self.handoff.producer_profile_sha256 != self.profile.producer_profile_sha256
                or len(self.handoff.process_profile_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.handoff.process_profile_sha256)
            ):
                raise InvalidAction("admission-channel-binding")
            self.consumed_raw = _record(self._conn, self._parent, _MAX_FRAME)
            self.check()
        except BaseException:
            self._stack.close()
            raise

    def check(self) -> None:
        """Parent must still own this exact live process relationship and ELF."""
        readiness = select.poll()
        readiness.register(self._pidfd, select.POLLIN)
        if os.getppid() != self._parent or readiness.poll(0):
            raise InvalidAction("admission-parent-ceased")
        if _stat_key(os.fstat(5)) != self._lease:
            raise InvalidAction("admission-launch-lease-changed")
        try:
            observed = Path(f"/proc/{self._parent}/exe").stat()
        except OSError as exc:
            raise InvalidAction("admission-parent-ceased") from exc
        pinned = os.fstat(self._binary)
        if (observed.st_dev, observed.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise InvalidAction("admission-parent-executable")

    def close(self) -> None:
        self._stack.close()
