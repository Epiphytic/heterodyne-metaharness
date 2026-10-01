"""The local alert-file contract between `wsd` and admind (ADR 0001 §6.2, §8).

`wsd` (plans 6 and 8) writes one file per alert into `<state>/alerts/`:

- name: `<id>.json`, where `<id>` matches ALERT_NAME;
- written atomically: to a dotfile first, then renamed into place (admind ignores dotfiles);
- content: `{"id": "<id>", "created_at": "<RFC 3339 UTC>", "text": "<plain text>"}`.

admind relays each file once, as a top-level message, and never modifies or deletes it: cleanup is the
writer's job. A file that doesn't follow the contract is relayed as a fixed "malformed alert" notice
that names the file, so a broken writer is still noticed.

Output safety: files are opened without following symlinks and without blocking (a FIFO would hang the
open), must be regular and at most MAX_ALERT_BYTES, and anything else counts as malformed. An alert whose
text holds a secret, an npub or a 64-hex identifier is withheld (a fixed notice names the file and the
kind of thing found), and control characters are escaped, so a hostile file cannot drive the chat client.
"""

import os
import re
import stat
from pathlib import Path

import msgspec

from heterodyne.config.secret_scan import sensitive_kind, show

ALERT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
MALFORMED = "🚨 A malformed alert file was found: {name}.json. Check the alert directory on the host."
WITHHELD = "🚨 A wsd alert ({name}.json) was withheld because it contains {kind}. Read it on the host."
MAX_ALERT_BYTES = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # all C0, DEL and C1 except tab and newline


class Alert(msgspec.Struct, frozen=True):
    id: str
    created_at: str
    text: str


def _read(path: Path) -> bytes | None:
    """The file's bytes if it is a regular file of sane size, else None. Never follows a symlink, never
    blocks (O_NONBLOCK so a FIFO without a writer fails or is rejected by fstat, not hung on)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_ALERT_BYTES:
            return None
        chunks: list[bytes] = []
        total = 0
        while data := os.read(fd, MAX_ALERT_BYTES + 1 - total):
            chunks.append(data)
            total += len(data)
            if total > MAX_ALERT_BYTES:
                return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def _is_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def scan(directory: Path) -> list[tuple[str, Alert | None]]:
    """Alerts in name order. None marks a malformed file (bad name, bad JSON, or id != name)."""
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    found: list[tuple[str, Alert | None]] = []
    for path in entries:
        if path.name.startswith(".") or path.suffix != ".json" or _is_dir(path):
            continue
        name = path.stem
        alert: Alert | None = None
        if ALERT_NAME.fullmatch(name):
            data = _read(path)
            try:
                alert = None if data is None else msgspec.json.decode(data, type=Alert)
            except msgspec.DecodeError:
                alert = None
            if alert is not None and alert.id != name:
                alert = None
        found.append((name, alert))
    return found


def _escape(text: str) -> str:
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def render(name: str, alert: Alert | None, limit: int) -> str:
    safe_name = show(name, False)[:64]
    if alert is None:
        return MALFORMED.format(name=safe_name)[:limit]
    kind = sensitive_kind(alert.created_at) or sensitive_kind(alert.text)
    if kind is not None:
        return WITHHELD.format(name=safe_name, kind=kind)[:limit]
    text = f"🚨 wsd alert ({_escape(alert.created_at)}): {_escape(alert.text)}"
    if len(text) > limit:
        marker = " … (truncated; see the alert file)"
        text = (text[: max(0, limit - len(marker))] + marker)[: max(0, limit)]
    return text
