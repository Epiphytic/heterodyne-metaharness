"""Whether admind's store has a group, read without creating or changing anything (design btq-v7sgt D4).

The installer asks this before it enables admind. `_load()` is not used: it opens a writable Store, which
creates the database and runs its schema, and an Audit log. Even `mode=ro` can create -wal and -shm files
in a writable directory, so a database with no -wal (no admind has it open) is opened `immutable=1`. An
`admind init` racing this can only read as not yet initialised, which the installer reports and a rerun
settles. Only ENOENT reads as absent: any other failure is "can't tell"."""

import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

INITIALISED, NOT_INITIALISED, UNKNOWN = 0, 1, 2


def state(db: Path) -> int:
    try:
        st = os.lstat(db)
    except FileNotFoundError:   # the kernel's word that it is absent; EACCES and the like are not
        return NOT_INITIALISED
    except OSError:
        return UNKNOWN
    if not stat.S_ISREG(st.st_mode):
        return UNKNOWN
    mode = "ro" if os.path.lexists(f"{db}-wal") else "ro&immutable=1"
    try:
        con = sqlite3.connect(f"file:{quote(str(db))}?mode={mode}", uri=True, timeout=5.0)
        try:
            row = con.execute("SELECT value FROM kv WHERE key = 'group_id_hex'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return UNKNOWN
    return INITIALISED if row and row[0] else NOT_INITIALISED
