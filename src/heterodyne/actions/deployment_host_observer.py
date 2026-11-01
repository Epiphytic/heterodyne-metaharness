"""Private read-only host predicates. No admission, guard PASS or host effects.

Only a root-owned retained profile selects the target. Actual systemd manager
peers, pinned programs, stable installed-tree reads and a private DB copy supply
measurements. These observations do not exclude other clients or prove a guard.
"""

# pyright: reportPrivateUsage=false

import contextlib
import errno
import hashlib
import os
import pwd
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heterodyne.actions.codec import InvalidAction, _decode, encode_record
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment_cessation import (
    _LiveDatabase,
    _manager_endpoint,
    _ManagerScope,
    _source_directory,
)
from heterodyne.actions.deployment_host_store import _private_root
from heterodyne.actions.deployment_observation import HostObservation, decode_observation
from heterodyne.actions.deployment_root_helper import _binary, _read, _stat_key
from heterodyne.admind.binding import canonical
from heterodyne.harness.resources import store_core

_UNIT = 'heterodyne-admind.service'
_PROPERTIES = (
    'Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState',
    'NeedDaemonReload', 'Environment', 'MainPID', 'InvocationID',
)
_SHA = re.compile(r'[0-9a-f]{64}')
_RELEASE = re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')
_TREE_DOMAIN = b'heterodyne-deployment-installed-tree.v1\x00'


class _ObservationProfile(Record, frozen=True):
    schema: Literal['heterodyne-deployment-observer-profile.v1']
    target_id: str
    service_user: str
    service_uid: int
    runuser_sha256: str
    env_sha256: str
    systemctl_sha256: str
    releases: str
    install_record: str
    database: str
    scratch: str


@dataclass(frozen=True)
class _Readback:
    observation: HostObservation
    # Volatile actual evidence is deliberately outside the transition digest.
    unit_fields: tuple[tuple[str, str], ...]
    database_copy_sha256: str


def _profile(fd: int) -> _ObservationProfile:
    if os.getuid() != 0 or os.geteuid() != 0:
        raise InvalidAction('deployment-observer-root-required')
    p = _decode(_read(fd, 8192), _ObservationProfile, max_bytes=8192)
    if (
        not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', p.service_user)
        or p.service_uid <= 0 or pwd.getpwnam(p.service_user).pw_uid != p.service_uid
        or any(not _SHA.fullmatch(pin) for pin in (
            p.runuser_sha256, p.env_sha256, p.systemctl_sha256,
        ))
    ):
        raise InvalidAction('deployment-observer-profile')
    for name in (p.releases, p.install_record, p.database, p.scratch):
        if not name.startswith('/') or os.path.normpath(name) != name or not name.isascii():
            raise InvalidAction('deployment-observer-path')
    return p


def _parse_unit(raw: bytes) -> dict[str, str]:
    if len(raw) > 16384:
        raise InvalidAction('deployment-observer-unit-bound')
    result: dict[str, str] = {}
    for line in raw.decode('ascii', errors='strict').splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in _PROPERTIES or key in result:
            raise InvalidAction('deployment-observer-unit-shape')
        result[key] = value
    if set(result) != set(_PROPERTIES):
        raise InvalidAction('deployment-observer-unit-shape')
    if (
        result['Id'] != _UNIT or result['LoadState'] != 'loaded'
        or result['NeedDaemonReload'] != 'no'
        or result['UnitFileState'] not in {'enabled', 'disabled'}
        or (result['ActiveState'], result['SubState']) not in {
            ('active', 'running'), ('inactive', 'dead'),
        }
        or not re.fullmatch(r'0|[1-9][0-9]{0,19}', result['MainPID'])
        or (result['ActiveState'] == 'inactive') != (result['MainPID'] == '0')
    ):
        raise InvalidAction('deployment-observer-unit-unsettled')
    return result


