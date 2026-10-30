"""Private source-owned observation of one retained admind systemd invocation.

Not all-client exclusion or operation settlement. A forced, reaped, empty unit
can cease even when its earlier effect is UNKNOWN; that history stays UNKNOWN.
This read-only component neither stops a process nor releases a deployment target.
"""

import os
import pwd
import select
import socket
import sqlite3
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_runtime import stream_hash
from heterodyne.platform import peer_credentials_checked
from heterodyne.wsd.backup_copy import snapshot as wal_snapshot

_UNIT = "heterodyne-admind.service"
_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID", "ExecMainPID",
    "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic", "InvocationID",
    "ControlGroup", "Result", "ExecMainCode", "ExecMainStatus",
)


@dataclass(frozen=True)
class _ManagerScope:
    """Protected factory configuration, never request JSON or caller authority."""

    service_user: str
    service_uid: int
    runuser_sha256: str
    env_sha256: str
    systemctl_sha256: str
    database: Path


@dataclass(frozen=True)
class UnitCessation:
    unit: str
    boot_id: str
    invocation_id: str
    pid: int
    started_monotonic_us: int
    exited_monotonic_us: int
    cgroup: str
    result: str
    exit_code: int
    exit_status: int
    outcome: str


@dataclass(frozen=True)
class StoppedSnapshot:
    cessation: UnitCessation
    source_device: int
    source_inode: int
    snapshot_sha256: str
    snapshot_bytes: int
    schema: int


def _boot() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()


def _number(fields: dict[str, str], key: str) -> int:
    value = fields[key]
    if not value.isascii() or not value.isdecimal() or len(value) > 20:
        raise InvalidAction("cessation-unit-number")
    return int(value)


def _manager_endpoint(endpoint: Path, uid: int) -> tuple[int, int, int]:
    """Kernel-authenticate one fixed internal endpoint, not a caller address.

    systemctl prefers systemd/private; authenticate that endpoint and its fallback
    bus both before and after the exact pinned command.
    """
    info = endpoint.stat(follow_symlinks=False)
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise InvalidAction("cessation-manager-socket")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(endpoint))
        if peer_credentials_checked(connection)[1] != uid:
            raise InvalidAction("cessation-manager-peer")
    return info.st_dev, info.st_ino, info.st_uid


