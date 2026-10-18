"""local-alert-v1: bounded local diagnostic files, never a delivery/acceptance receipt.

All participating publishers serialize the count/check/write/settle/cleanup sequence on the same
private directory lock. Service-UID callers attest their own episode lifecycle; no authorization is
created here. Existing admind filename deduplication can relay overflow only once per database.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.agents.base import write_private
from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.fsutil import private_dir

MAX_EXACT = 255
META = ".local-alert-v1.state"
LOCK = ".local-alert-v1.lock"
HEX32 = re.compile("[0-9a-f]{32}")
EPISODE = re.compile("[ -~]{1,128}")


def canonical(value: object) -> bytes:
    return json.dumps(
        msgspec.to_builtins(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


@dataclass(frozen=True)
class Intent:
    namespace: Literal["d6c", "p8"]
    owner_store_uuid: str
    owner_generation: int
    episode_id: str
    kind: Literal["failure", "recovery", "retirement"]
    created_at: str
    text: str

    def identity(self) -> tuple[int, str, str, int, str]:
        return (1, self.namespace, self.owner_store_uuid, self.owner_generation, self.episode_id)

    def filename(self) -> str:
        return self.namespace + "_" + hashlib.sha256(canonical(self.identity())).hexdigest()[:32] + ".json"

    def payload(self) -> bytes:
        if (
            self.namespace not in ("d6c", "p8")
            or not HEX32.fullmatch(self.owner_store_uuid)
            or type(self.owner_generation) is not int
            or self.owner_generation < 1
            or not EPISODE.fullmatch(self.episode_id)
            or self.kind not in ("failure", "recovery", "retirement")
            or not self.created_at.endswith("Z")
        ):
            raise ValueError("local alert identity")
        if datetime.fromisoformat(self.created_at.replace("Z", "+00:00")).utcoffset() is None:
            raise ValueError("local alert time")
        if len(self.text.encode()) > 65536 or sensitive_kind(self.text) is not None:
            raise ValueError("local alert text withheld")
        payload = canonical({"id": self.filename()[:-5], "created_at": self.created_at, "text": self.text})
        if len(payload) > 65536:
            raise ValueError("local alert payload limit")
        return payload


@contextmanager
def locked(directory: Path) -> Generator[None]:
    private_dir(directory)
    fd = os.open(directory / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("local alert lock is not private")
        until = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise TimeoutError("local alert directory busy") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


class Entry(msgspec.Struct, forbid_unknown_fields=True):
    identity: tuple[int, str, str, int, str]
    hash: str
    kind: Literal["failure", "recovery", "retirement"]
    filename: str
    settled: Literal["cleared", "retired", "published"] | None


class Aggregate(msgspec.Struct, forbid_unknown_fields=True):
    coalesced: int = 0
    retired: int = 0
    oldest: str | None = None


class Metadata(msgspec.Struct, forbid_unknown_fields=True):
    version: Literal[1]
    entries: dict[str, Entry]
    aggregates: dict[str, Aggregate]
    pending: dict[str, tuple[tuple[int, str, str, int, str], str]] = msgspec.field(
        default_factory=dict[str, tuple[tuple[int, str, str, int, str], str]]
    )


def _load(directory: Path) -> Metadata:
    try:
        fd = os.open(directory / META, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return Metadata(1, {}, {"d6c": Aggregate(), "p8": Aggregate()})
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_size > 262144
        ):
            raise ValueError("local alert metadata is not private")
        payload = os.read(fd, 262145)
    finally:
        os.close(fd)
    result = msgspec.json.decode(payload, type=Metadata)
    if (len(payload) > 262144 or len(result.entries) > MAX_EXACT or len(result.pending) > MAX_EXACT
            or set(result.aggregates) != {"d6c", "p8"}):
        raise ValueError("local alert metadata")
    if any(
        not 0 <= a.coalesced <= (1 << 64) - 1
        or not 0 <= a.retired <= (1 << 64) - 1
        or (a.oldest is not None and len(a.oldest) > 40)
        for a in result.aggregates.values()
    ):
        raise ValueError("local alert aggregate bounds")
    for filename, (identity, sha) in result.pending.items():
        if (identity[0] != 1 or identity[1] not in ("d6c", "p8")
                or not HEX32.fullmatch(identity[2]) or not 1 <= identity[3] <= (1 << 64) - 1
                or not EPISODE.fullmatch(identity[4]) or not re.fullmatch("[0-9a-f]{64}", sha)
                or filename != (identity[1] + "_"
                                + hashlib.sha256(canonical(identity)).hexdigest()[:32] + ".json")):
            raise ValueError("local alert pending identity")
    for filename, entry in result.entries.items():
        if (
            filename != entry.filename
            or "/" in filename
            or not filename.endswith(".json")
            or len(entry.hash) != 64
            or entry.identity[0] != 1
            or entry.identity[1] not in ("d6c", "p8")
            or not HEX32.fullmatch(entry.identity[2])
            or not 1 <= entry.identity[3] <= (1 << 64) - 1
            or not EPISODE.fullmatch(entry.identity[4])
            or filename
            != entry.identity[1] + "_" + hashlib.sha256(canonical(entry.identity)).hexdigest()[:32] + ".json"
            or not re.fullmatch("[0-9a-f]{64}", entry.hash)
        ):
            raise ValueError("local alert entry")
    return result


def _save(directory: Path, metadata: Metadata) -> None:
    write_private(directory / META, canonical(metadata).decode())


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > 65536
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("local alert file")
        payload = os.read(fd, 65537)
        if len(payload) > 65536:
            raise ValueError("local alert file bound")
        return payload
    finally:
        os.close(fd)


def publish(directory: Path, intent: Intent) -> Literal["written", "existing", "coalesced", "held"]:
    try:
        payload = intent.payload()
        sha = hashlib.sha256(payload).hexdigest()
        name = intent.filename()
        with locked(directory):
            metadata = _load(directory)
            entries = metadata.entries
            prior = metadata.pending.get(name)
            if prior is not None and prior != (intent.identity(), sha):
                return "held"
            existing = entries.get(name)
            if existing is not None:
                if existing.identity != intent.identity() or existing.hash != sha:
                    return "held"
                if existing.settled is not None:
                    return "existing"  # producer ledger remains responsible for dedup after cleanup
            for old_name, entry in list(entries.items()):
                if entry.settled is not None:
                    path = directory / old_name
                    if path.exists() and hashlib.sha256(_read(path)).hexdigest() != entry.hash:
                        return "held"
                    path.unlink(missing_ok=True)
                    del entries[old_name]
            if existing is None:
                # Foreign diagnostic files consume capacity and are never deleted or adopted.
                count = sum(
                    1
                    for path in directory.iterdir()
                    if path.name.endswith(".json")
                    and not path.name.startswith(".")
                    and path.name != "shared_overflow.json"
                )
                if count >= MAX_EXACT or len(entries) >= MAX_EXACT:
                    if count >= 256 and not (directory / "shared_overflow.json").exists():
                        return "held"
                    if prior is not None:
                        return "coalesced"  # same exact pending intent: no recount or overflow rewrite
                    if len(metadata.pending) >= MAX_EXACT:
                        return "held"  # bounded pending dedup never evicts an unsettled identity
                    metadata.pending[name] = (intent.identity(), sha)
                    aggregate = metadata.aggregates[intent.namespace]
                    aggregate.coalesced = min((1 << 64) - 1, aggregate.coalesced + 1)
                    aggregate.oldest = aggregate.oldest or intent.created_at
                    _save(directory, metadata)
                    overflow = canonical(
                        {
                            "id": "shared_overflow",
                            "created_at": intent.created_at,
                            "text": (
                                "Local diagnostic capacity reached; updates are local only. "
                                + " ".join(
                                    f"{ns}: coalesced={value.coalesced}, retired={value.retired}."
                                    for ns, value in sorted(metadata.aggregates.items())
                                )
                            ),
                        }
                    )
                    write_private(directory / "shared_overflow.json", overflow.decode())
                    return "coalesced"
                metadata.pending.pop(name, None)
                entries[name] = Entry(intent.identity(), sha, intent.kind, name, None)
                _save(directory, metadata)  # durable intended filename/hash before visible publication
            path = directory / name
            if path.exists():
                return "existing" if _read(path) == payload else "held"
            write_private(path, payload.decode())
            return "written"
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        return "held"


def settle(
    directory: Path,
    intent: Intent,
    expected_hash: str,
    disposition: Literal["cleared", "retired", "published"],
) -> bool:
    try:
        if hashlib.sha256(intent.payload()).hexdigest() != expected_hash:
            return False
        if disposition not in ("cleared", "retired", "published"):
            return False
        if disposition == "published" and intent.kind not in ("recovery", "retirement"):
            return False
        with locked(directory):
            metadata = _load(directory)
            entry = metadata.entries.get(intent.filename())
            if entry is None:
                # Coalescing counted this exact intent, but no individual file exists.
                # Owner clearance/retirement can release the dedup slot, never claim publication.
                pending = metadata.pending.get(intent.filename())
                if disposition == "published" or pending != (intent.identity(), expected_hash):
                    return False
                del metadata.pending[intent.filename()]
                if disposition == "retired":
                    aggregate = metadata.aggregates[intent.namespace]
                    aggregate.retired = min((1 << 64) - 1, aggregate.retired + 1)
                _save(directory, metadata)
                return True
            if (
                entry.identity != intent.identity()
                or entry.hash != expected_hash
                or entry.kind != intent.kind
            ):
                return False
            if disposition == "published":
                if hashlib.sha256(_read(directory / intent.filename())).hexdigest() != expected_hash:
                    return False
            if entry.settled is not None:
                return entry.settled == disposition
            entry.settled = disposition
            aggregate = metadata.aggregates[intent.namespace]
            if disposition == "retired":
                aggregate.retired = min((1 << 64) - 1, aggregate.retired + 1)
            _save(directory, metadata)
            return True
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        return False
