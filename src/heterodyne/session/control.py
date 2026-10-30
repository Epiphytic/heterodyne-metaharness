"""Protected durable intake for untrusted manager intent, never effect authority.

Only the owning daemon consumes this journal. Socket handlers take only its short
cross-process lock; they never invoke Hermes or hold a Hermes store lock.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from heterodyne.fsutil import private_dir
from heterodyne.session.shim import CONTROL_REPLY_BYTES, control_uuid, parse_control, strict_json
from heterodyne.wsd.gate import open_lock_file


class ControlConflict(ValueError):
    """A successfully read immutable request has different offered bytes."""


class ControlCapacity(ValueError):
    """Intake refuses without accepting a 101st unanswered request."""


PENDING_LIMIT = 100
STATES = frozenset({"accepted", "pending", "completed", "refused", "uncertain"})
TERMINAL = frozenset({"completed", "refused"})


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path: Path, raw: bytes) -> None:
    """Atomic private file plus directory durability before returning acknowledgement."""
    fd, name = tempfile.mkstemp(prefix=".intake-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
        _sync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(name).unlink()


def _read(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("retained control record is not a regular file")
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(fd, limit + 1 - len(raw))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
        raise ValueError("retained control record exceeds bound")
    finally:
        os.close(fd)


class ControlOwner:
    """Actual lifetime flock for the one owning Hermes daemon, never a ready flag."""
    def __init__(self, root: Path):
        private_dir(root)
        self.pid = os.getpid()
        self.fd: int | None = open_lock_file(root / "owner.lock")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        if self.fd is None or self.pid != os.getpid():
            raise RuntimeError("manager control owner is unavailable")
        os.fstat(self.fd)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class ControlJournal:
    """A host-selected generation directory outside every sandbox bind mount."""
    def __init__(self, root: Path, owner: ControlOwner | None = None):
        self.root = root
        self.owner = owner
        if owner is None:
            if not all((root / name).is_dir() for name in ("requests", "pending", "outcomes")):
                raise FileNotFoundError("manager journal is unavailable")
            return
        owner.check()
        for folder in (root, root / "requests", root / "pending", root / "outcomes"):
            private_dir(folder)
        os.close(open_lock_file(root / "journal.lock"))
        _sync_dir(root)
        _sync_dir(root.parent)

    def _writable(self) -> None:
        if self.owner is None:
            raise RuntimeError("read-only journal cannot admit or answer intent")
        self.owner.check()

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        fd = (open_lock_file(self.root / "journal.lock") if self.owner is not None
              else os.open(self.root / "journal.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC))
        try:
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.005)
            yield
        finally:
            os.close(fd)

    def _path(self, category: str, request_id: str) -> Path:
        if not control_uuid(request_id):
            raise ValueError("invalid retained control identity")
        return self.root / category / (request_id + ".json")

    def _outcome(self, request_id: str) -> dict[str, Any] | None:
        path = self._path("outcomes", request_id)
        if not path.exists():
            return None
        value = strict_json(_read(path, CONTROL_REPLY_BYTES))
        if (not isinstance(value, dict) or set(value) != {"ok", "state", "request_sha256", "data"}
                or not isinstance(value["state"], str) or value["state"] not in STATES
                or type(value["ok"]) is not bool
                or value["ok"] != (value["state"] != "refused") or not isinstance(value["data"], dict)
                or value["request_sha256"] != hashlib.sha256(
                    _read(self._path("requests", request_id), 64 * 1024)).hexdigest()):
            raise ValueError("invalid retained control outcome")
        return value

    def _pending(self, reap: bool = False) -> list[str]:
        result = []
        with os.scandir(self.root / "pending") as entries:
            for entry in entries:
                # A killed atomic writer can leave its private temporary file.
                # It was never an admission marker. Never follow arbitrary entries.
                if (re.fullmatch(r"\.intake-[a-z0-9_]{8}", entry.name)
                        and entry.is_file(follow_symlinks=False)):
                    if reap:
                        self._writable()
                        Path(entry.path).unlink()
                        _sync_dir(self.root / "pending")
                    continue
                if not entry.name.endswith(".json"):
                    raise ValueError("unexpected pending control entry")
                request_id = entry.name[:-5]
                if _read(self._path("pending", request_id), 2) != b"{}":
                    raise ValueError("invalid retained pending marker")
                outcome = self._outcome(request_id)
                if outcome is not None and outcome["state"] in TERMINAL:
                    if reap:
                        self._writable()
                        Path(entry.path).unlink()
                        _sync_dir(self.root / "pending")
                    continue
                result.append(request_id)
                if len(result) > PENDING_LIMIT:
                    raise ValueError("pending control journal exceeds bound")
        return sorted(result)

    def intake(self, raw: bytes, *, stopped: threading.Event | None = None) -> dict[str, Any]:
        self._writable()
        request = parse_control(raw)
        request_id = request["request_id"]
        digest = hashlib.sha256(raw).hexdigest()
        with self.locked():
            if stopped is not None and stopped.is_set():
                raise RuntimeError("closed generation refuses intake")
            path = self._path("requests", request_id)
            if path.exists() and _read(path, 64 * 1024) != raw:
                raise ControlConflict("control request identity already pins different bytes")
            outcome = self._outcome(request_id)
            if outcome is not None:
                return outcome
            marker = self._path("pending", request_id)
            if not marker.exists():
                if len(self._pending(reap=True)) >= PENDING_LIMIT:
                    raise ControlCapacity("100 unanswered manager requests; intake refused")
                if stopped is not None and stopped.is_set():
                    raise RuntimeError("closed generation refuses intake")
                if not path.exists():
                    _write(path, raw)
                # The immutable request is durable before its admission marker, and
                # the marker is durable before acceptance. Orphans were never acked.
                if stopped is not None and stopped.is_set():
                    raise RuntimeError("closed generation refuses admission marker")
                _write(marker, b"{}")
            return {"ok": True, "state": "accepted", "request_sha256": digest, "data": {}}

    def pending(self) -> list[dict[str, Any]]:
        with self.locked():
            return [{"request": parse_control(raw), "raw": raw,
                     "request_sha256": hashlib.sha256(raw).hexdigest()}
                    for request_id in self._pending()
                    for raw in [_read(self._path("requests", request_id), 64 * 1024)]]

    def outcome(self, request_id: str) -> dict[str, Any] | None:
        with self.locked():
            return self._outcome(request_id)

    def reply(self, request_id: str, state: str, data: dict[str, Any]) -> dict[str, Any]:
        self._writable()
        if state not in STATES or not isinstance(data, dict):
            raise ValueError("invalid control reply")
        with self.locked():
            raw = _read(self._path("requests", request_id), 64 * 1024)
            digest = hashlib.sha256(raw).hexdigest()
            result = {"ok": state != "refused", "state": state, "request_sha256": digest, "data": data}
            encoded = json.dumps(result, allow_nan=False, sort_keys=True).encode()
            result = strict_json(encoded)  # compare the same durable JSON projection on replay
            if len(encoded) + 1 > CONTROL_REPLY_BYTES:
                raise ValueError("control reply exceeds bound; operator route required")
            prior = self._outcome(request_id)
            if prior is not None and prior["state"] in TERMINAL:
                if prior != result:
                    raise ValueError("terminal control outcome is immutable")
                return prior
            _write(self._path("outcomes", request_id), encoded)
            if state in TERMINAL:
                marker = self._path("pending", request_id)
                marker.unlink(missing_ok=True)
                _sync_dir(marker.parent)
            return result
