"""Private fixed root installer primitive; no installed CLI or permit publisher.

Root custody of the profile/permit is required independently of their contents.
This module never manufactures an approval from a caller-supplied packet.
"""

import contextlib
import fcntl
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from heterodyne.actions.codec import (
    InvalidAction,
    _decode,  # pyright: ignore[reportPrivateUsage] - existing bounded closed codec
    encode_record,
)
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_host_store import (
    _private_root,  # pyright: ignore[reportPrivateUsage] - reuse protected descriptor walk
)
from heterodyne.actions.deployment_outage import decode_packet, packet_digest

_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_SUDO = Path("/usr/bin/sudo")
_BASH = Path("/usr/bin/bash")
_HELPER = Path("/usr/local/libexec/heterodyne/deployment-install-helper")
_HASH = re.compile(r"[0-9a-f]{64}")


class _Profile(Record, frozen=True):
    """One protected target; no request-selectable checkout, binary or arguments."""

    schema: Literal["heterodyne-root-install-profile.v1"]
    executor_uid: int
    target: Target
    checkout: str
    sudo_sha256: str
    bash_sha256: str
    helper_sha256: str


class _Permit(Record, frozen=True):
    """Protected admission owner's retained exact INSTALL step, not wire input.

    Its producer must already have authenticated whole-packet approval, admitted
    the finite outage capsule, and reconciled stopped_guard. No producer exists
    here. A receipt or correctly shaped file in unprotected storage is useless.
    """

    schema: Literal["heterodyne-root-install-permit.v1"]
    packet_sha256: str
    packet: str
    ordinal: int
    expected_before_sha256: str
    deadline_ns: int


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stat_key(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(fd: int, bound: int, mode: int = 0o400) -> bytes:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_uid != 0
        or stat.S_IMODE(before.st_mode) != mode or before.st_nlink != 1
        or not 0 < before.st_size <= bound
    ):
        raise InvalidAction("root-helper-file-custody")
    raw = bytearray()
    while chunk := os.pread(fd, min(65536, bound + 1 - len(raw)), len(raw)):
        raw.extend(chunk)
        if len(raw) > bound:
            raise InvalidAction("root-helper-file-bound")
    after = os.fstat(fd)
    if len(raw) != before.st_size or _stat_key(before) != _stat_key(after):
        raise InvalidAction("root-helper-file-changed")
    return bytes(raw)


def _binary(path: Path, expected: str) -> int:
    """Descriptor anchor on an exact protected executable, including ancestors."""
    if not _HASH.fullmatch(expected) or not path.is_absolute():
        raise InvalidAction("root-helper-binary-pin")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise InvalidAction("root-helper-binary-ancestor")
        executable = os.open(path.name, _FILE, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        info = os.fstat(executable)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not info.st_mode & 0o111 or info.st_nlink != 1 or info.st_size > 32 * 1024 * 1024
        ):
            raise InvalidAction("root-helper-binary-custody")
        observed = hashlib.sha256()
        offset = 0
        while chunk := os.pread(executable, 65536, offset):
            offset += len(chunk)
            if offset > 32 * 1024 * 1024:
                raise InvalidAction("root-helper-binary-bound")
            observed.update(chunk)
        if observed.hexdigest() != expected or _stat_key(os.fstat(executable)) != _stat_key(info):
            raise InvalidAction("root-helper-binary-drift")
        return executable
    except BaseException:
        os.close(executable)
        raise


def _sudo_parent(uid: int, sudo_sha256: str) -> None:
    """Require actual sudo monitor ancestry; SUDO_UID alone is never authority.

    Linux platform qualification must show the installed sudo retains its real
    invoking UID in the immediate monitor parent. An incompatible topology holds.
    Root is trusted, including its ability to replace a protected installation.
    """
    if os.getuid() != 0 or os.geteuid() != 0 or uid <= 0 or os.environ.get("SUDO_UID") != str(uid):
        raise InvalidAction("root-helper-sudo-identity")
    parent = os.getppid()
    process = Path("/proc") / str(parent)
    try:
        with contextlib.ExitStack() as stack:
            pidfd = os.pidfd_open(parent)
            stack.callback(os.close, pidfd)
            binary = _binary(_SUDO, sudo_sha256)
            stack.callback(os.close, binary)
            start = (process / "stat").read_bytes()
            status = (process / "status").read_text("ascii")
            uids = next(line.split()[1:] for line in status.splitlines() if line.startswith("Uid:"))
            if uids != [str(uid), "0", "0", "0"]:
                raise InvalidAction("root-helper-sudo-kernel-identity")
            executable = (process / "exe").stat()
            expected = os.fstat(binary)
            if (executable.st_dev, executable.st_ino) != (expected.st_dev, expected.st_ino):
                raise InvalidAction("root-helper-sudo-parent-executable")
            # /proc stat includes dynamic CPU fields; compare only PID identity
            # and starttime, with comm parsed through its final closing bracket.
            current = _process_identity((process / "stat").read_bytes())
            if os.getppid() != parent or _process_identity(start) != current:
                raise InvalidAction("root-helper-sudo-parent-changed")
    except (OSError, StopIteration, IndexError, UnicodeError) as exc:
        raise InvalidAction("root-helper-sudo-unavailable") from exc


def _process_identity(raw: bytes) -> tuple[bytes, bytes]:
    return raw.split(b" ", 1)[0], raw.rsplit(b") ", 1)[1].split()[19]