class _AdmindInvocation:  # pyright: ignore[reportUnusedClass] - private protected recipe observer
    """Retain a kernel pidfd before stop, then observe the exact old invocation.

    Source qualification must still establish that this manager/cgroup scope
    covers the intended source and that no external client can commit. This
    component alone does not establish that wider claim.
    """

    def __init__(self, scope: _ManagerScope):
        self.scope = scope
        self.pidfd = self.group_fd = -1
        try:
            self._capture_live()
        except BaseException as exc:
            self.close()
            if isinstance(exc, InvalidAction):
                raise
            if isinstance(exc, (OSError, UnicodeError, KeyError, ValueError, subprocess.SubprocessError)):
                raise InvalidAction("cessation-capture-unavailable") from exc
            raise

    def _capture_live(self) -> None:
        self.boot = _boot()
        before = self._show()
        self._identity(before)
        if before["ActiveState"] != "active" or before["SubState"] != "running":
            raise InvalidAction("cessation-live-invocation-required")
        self.before = before
        self.pid = _number(before, "MainPID")
        if self.pid <= 0 or self.pid != _number(before, "ExecMainPID"):
            raise InvalidAction("cessation-live-pid-required")
        self.pidfd = os.pidfd_open(self.pid, 0)
        self.group_fd = self._capture_group()
        if self._show() != before or _boot() != self.boot:
            raise InvalidAction("cessation-invocation-changed")

    def close(self) -> None:
        for attribute in ("group_fd", "pidfd"):
            fd = getattr(self, attribute)
            if fd >= 0:
                os.close(fd)
                setattr(self, attribute, -1)

    def stopped_snapshot(self, destination: Path) -> StoppedSnapshot:
        """Newest live-path WAL snapshot surrounded by exact old-unit cessation.

        This records one unit and a consistent SQLite read. It is deliberately
        insufficient for target release: independent recovery authority and
        positive exclusion of every other writer are separate requirements.
        Destination is a fresh protected recipe output, never a backup to restore.
        """
        try:
            before = self.observe()
            source = self.scope.database
            for path in (source, *source.parents):
                info = path.stat(follow_symlinks=False)
                if (path.is_symlink() or info.st_uid not in {0, self.scope.service_uid}
                    or info.st_mode & 0o022):
                    raise InvalidAction("cessation-live-database-custody")
            original = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(original.st_mode):
                raise InvalidAction("cessation-live-database-regular")
            # Failed/partial snapshots stay in an attempt directory which is
            # removed on every path. Never delete/adopt a preexisting destination.
            with tempfile.TemporaryDirectory(prefix=".stopped-", dir=destination.parent) as attempt:
                temporary = Path(attempt) / "snapshot.db"
                record = wal_snapshot(source, temporary, threading.Event())
                after = self.observe()
                latest = source.stat(follow_symlinks=False)
                if (before != after or (latest.st_dev, latest.st_ino) != (original.st_dev, original.st_ino)
                    or source.is_symlink()):
                    raise InvalidAction("cessation-live-database-changed")
                published = False
                try:
                    os.link(temporary, destination, follow_symlinks=False)
                    published = True
                    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except BaseException:
                    if published:
                        destination.unlink()
                    raise
                return StoppedSnapshot(
                    after, original.st_dev, original.st_ino, str(record["sha256"]),
                    int(record["bytes"]), int(record["schema"]),
                )
        except (OSError, ValueError, TimeoutError, sqlite3.Error) as exc:
            if isinstance(exc, InvalidAction):
                raise
            raise InvalidAction("cessation-stopped-snapshot-unavailable") from exc

    def _capture_group(self) -> int:
        """Retain the original kernel cgroup, never infer removal from a path miss."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/sys/fs/cgroup", flags)
        try:
            for part in self.before["ControlGroup"].split("/")[1:]:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            membership = Path(f"/proc/{self.pid}/cgroup").read_text(encoding="ascii").splitlines()
            if membership != ["0::" + self.before["ControlGroup"]]:
                raise InvalidAction("cessation-pid-cgroup-changed")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _empty_group(self) -> None:
        try:
            fd = os.open(
                "cgroup.events", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                dir_fd=self.group_fd,
            )
        except FileNotFoundError:
            # Real cgroupfs retains st_nlink==2 after rmdir. The kernel's deleted
            # marker names the ORIGINAL anchored cgroup, never its current path.
            retained = str(Path(f"/proc/self/fd/{self.group_fd}").readlink())
            expected = "/sys/fs/cgroup" + self.before["ControlGroup"] + " (deleted)"
            if retained == expected:
                return
            raise
        try:
            raw = os.read(fd, 4097)
            if len(raw) > 4096 or raw.decode("ascii").splitlines().count("populated 0") != 1:
                raise InvalidAction("cessation-cgroup-populated")
        finally:
            os.close(fd)

    def _identity(self, fields: dict[str, str]) -> None:
        invocation = fields["InvocationID"]
        group = fields["ControlGroup"]
        if (
            fields["Id"] != _UNIT or fields["LoadState"] != "loaded"
            or len(invocation) != 32 or any(c not in "0123456789abcdef" for c in invocation)
            or invocation == "0" * 32 or _number(fields, "ExecMainStartTimestampMonotonic") <= 0
            or not group.startswith(f"/user.slice/user-{self.scope.service_uid}.slice/")
            or any(part in {"", ".", ".."} for part in group.split("/")[1:])
            or not group.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in group)
            or not group.endswith("/" + _UNIT)
        ):
            raise InvalidAction("cessation-unit-identity")

    def _show(self) -> dict[str, str]:
        """Actual fixed root helper read; no executable/env/unit from request data."""
        s = self.scope
        if os.geteuid() != 0 or s.service_uid <= 0 or pwd.getpwnam(s.service_user).pw_uid != s.service_uid:
            raise InvalidAction("cessation-protected-root-profile-required")
        runtime = Path("/run/user") / str(s.service_uid)
        bus = runtime / "bus"
        private = runtime / "systemd/private"
        endpoints = {endpoint: _manager_endpoint(endpoint, s.service_uid) for endpoint in (private, bus)}
        pins = (
            (Path("/usr/sbin/runuser"), s.runuser_sha256),
            (Path("/usr/bin/env"), s.env_sha256),
            (Path("/usr/bin/systemctl"), s.systemctl_sha256),
        )
        # Protected binary source checks surround the exact same invocation.
        for path, expected in pins:
            if stream_hash(path) != expected:
                raise InvalidAction("cessation-helper-source-drift")
        argv = (
            "/usr/sbin/runuser", "-u", s.service_user, "--", "/usr/bin/env", "-i",
            f"XDG_RUNTIME_DIR={runtime}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus}",
            "LC_ALL=C", "/usr/bin/systemctl", "--user", "show", "--no-pager",
            "--property=" + ",".join(_PROPERTIES), "--", _UNIT,
        )
        result = subprocess.run(
            argv, capture_output=True, timeout=5, check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if result.returncode or len(result.stdout) > 16384:
            raise InvalidAction("cessation-manager-unavailable")
        fields: dict[str, str] = {}
        for line in result.stdout.decode("ascii", errors="strict").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in fields or key not in _PROPERTIES:
                raise InvalidAction("cessation-unit-shape")
            fields[key] = value
        if set(fields) != set(_PROPERTIES):
            raise InvalidAction("cessation-unit-shape")
        for path, expected in pins:
            if stream_hash(path) != expected:
                raise InvalidAction("cessation-helper-source-drift")
        for endpoint, identity in endpoints.items():
            if _manager_endpoint(endpoint, s.service_uid) != identity:
                raise InvalidAction("cessation-manager-socket-changed")
        return fields

    def observe(self) -> UnitCessation:
        try:
            return self._observe_checked()
        except (OSError, UnicodeError, KeyError, ValueError, subprocess.SubprocessError) as exc:
            if isinstance(exc, InvalidAction):
                raise
            raise InvalidAction("cessation-observation-unavailable") from exc

    def _observe_checked(self) -> UnitCessation:
        before = self.before
        after = self._show()
        if _boot() != self.boot or any(
            after[key] != before[key] for key in (
                "Id", "LoadState", "ExecMainPID", "ExecMainStartTimestampMonotonic",
            )
        ):
            raise InvalidAction("cessation-invocation-changed")
        # systemd may clear InvocationID and ControlGroup when the unit goes
        # inactive. Any retained nonempty value must still name the old run.
        for key in ("InvocationID", "ControlGroup"):
            if after[key] not in {"", "0" * 32 if key == "InvocationID" else "", before[key]}:
                raise InvalidAction("cessation-invocation-changed")
        if (
            after["ActiveState"] not in {"inactive", "failed"}
            or after["SubState"] not in {"dead", "failed"} or _number(after, "MainPID") != 0
            or _number(after, "ExecMainExitTimestampMonotonic")
            < _number(before, "ExecMainStartTimestampMonotonic")
        ):
            raise InvalidAction("cessation-unit-not-stopped")
        poller = select.poll()
        poller.register(self.pidfd, select.POLLIN)
        if not any(fd == self.pidfd and mask & select.POLLIN for fd, mask in poller.poll(0)):
            raise InvalidAction("cessation-original-process-live")
        self._empty_group()
        if self._show() != after or _boot() != self.boot:
            raise InvalidAction("cessation-invocation-changed")
        code, status = _number(after, "ExecMainCode"), _number(after, "ExecMainStatus")
        # The authenticated manager applies SuccessExitStatus and normal stop
        # signals; code/status remain explicit evidence rather than reinterpreted.
        clean = after["Result"] == "success" and code in {1, 2, 3}
        return UnitCessation(
            _UNIT, self.boot, before["InvocationID"], self.pid,
            _number(before, "ExecMainStartTimestampMonotonic"),
            _number(after, "ExecMainExitTimestampMonotonic"), before["ControlGroup"],
            after["Result"], code, status, "clean" if clean else "forced",
        )