def _endpoint_state(path: Path, uid: int) -> tuple[int, int, int, str]:
    try:
        device, inode, owner = _manager_endpoint(path, uid)
        return device, inode, owner, 'connected'
    except ConnectionRefusedError as exc:
        # systemctl falls back to the explicitly selected live session bus. Only
        # its fixed preferred private endpoint may be a stale, unchanged socket.
        if exc.errno != errno.ECONNREFUSED or path.name != 'private':
            raise
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            raise InvalidAction('deployment-observer-private-endpoint') from exc
        return info.st_dev, info.st_ino, info.st_uid, 'refused'


def _show(p: _ObservationProfile) -> dict[str, str]:
    runtime = Path('/run/user') / str(p.service_uid)
    endpoints = [runtime / 'systemd/private', runtime / 'bus']
    before = [_endpoint_state(path, p.service_uid) for path in endpoints]
    with contextlib.ExitStack() as stack:
        for path, pin in (
            (Path('/usr/sbin/runuser'), p.runuser_sha256),
            (Path('/usr/bin/env'), p.env_sha256),
            (Path('/usr/bin/systemctl'), p.systemctl_sha256),
        ):
            stack.callback(os.close, _binary(path, pin))
        # All executable paths are fixed protected host paths; never packet data.
        argv = (
            '/usr/sbin/runuser', '-u', p.service_user, '--', '/usr/bin/env', '-i',
            f'XDG_RUNTIME_DIR={runtime}',
            f'DBUS_SESSION_BUS_ADDRESS=unix:path={runtime / "bus"}', 'LC_ALL=C',
            '/usr/bin/systemctl', '--user', 'show', '--no-pager',
            '--property=' + ','.join(_PROPERTIES), '--', _UNIT,
        )
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=5, check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
            )
            output.seek(0)
            raw = output.read(16385)
        if result.returncode:
            raise InvalidAction('deployment-observer-manager-unavailable')
        for path, pin in (
            (Path('/usr/sbin/runuser'), p.runuser_sha256),
            (Path('/usr/bin/env'), p.env_sha256),
            (Path('/usr/bin/systemctl'), p.systemctl_sha256),
        ):
            checked = _binary(path, pin)
            os.close(checked)
    if [_endpoint_state(path, p.service_uid) for path in endpoints] != before:
        raise InvalidAction('deployment-observer-manager-changed')
    return _parse_unit(raw)


def _record(p: _ObservationProfile) -> tuple[str, bytes]:
    path = Path(p.install_record)
    parent = _source_directory(path.parent, p.service_uid)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode) or before.st_uid != p.service_uid
                or before.st_nlink != 1 or before.st_mode & 0o022 or before.st_size > 16384
            ):
                raise InvalidAction('deployment-observer-record-custody')
            raw = os.pread(fd, 16385, 0)
            if len(raw) != before.st_size or _stat_key(os.fstat(fd)) != _stat_key(before):
                raise InvalidAction('deployment-observer-record-changed')
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    # The installer's native closed version/release/units record; no new authority schema.
    value = _decode(raw, dict[str, object], max_bytes=16384)
    release = value.get('release')
    if (
        set(value) != {'version', 'release', 'units'} or type(value['version']) is not int
        or value['version'] != 1 or not isinstance(value['units'], dict)
        or not isinstance(release, str) or not _RELEASE.fullmatch(release)
    ):
        raise InvalidAction('deployment-observer-record-shape')
    return release, raw


