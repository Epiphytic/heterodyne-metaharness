"""Private kernel/filesystem boundary, never an approval or host executor opener.

Only the future protected factory may supply configuration. A submission and a
staged artifact prove neither human consent nor qualification of that factory.
"""

import contextlib
import fcntl
import hashlib
import os
import socket
import stat
import time
from collections.abc import Generator
from pathlib import Path
from typing import Literal

from heterodyne.actions.codec import MAX_BYTES, InvalidAction
from heterodyne.actions.deployment import Release, Target, decode_release, validate
from heterodyne.actions.deployment_outage import DeploymentPacket, packet_digest, validate_packet
from heterodyne.platform import peer_credentials_checked

Role = Literal["bootstrap", "release", "installer", "guard", "units", "dependency-lock"]
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_ROLES: tuple[Role, ...] = ("bootstrap", "release", "installer", "guard", "units", "dependency-lock")
_INGRESS_SECONDS = 2
_ROOT_INSTALL_HELPER = "/usr/local/libexec/heterodyne/deployment-install-helper"


def _private_root(path: Path) -> int:
    """Walk from / by descriptor; no writable/foreign directory or symlink."""
    if not path.is_absolute() or str(path) != os.path.normpath(path) or ".." in path.parts:
        raise InvalidAction("protected-store-path")
    fd = os.open("/", _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022:
                raise InvalidAction("protected-store-ancestor")
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise InvalidAction("protected-store-owner-mode")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_submission(conn: socket.socket, submit_uid: int, targets: tuple[Target, ...]) -> Release:
    """Read one untrusted closed value, only after actual kernel UID admission.

    submit_uid comes from protected configuration, never from the wire. This is
    build ingress, not an operator/cancel socket and not deployment authority.
    EOF frames the single request; extra bytes and a stalled peer refuse.
    """
    if submit_uid <= 0 or submit_uid == os.geteuid():
        raise InvalidAction("separate-submit-uid-required")
    try:
        if peer_credentials_checked(conn)[1] != submit_uid:
            raise InvalidAction("deployment-peer-refused")
        prior_timeout = conn.gettimeout()
        deadline = time.monotonic() + _INGRESS_SECONDS
        try:
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvalidAction("deployment-submission-deadline")
                conn.settimeout(remaining)
                chunk = conn.recv(min(4096, MAX_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise InvalidAction("deployment-submission-bound")
        finally:
            conn.settimeout(prior_timeout)
        return decode_release(bytes(raw), targets)
    except OSError as exc:
        raise InvalidAction("deployment-peer-unavailable") from exc


def _role(release: Release, role: Role) -> tuple[str, int]:
    if role not in _ROLES:
        raise InvalidAction("deployment-artifact-role")
    field = role.replace("-", "_") + "_sha256"
    return getattr(release.artifacts, field), 512 * 1024 * 1024 if role == "release" else 16 * 1024 * 1024


class _ProtectedStore:  # pyright: ignore[reportUnusedClass] - dormant protected factory primitive
    """Executor-owned descriptor anchor; no directory creation or account setup.

    Artifacts are immutable-by-publication, mode 0400 and named by exact digest.
    The distinct build UID is checked, but protected UID separation must still be
    qualified with the actual host account, ancestors and namespace boundaries.
    """

    def __init__(self, root: Path, build_uid: int):
        if os.geteuid() == 0 or build_uid <= 0 or build_uid == os.geteuid():
            raise InvalidAction("separate-executor-uid-required")
        self.fd = _private_root(root)

    def close(self) -> None:
        os.close(self.fd)

    @contextlib.contextmanager
    def target_lock(self, release: Release) -> Generator[None]:
        validate(release, (release.target,))
        name = hashlib.sha256(release.target.target_id.encode()).hexdigest() + ".lock"
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise InvalidAction("protected-target-lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InvalidAction("deployment-target-busy") from None
            os.fsync(fd)
            os.fsync(self.fd)
            yield
        finally:
            os.close(fd)

    def _readback(self, name: str, expected: str, bound: int) -> None:
        fd = os.open(name, _FILE_FLAGS, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_nlink != 1
                or info.st_size > bound
            ):
                raise InvalidAction("unsafe-staged-artifact")
            observed = hashlib.sha256()
            count = 0
            while chunk := os.read(fd, 65536):
                count += len(chunk)
                if count > bound:
                    raise InvalidAction("staged-artifact-bound")
                observed.update(chunk)
            if count != info.st_size or observed.hexdigest() != expected:
                raise InvalidAction("staged-artifact-drift")
        finally:
            os.close(fd)

    def stage(self, release: Release, role: Role, source_fd: int) -> str:
        """Copy original regular-file bytes; source ownership confers no authority.

        Repeated exact publication reads back the retained bytes. A failed copy
        leaves a retained partial file, never a usable digest name or an overwrite.
        """
        validate(release, (release.target,))
        expected, bound = _role(release, role)
        name = role + "-" + expected
        try:
            self._readback(name, expected, bound)
            return name
        except FileNotFoundError:
            pass
        source = os.fstat(source_fd)
        if not stat.S_ISREG(source.st_mode) or source.st_size > bound:
            raise InvalidAction("deployment-source-artifact")
        temporary = ".partial-" + role + "-" + os.urandom(16).hex()
        out = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=self.fd,
        )
        try:
            observed = hashlib.sha256()
            offset = 0
            while chunk := os.pread(source_fd, 65536, offset):
                offset += len(chunk)
                if offset > bound:
                    raise InvalidAction("deployment-source-bound")
                observed.update(chunk)
                with memoryview(chunk) as view:
                    while view:
                        written = os.write(out, view)
                        if written <= 0:
                            raise InvalidAction("deployment-stage-write")
                        view = view[written:]
            if offset != source.st_size or observed.hexdigest() != expected:
                raise InvalidAction("deployment-source-pin")
            os.fchmod(out, 0o400)
            os.fsync(out)
            try:
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            except FileExistsError:
                pass
            os.unlink(temporary, dir_fd=self.fd)
            os.fsync(self.fd)
            self._readback(name, expected, bound)
            return name
        finally:
            os.close(out)


def _installer_argv(  # pyright: ignore[reportUnusedFunction] - source-only fixed recipe composition
    packet: DeploymentPacket,
) -> tuple[str, ...]:
    """Name one fixed helper and packet; never delegate root bash to the executor.

    The root-owned helper/profile must independently allow the exact packet,
    UID, source closure and one-use operation, then perform the existing single
    private-copy/hash/execute lifetime. No such helper is installed or opened by
    this source foundation. Caller paths, commands and flags are not arguments.
    """
    release = packet.release
    validate_packet(packet, (release.target,))
    validate(release, (release.target,))
    if release.artifacts.bootstrap_sha256 != release.artifacts.installer_sha256:
        raise InvalidAction("pinned-bootstrap-installer-mismatch")
    return (
        "/usr/bin/sudo", "-n", _ROOT_INSTALL_HELPER, "--packet-sha256", packet_digest(packet),
    )
