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
from collections import deque
from collections.abc import Mapping, Sequence, Set
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path, PosixPath, PurePosixPath, PureWindowsPath, WindowsPath
from typing import Any, cast
from uuid import UUID

from heterodyne.admind.redact import redact, unique_key
from heterodyne.admind.store import now, private_dir

# Fields that hold message IDs or outbox keys. A 64-hex run in them becomes a stable reference instead of
# a redaction marker, so records about one message still correlate (B16).
ID_FIELDS = frozenset({"message_id", "reply_to", "key", "target", "anchor"})
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{64,}")


def ref_id(value: str) -> str:
    """How the audit names a 64-hex identifier: `id:` and 12 hex digits of its SHA-256."""
    return "id:" + hashlib.sha256(value.lower().encode()).hexdigest()[:12]


MAX_DEPTH = 64
# The exact stdlib classes whose str() is plain text, never an escaped rendering of a string they hold.
# Matched by `type()`, not `isinstance`: a subclass may override __str__.
_PLAIN = frozenset(
    {PurePosixPath, PureWindowsPath, PosixPath, WindowsPath, datetime, date, time, UUID, Decimal}
)
_SCALARS = frozenset({bool, int, float})


def clean(value: object, field: str | None = None) -> object:
    """A field value as it may be written: every string redacted, recursively; identifiers referenced.
    A cycle becomes "<cycle>" and nesting beyond MAX_DEPTH "<too deep>", so this always terminates."""
    return _clean(value, field, set(), 0)


def _clean(value: object, field: str | None, active: set[int], depth: int) -> object:
    if value is None or type(value) in _SCALARS:
        return value
    if isinstance(value, str):
        if field in ID_FIELDS:
            value = _HEX_RUN.sub(lambda m: ref_id(m.group()), value)
        return redact(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return redact(_text(cast(bytes, value)))
    if isinstance(value, int):    # a subclass (IntEnum, say) may override __str__: keep only the number
        return int.__int__(value)
    if isinstance(value, float):
        return float.__float__(value)
    if isinstance(value, Mapping | Set | Sequence | deque):
        node = cast(object, value)
        ident = id(node)
        if ident in active:
            return "<cycle>"
        if depth >= MAX_DEPTH:
            return "<too deep>"
        active.add(ident)
        try:
            return _container(node, field, active, depth + 1)
        finally:
            active.discard(ident)
    name = _type_name(value)
    if isinstance(value, BaseException):
        return {"type": name, "args": _clean(value.args, field, active, depth + 1)}
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - an object's own __str__ is unknown code; the audit must still write
        return f"<{name}: unprintable>"
    if type(value) in _PLAIN:
        return redact(text)
    # An object's own __str__ is unknown code. One that escapes a control character (as repr does) turns
    # "\n" into a letter and hides where a token starts, so a backslash means the text is withheld, not
    # redacted. A backslash-free str() has no such escape. This covers subclasses of the plain types and
    # Enum members too: their default str() has no backslash.
    if "\\" in text:
        return f"<{name}: withheld>"
    return redact(text)


def _type_name(value: object) -> str:
    """A type's name as it may be written. `type()` can build a name holding a token or a control character,
    so it is redacted, and one that still holds a backslash or a control character is replaced."""
    name = redact(type(value).__name__)
    if "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "object"
    return name


def _container(value: object, field: str | None, active: set[int], depth: int) -> object:
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for k, v in cast(Mapping[Any, Any], value).items():
            key = _key(k, active, depth)
            out[unique_key(key, out)] = _clean(v, key, active, depth)
        return out
    if isinstance(value, Set):
        items = [_clean(v, field, active, depth) for v in cast(Set[Any], value)]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, default=str))   # deterministic
    return [_clean(v, field, active, depth) for v in cast(Sequence[Any], value)]


def _key(key: object, active: set[int], depth: int) -> str:
    """A dict key as it may be written. Scalar keys keep their `str()` spelling; others are cleaned first,
    and a compound key becomes JSON of its cleaned parts, which is safe because every string in it is
    already redacted."""
    if key is None or type(key) in _SCALARS:
        return str(key)
    cleaned = _clean(key, None, active, depth)
    if isinstance(cleaned, str):
        return cleaned
    return json.dumps(cleaned, sort_keys=True, default=str, ensure_ascii=False)


def _text(value: object) -> str:
    """A value's text for redaction. Bytes are decoded, not `str()`ed: its repr would escape a control
    character into a letter (`\\n` into `n`) before redaction, which hides where a token starts."""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(cast(bytes, value)).decode("utf-8", "replace")
    return str(value)


class Audit:
    def __init__(self, path: Path) -> None:
        self.path = path
        private_dir(path.parent)

    def write(self, kind: str, **fields: object) -> None:
        record = cast(dict[str, object], clean({"ts": now(), "kind": kind, **fields}))
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
