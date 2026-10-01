"""The local alert-file contract between `wsd` and admind (ADR 0001 §6.2, §8).

`wsd` (plans 6 and 8) writes one file per alert into `<state>/alerts/`:

- name: `<id>.json`, where `<id>` matches ALERT_NAME;
- written atomically: to a dotfile first, then renamed into place (admind ignores dotfiles);
- content: `{"id": "<id>", "created_at": "<RFC 3339 UTC>", "text": "<plain text>"}`.

admind relays each file once, as a top-level message, and never modifies or deletes it: cleanup is the
writer's job. A file that doesn't follow the contract is relayed as a fixed "malformed alert" notice
that names the file, so a broken writer is still noticed.
"""

import re
from pathlib import Path

import msgspec

ALERT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
MALFORMED = "🚨 A malformed alert file was found: {name}.json. Check the alert directory on the host."


class Alert(msgspec.Struct, frozen=True):
    id: str
    created_at: str
    text: str


def scan(directory: Path) -> list[tuple[str, Alert | None]]:
    """Alerts in name order. None marks a malformed file (bad name, bad JSON, or id != name)."""
    try:
        entries = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    found: list[tuple[str, Alert | None]] = []
    for path in entries:
        if path.name.startswith(".") or path.suffix != ".json" or not path.is_file():
            continue
        name = path.stem
        alert: Alert | None = None
        if ALERT_NAME.fullmatch(name):
            try:
                alert = msgspec.json.decode(path.read_bytes(), type=Alert)
            except (OSError, msgspec.DecodeError):
                alert = None
            if alert is not None and alert.id != name:
                alert = None
        found.append((name, alert))
    return found


def render(name: str, alert: Alert | None, limit: int) -> str:
    if alert is None:
        return MALFORMED.format(name=name[:64])[:limit]
    text = f"🚨 wsd alert ({alert.created_at}): {alert.text}"
    if len(text) > limit:
        marker = " … (truncated; see the alert file)"
        text = (text[: max(0, limit - len(marker))] + marker)[: max(0, limit)]
    return text
