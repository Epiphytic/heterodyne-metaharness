"""Whether admind's store has a group, read without creating or changing anything (design btq-v7sgt D4).

The installer asks this before it enables admind. `_load()` is not used: it opens a writable Store, which
creates the database and runs its schema, and an Audit log. SQLite can't read the live files in place
without changing them (a WAL reader takes a read mark in -shm) or without missing a concurrent commit
(`immutable=1`). So the database and its -wal are read as bytes, twice over, and only a pass whose two
reads agree is used. The bytes are copied into a private temporary directory and read there, where SQLite
rebuilds -shm and keeps only committed frames. Only ENOENT reads as absent: anything else that can't be
settled is "can't tell"."""

import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote

INITIALISED, NOT_INITIALISED, UNKNOWN = 0, 1, 2
ATTEMPTS = 3
SIDES = ("", "-wal")       # -shm is never read: the private copy rebuilds it from the WAL


class _Moved(Exception):
    pass


def _read(path: str) -> tuple[tuple[int, int, int], bytes] | None:
    """A regular file's identity and bytes, never through a symlink, or None when it is absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _Moved
        data = f.read()
        after = os.fstat(fd)
    ident = (st.st_ino, st.st_size, st.st_mtime_ns)
    if ident != (after.st_ino, after.st_size, after.st_mtime_ns) or len(data) != st.st_size:
        raise _Moved
    return ident, data


def _pass(db: Path) -> dict[str, tuple[tuple[int, int, int], bytes] | None] | None:
    """db, wal, db, wal: the first reads when the second agree with them, else None. Each file was then
    unchanged from its first read to its second, and those spans overlap, so the pair existed together."""
    if os.path.lexists(f"{db}-journal"):
        raise _Moved
    first = {s: _read(f"{db}{s}") for s in SIDES}
    second = {s: _read(f"{db}{s}") for s in SIDES}
    if os.path.lexists(f"{db}-journal"):
        raise _Moved
    return first if first == second else None


def state(db: Path) -> int:
    try:
        for _ in range(ATTEMPTS):
            snap = _pass(db)
            if snap is not None:
                break
        else:
            return UNKNOWN
    except (_Moved, OSError):
        return UNKNOWN
    if snap[""] is None:
        return UNKNOWN if snap["-wal"] is not None else NOT_INITIALISED
    try:
        with tempfile.TemporaryDirectory(prefix="admind-initialised.") as scratch:
            copy = Path(scratch) / "admind.db"
            for side, got in snap.items():
                if got is not None:
                    fd = os.open(f"{copy}{side}", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(got[1])
            con = sqlite3.connect(f"file:{quote(str(copy))}?mode=rw", uri=True, timeout=5.0)
            try:
                row = con.execute("SELECT value FROM kv WHERE key = 'group_id_hex'").fetchone()
            finally:
                con.close()
    except (OSError, sqlite3.Error):
        return UNKNOWN
    return INITIALISED if row and row[0] else NOT_INITIALISED