@contextlib.contextmanager
def _running_release(p: _ObservationProfile, unit: dict[str, str], release: str) -> Generator[None]:
    """Bind the active process environment, not only configured unit Environment."""
    if unit['ActiveState'] == 'inactive':
        yield
        return
    pid = int(unit['MainPID'])
    with contextlib.ExitStack() as stack:
        pidfd = os.pidfd_open(pid)
        stack.callback(os.close, pidfd)
        process = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, process)

        def check() -> None:
            signal.pidfd_send_signal(pidfd, 0)
            values: dict[str, bytes] = {}
            for name in ('status', 'environ'):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=process)
                try:
                    values[name] = os.read(fd, 1024 * 1024 + 1)
                finally:
                    os.close(fd)
                if len(values[name]) > 1024 * 1024:
                    raise InvalidAction('deployment-observer-process-bound')
            uids = [line.split()[1:] for line in values['status'].splitlines()
                    if line.startswith(b'Uid:')]
            selected = str(p.service_uid).encode()
            marks = [part for part in values['environ'].split(b'\0')
                     if part.startswith(b'HETERODYNE_RELEASE=')]
            if uids != [[selected] * 4] or marks != [b'HETERODYNE_RELEASE=' + release.encode()]:
                raise InvalidAction('deployment-observer-running-release-mismatch')
            signal.pidfd_send_signal(pidfd, 0)

        check()
        yield
        check()


def _entry_owner(root: int, path: str, expected: store_core.Stat, uid: int) -> str:
    """Check owner on the exact nofollow entry whose statx is being manifested."""
    with contextlib.ExitStack() as stack:
        parent = root
        *directories, name = path.split('/')
        for part in directories:
            parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, parent)
        before = store_core.statx_at(parent, name)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (before != expected or store_core.statx_at(parent, name) != expected
                or info.st_uid not in {0, uid}
                or (expected.kind != 'link' and info.st_mode & 0o022)):
            raise InvalidAction('deployment-observer-tree-custody')
        return 'root' if info.st_uid == 0 else 'service'


def _tree_digest(fd: int, service_uid: int) -> str:
    """Reuse the bounded statx/fd walker; hash content/mode/link facts, not inode/time."""
    root_before = store_core.statx_at(fd, '')
    root_owner = os.fstat(fd).st_uid
    if root_owner not in {0, service_uid} or root_before.mode & 0o022:
        raise InvalidAction('deployment-observer-tree-custody')
    total = 0
    deadline = time.monotonic() + 30

    def bounded_read(source: int, size: int) -> bytes:
        nonlocal total
        part = os.read(source, size)
        total += len(part)
        if total > 2 * 1024**3 or time.monotonic() > deadline:
            raise InvalidAction('deployment-observer-tree-bound')
        return part

    # Store admission refuses external links. A host observation instead binds
    # their literal targets without ever following them; this is NOT a claim
    # that the directory closes its external interpreter/import dependencies.
    rows: list[dict[str, object]] = []
    first: list[tuple[str, store_core.Stat, str | None]] = []
    with contextlib.closing(store_core._traverse(fd)) as entries:
        for path, info, target in entries:
            if time.monotonic() > deadline:
                raise InvalidAction('deployment-observer-tree-bound')
            owner = _entry_owner(fd, path, info, service_uid)
            first.append((path, info, target))
            sha = store_core._hash_file(
                fd, path, bounded_read, info.__eq__, store_core.WalkRefused,
            ) if info.kind == 'file' else None
            rows.append({'path': path, 'kind': info.kind, 'mode': info.mode,
                         'size': info.size if info.kind == 'file' else 0,
                         'owner': owner, 'sha256': sha, 'target': target})
    with contextlib.closing(store_core._traverse(fd)) as entries:
        second: list[tuple[str, store_core.Stat, str | None]] = []
        for path, info, target in entries:
            if time.monotonic() > deadline:
                raise InvalidAction('deployment-observer-tree-bound')
            _entry_owner(fd, path, info, service_uid)
            second.append((path, info, target))
        if second != first:
            raise InvalidAction('deployment-observer-tree-changed')
    root_after = store_core.statx_at(fd, '')
    if root_before != root_after:
        raise InvalidAction('deployment-observer-tree-changed')
    manifest = canonical({'root_mode': root_before.mode,
                          'root_owner': 'root' if root_owner == 0 else 'service', 'entries': rows})
    return hashlib.sha256(_TREE_DOMAIN + manifest).hexdigest()


