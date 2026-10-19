"""Bounded online WAL snapshots on an independent read-only connection.

No Journal constructor, migration, WSD journal lock or effect transaction participates in the copy.
The caller's dedicated backup worker owns the destination attempt directory and cancellation event.
"""

import hashlib
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from heterodyne.agents.base import write_private

MAX_WAL_GROWTH = 256 * 1024 * 1024
MIN_FREE = 512 * 1024 * 1024
MAX_SECONDS = 30 * 60


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while part := stream.read(1024 * 1024):
            result.update(part)
    return result.hexdigest()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def snapshot(
    source: Path, target: Path, cancel: threading.Event, *, monotonic: Callable[[], float] = time.monotonic
) -> dict[str, str | int]:
    """One consistent source snapshot, exclusively-created target, hashed only after durable close."""
    if source.is_symlink() or not source.is_file():
        raise ValueError("backup source is not a regular file")
    source = source.resolve(strict=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(fd)
    start = monotonic()
    wal = Path(str(source) + "-wal")
    initial_wal = _size(wal)
    reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
    writer = sqlite3.connect(target, timeout=5)
    try:
        if reader.execute("PRAGMA journal_mode").fetchone() != ("wal",):
            raise ValueError("online snapshot requires WAL")
        reader.execute("PRAGMA query_only=ON")
        reader.execute("BEGIN")
        # BEGIN alone is deferred: this SELECT actually pins the WAL read snapshot.
        reader.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
        schema = reader.execute("PRAGMA user_version").fetchone()[0]

        def progress(status: int, remaining: int, total: int) -> None:
            if (
                cancel.is_set()
                or monotonic() - start > MAX_SECONDS
                or _size(wal) - initial_wal > MAX_WAL_GROWTH
                or shutil.disk_usage(target.parent).free < MIN_FREE
            ):
                raise TimeoutError("bounded backup cancelled")

        reader.backup(writer, pages=128, progress=progress, sleep=0.05)
        writer.close()
        writer = None
    finally:
        if writer is not None:
            writer.close()
        reader.rollback()
        reader.close()
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    check = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("snapshot integrity")
        if check.execute("PRAGMA user_version").fetchone()[0] != schema:
            raise ValueError("snapshot schema changed")
    finally:
        check.close()
    directory = os.open(target.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"sha256": digest(target), "bytes": target.stat().st_size, "schema": int(schema)}


def seal_manifest(path: Path, text: str) -> None:
    """Call only after every referenced copy has closed, synced and passed integrity/hash checks."""
    write_private(path, text)