class _RootInstaller:  # pyright: ignore[reportUnusedClass] - private uninstalled effect primitive
    """Private root-owned one-use INSTALL effect; never releases the target.

    The owning installed factory must fix root/profile descriptors and qualify
    its interpreter/import closure. Neither path is an argument of a public CLI.
    The executor only supplies packet_sha256, checked against retained custody.
    """

    def __init__(self, root: Path):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise InvalidAction("root-helper-root-required")
        self.fd = _private_root(root)

    def close(self) -> None:
        os.close(self.fd)

    def _file(self, name: str, bound: int) -> bytes:
        fd = os.open(name, _FILE, dir_fd=self.fd)
        try:
            return _read(fd, bound)
        finally:
            os.close(fd)

    def _retained(self, requested: str):
        if not _HASH.fullmatch(requested):
            raise InvalidAction("root-helper-request-digest")
        profile_raw = self._file("profile.json", 16384)
        profile = _decode(profile_raw, _Profile)
        if (
            profile.schema != "heterodyne-root-install-profile.v1" or profile.executor_uid <= 0
            or not Path(profile.checkout).is_absolute()
            or str(Path(profile.checkout)) != os.path.normpath(profile.checkout)
            or ".." in Path(profile.checkout).parts
        ):
            raise InvalidAction("root-helper-profile")
        permit = _decode(self._file(requested + ".permit", 65536), _Permit, max_bytes=65536)
        packet = decode_packet(permit.packet.encode("utf-8"), (profile.target,))
        transition = packet.outage.transitions[2]
        if (
            permit.schema != "heterodyne-root-install-permit.v1"
            or permit.packet_sha256 != requested or packet_digest(packet) != requested
            or permit.ordinal != 2 or permit.expected_before_sha256 != transition.before_sha256
            or packet.root_profile_sha256 != _digest(profile_raw)
            or packet.root_helper_sha256 != profile.helper_sha256
            or packet.sudo_sha256 != profile.sudo_sha256
            or packet.release.artifacts.bootstrap_sha256 != packet.release.artifacts.installer_sha256
            or not packet.outage.not_before_ns <= time.time_ns() < permit.deadline_ns
            <= packet.outage.deadline_ns
        ):
            raise InvalidAction("root-helper-permit-binding")
        return profile, packet

    def run(self, requested: str) -> int:
        """Issue at most once. Any post-issuance exit/exception stays uncertain."""
        profile, packet = self._retained(requested)
        _sudo_parent(profile.executor_uid, profile.sudo_sha256)
        with contextlib.ExitStack() as stack:
            bash = _binary(_BASH, profile.bash_sha256)
            stack.callback(os.close, bash)
            helper = _binary(_HELPER, profile.helper_sha256)
            stack.callback(os.close, helper)
            # Per-target serialization is independent of per-packet one-use.
            lock = os.open("target.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                           0o600, dir_fd=self.fd)
            stack.callback(os.close, lock)
            _read_lock(lock)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InvalidAction("root-helper-target-busy") from None
            installer = self._file("installer-" + packet.release.artifacts.installer_sha256, 16 * 1024 * 1024)
            if _digest(installer) != packet.release.artifacts.installer_sha256:
                raise InvalidAction("root-helper-installer-drift")
            # Same root-private lifetime for retained bytes, hash and invocation.
            # No request-selected path, shell expression or additional arguments.
            with tempfile.TemporaryFile(dir="/run") as script:
                script.write(installer)
                script.flush()
                os.fchmod(script.fileno(), 0o400)
                os.fsync(script.fileno())
                _sudo_parent(profile.executor_uid, profile.sudo_sha256)
                profile_again, packet_again = self._retained(requested)
                if (profile_again, packet_again) != (profile, packet):
                    raise InvalidAction("root-helper-retained-drift")
                self._issue(requested, packet.release.execution_id)
                command = (
                    "/proc/self/fd/" + str(bash), "/proc/self/fd/" + str(script.fileno()),
                    "--no-wsd", "--no-verity", "--service-user", profile.target.service_user,
                    "--checkout", profile.checkout, "--commit", packet.release.artifacts.source_commit,
                    "--expect-sha256", packet.release.artifacts.release_sha256,
                )
                # A return code is diagnostic only. No APPLIED/COMPLETE receipt
                # or permission to retry is derived from success/failure here.
                return subprocess.run(
                    command, stdin=subprocess.DEVNULL, cwd="/", close_fds=True,
                    pass_fds=(bash, script.fileno(), lock), env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
                    check=False,
                ).returncode

    def _issue(self, requested: str, execution: str) -> None:
        try:
            fd = os.open(requested + ".issued", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400, dir_fd=self.fd)
        except FileExistsError:
            raise InvalidAction("root-helper-already-issued-unknown") from None
        try:
            raw = encode_record({"schema": "heterodyne-root-install-issued.v1",
                                 "packet_sha256": requested, "execution_id": execution,
                                 "outcome": "unknown"})
            with memoryview(raw) as view:
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise InvalidAction("root-helper-issued-write")
                    view = view[count:]
            os.fsync(fd)
            os.fsync(self.fd)
        finally:
            os.close(fd)


def _read_lock(fd: int) -> None:
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
    ):
        raise InvalidAction("root-helper-lock-custody")
