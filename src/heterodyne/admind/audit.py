"""admind's append-only audit log: one JSON object per line (ADR 0001 §8).

Each record carries `ts` (UTC) and `kind`, plus the fields of the event (sender, text, action, result).
The file is opened per write with O_APPEND and fsync'd, so a crash never leaves a half-written earlier
record, and rotation by an external tool is safe. It is chmod'ed to 0600 on every open, must be a regular
file, and is never followed through a symlink. It is kept separate from beads on purpose.
"""

import json
import os
import stat
from pathlib import Path

from heterodyne.admind.store import now, private_dir


class Audit:
    def __init__(self, path: Path) -> None:
        self.path = path
        private_dir(path.parent)

    def write(self, kind: str, **fields: object) -> None:
        record = {"ts": now(), "kind": kind, **fields}
        data = (json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode()
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PermissionError(f"{self.path} is not a regular file")
            os.fchmod(fd, 0o600)    # on every open: a file that already existed may have looser bits
            # A torn earlier record (crash, or a write that failed part-way) leaves the file without a
            # trailing newline; start with one so the fragment stays on its own line and this record
            # is whole. Checked on every open, so it also covers a failed write in this process.
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                data = b"\n" + data
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
