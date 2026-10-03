"""admind's append-only audit log: one JSON object per line (ADR 0001 §8).

Each record carries `ts` (UTC) and `kind`, plus the fields of the event (sender, text, action, result).
The file is opened per write with O_APPEND and fsync'd, so a crash never leaves a half-written earlier
record, and rotation by an external tool is safe. It is chmod'ed to 0600 on every open, must be a regular
file, and is never followed through a symlink. It is kept separate from beads on purpose.

Every field is redacted (`redact`), and 64-hex identifiers in `ID_FIELDS` are written as `ref_id`
references (ADR 0001 §8, revision 13; plan 2b B16).
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, cast

from heterodyne.admind.redact import redact, unique_key
from heterodyne.admind.store import now, private_dir

# Fields that hold message IDs or outbox keys. A 64-hex run in them becomes a stable reference instead of
# a redaction marker, so records about one message still correlate (B16).
ID_FIELDS = frozenset({"message_id", "reply_to", "key", "target", "anchor"})
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")


def ref_id(value: str) -> str:
    """How the audit names a 64-hex identifier: `id:` and 12 hex digits of its SHA-256."""
    return "id:" + hashlib.sha256(value.lower().encode()).hexdigest()[:12]


def clean(value: object, field: str | None = None) -> object:
    """A field value as it may be written: every string redacted, recursively; identifiers referenced."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if field in ID_FIELDS:
            value = _HEX_RUN.sub(lambda m: ref_id(m.group()), value)
        return redact(value)
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in cast(dict[Any, Any], value).items():
            out[unique_key(redact(str(k)), out)] = clean(v, str(k))
        return out
    if isinstance(value, list | tuple):
        return [clean(v, field) for v in cast(list[Any] | tuple[Any, ...], value)]
    return redact(str(value))


class Audit:
    def __init__(self, path: Path) -> None:
        self.path = path
        private_dir(path.parent)

    def write(self, kind: str, **fields: object) -> None:
        record = {"ts": now(), "kind": kind, **{k: clean(v, k) for k, v in fields.items()}}
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
