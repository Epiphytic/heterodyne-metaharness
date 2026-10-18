"""Immutable paired artifacts and calendar retention, never a three-store atomic checkpoint."""

import json
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Any, Literal, cast

from heterodyne.fsutil import private_dir
from heterodyne.wsd.backup_copy import digest, seal_manifest, snapshot

NativeStatus = Literal["succeeded", "failed", "unknown"]


@dataclass(frozen=True)
class Identity:
    installation: str
    generation: str
    owner_hash: str

    def record(self) -> dict[str, str]:
        return {
            "installation": self.installation,
            "generation": self.generation,
            "owner_hash": self.owner_hash,
        }


def copy_bundle(
    directory: Path,
    sources: tuple[Path, Path],
    cycle: int,
    native: NativeStatus,
    cancel: threading.Event,
    identity: Identity,
    attempt_id: str,
) -> dict[str, str]:
    if not re.fullmatch("[0-9a-f]{32}", attempt_id):
        raise ValueError("backup attempt identity")
    private_dir(directory)
    attempt = directory / attempt_id
    attempt.mkdir(mode=0o700)  # never overwrite even an incomplete predecessor
    copies: list[dict[str, str | int | float]] = []
    for number, source in enumerate(sources):
        target = attempt / f"local-{number}.db"
        captured = time.time()
        result = snapshot(source, target, cancel)
        copies.append({"file": target.name, "capture_utc": captured, **result})
    manifest: dict[str, object] = {
        "version": 1,
        **identity.record(),
        "cycle": cycle,
        "attempt": attempt.name,
        "completed_utc": time.time(),
        "native_status": native,
        "status": "succeeded" if native == "succeeded" else "partial",
        "capture": "individually-consistent-not-atomic",
        "copies": copies,
    }
    path = attempt / "manifest.json"
    seal_manifest(path, json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return {"status": str(manifest["status"]), "attempt": attempt.name, "manifest_sha256": digest(path)}


def verify_bundle(
    directory: Path, attempt_id: str, cycle: int, native: str, identity: Identity
) -> dict[str, str]:
    """Recovery after exclusive owner fencing: absence never settles an uncertain cycle."""
    if not re.fullmatch("[0-9a-f]{32}", attempt_id):
        raise ValueError("backup attempt identity")
    attempt = directory / attempt_id
    if attempt.is_symlink() or not attempt.is_dir():
        raise ValueError("backup attempt absent")
    path = attempt / "manifest.json"
    record = read_manifest(path)
    if (
        set(record)
        != {
            "version",
            "installation",
            "generation",
            "owner_hash",
            "cycle",
            "attempt",
            "completed_utc",
            "native_status",
            "status",
            "capture",
            "copies",
        }
        or any(record.get(key) != value for key, value in identity.record().items())
        or record.get("version") != 1
        or record.get("cycle") != cycle
        or record.get("attempt") != attempt_id
        or record.get("native_status") != native
        or record.get("capture") != "individually-consistent-not-atomic"
        or record.get("status") != ("succeeded" if native == "succeeded" else "partial")
        or len(record.get("copies", [])) != 2
    ):
        raise ValueError("backup manifest identity")
    for number, copy in enumerate(record["copies"]):
        target = attempt / f"local-{number}.db"
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("backup copy is not private")
        if (
            copy.get("file") != target.name
            or target.is_symlink()
            or target.stat().st_size != copy.get("bytes")
            or digest(target) != copy.get("sha256")
        ):
            raise ValueError("backup copy hash")
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",) or connection.execute(
                "PRAGMA user_version"
            ).fetchone() != (copy.get("schema"),):
                raise ValueError("backup copy integrity")
        finally:
            connection.close()
    return {"status": record["status"], "attempt": attempt_id, "manifest_sha256": digest(path)}


def retain(directory: Path, identity: Identity, now: float, unresolved: str | None) -> None:
    """Called on the copy lane. Never deletes foreign, unverifiable, or latest verified recovery data.

    Successful sets use UTC civil daily/weekly buckets, not cycle identity. Only known exact local
    artifacts participate. Incomplete/unverifiable artifacts hold for operator recovery, not deletion.
    """
    successes: list[tuple[float, Path]] = []
    partials: list[tuple[float, Path]] = []
    attempts = list(islice(directory.iterdir(), 257))
    if len(attempts) > 256:
        return  # unknown artifact saturation is a visible retention hold, never unbounded work
    for attempt in attempts:
        if attempt.name == unresolved or not re.fullmatch("[0-9a-f]{32}", attempt.name):
            continue
        try:
            raw = read_manifest(attempt / "manifest.json")
            verify_bundle(directory, attempt.name, raw["cycle"], raw["native_status"], identity)
            completed = float(raw["completed_utc"])
            if completed > now:
                continue
            (successes if raw["status"] == "succeeded" else partials).append((completed, attempt))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            continue
    successes.sort(reverse=True)
    keep: set[Path] = {successes[0][1]} if successes else set()
    days: set[str] = set()
    weeks: set[str] = set()
    today = datetime.fromtimestamp(now, UTC).date()
    monday = today - timedelta(days=today.weekday())
    for completed, attempt in successes:
        day = datetime.fromtimestamp(completed, UTC).date()
        week = day - timedelta(days=day.weekday())
        if 0 <= (today - day).days < 7 and day.isoformat() not in days:
            keep.add(attempt)
            days.add(day.isoformat())
        if 0 <= (monday - week).days < 28 and week.isoformat() not in weeks:
            keep.add(attempt)
            weeks.add(week.isoformat())
    partials.sort(reverse=True)
    if partials:
        keep.add(partials[0][1])
    for _, attempt in successes + partials:
        if attempt not in keep:
            shutil.rmtree(attempt)  # exact owned verified artifacts only; last success is always protected


def new_attempt() -> str:
    return uuid.uuid4().hex


def read_manifest(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_size > 8192
        ):
            raise ValueError("backup manifest bound")
        record = json.loads(os.read(fd, 8193))
    finally:
        os.close(fd)
    if not isinstance(record, dict):
        raise ValueError("backup manifest object")
    record = cast(dict[str, Any], record)
    if not math.isfinite(float(record.get("completed_utc", float("nan")))):
        raise ValueError("backup completion time")
    return record