def _quarantine(p: _ObservationProfile) -> tuple[int, str]:
    scope = _ManagerScope(p.service_user, p.service_uid, p.runuser_sha256, p.env_sha256,
                          p.systemctl_sha256, Path(p.database))
    directory = _private_root(Path(p.scratch))
    try:
        with tempfile.TemporaryDirectory(prefix='.observe-', dir=f'/proc/self/fd/{directory}') as attempt:
            live = _LiveDatabase(scope)
            try:
                staged = Path(attempt) / 'live.db'
                live.copy(staged)
                # SQLite sees only copied files in root scratch, never a live service-owned path.
                with contextlib.closing(sqlite3.connect(staged.as_uri() + '?mode=ro', uri=True)) as db:
                    deadline = time.monotonic() + 5
                    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                    db.execute('PRAGMA query_only=ON')
                    db.execute('BEGIN')
                    if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                        raise InvalidAction('deployment-observer-database-integrity')
                    count = db.execute("SELECT COUNT(*) FROM outbox WHERE status='quarantined'").fetchone()[0]
                live.check()
                parts: list[dict[str, object]] = []
                for suffix in ('', '-wal'):
                    path = Path(str(staged) + suffix)
                    if not path.exists():
                        continue
                    digest = hashlib.sha256()
                    with path.open('rb') as source:
                        while part := source.read(65536):
                            digest.update(part)
                    parts.append({'suffix': suffix, 'size': path.stat().st_size,
                                  'sha256': digest.hexdigest()})
                # Diagnostic binding to copied inputs, not an approval or proof
                # of a historical transaction outcome.
                image = b'heterodyne-deployment-db-copy.v1\x00' + canonical(parts)
                return count, hashlib.sha256(image).hexdigest()
            finally:
                live.close()
    finally:
        os.close(directory)


def _observe(profile_fd: int) -> _Readback:  # pyright: ignore[reportUnusedFunction] - owning runner seam
    """Source-owned measurements only. Missing guards always remain unchecked."""
    try:
        p = _profile(profile_fd)
        before = _show(p)
        release, record = _record(p)
        environment = shlex.split(before['Environment'])
        releases = [e for e in environment if e.startswith('HETERODYNE_RELEASE=')]
        if releases != ['HETERODYNE_RELEASE=' + release]:
            raise InvalidAction('deployment-observer-release-mismatch')
        with _running_release(p, before, release):
            tree = _source_directory(Path(p.releases) / release, p.service_uid)
            try:
                digest = _tree_digest(tree, p.service_uid)
                quarantines, database = _quarantine(p)
                if _tree_digest(tree, p.service_uid) != digest:
                    raise InvalidAction('deployment-observer-tree-changed')
                current = _source_directory(Path(p.releases) / release, p.service_uid)
                try:
                    if _stat_key(os.fstat(current)) != _stat_key(os.fstat(tree)):
                        raise InvalidAction('deployment-observer-tree-replaced')
                finally:
                    os.close(current)
            finally:
                os.close(tree)
            if _record(p) != (release, record) or _show(p) != before:
                raise InvalidAction('deployment-observer-target-changed')
        observation = HostObservation(
            'heterodyne-deployment-observation.v1', p.target_id,
            'enabled' if before['UnitFileState'] == 'enabled' else 'disabled',
            'active' if before['ActiveState'] == 'active' else 'inactive', release,
            'unchecked', quarantines, digest,
        )
        observation = decode_observation(encode_record(observation))
        return _Readback(observation, tuple(sorted(before.items())), database)
    except (OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError,
            sqlite3.Error, store_core.WalkRefused) as exc:
        if isinstance(exc, InvalidAction):
            raise
        raise InvalidAction('deployment-observer-unavailable') from exc
