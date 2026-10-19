"""wsd's side of the alert-file contract with admind (`admind.alerts`, ADR 0001 §6.2): one `<id>.json` per
alert in the alerts directory, `{"id", "created_at", "text"}`, written to a dotfile first and then renamed
into place, so admind never reads a partial file. An alert whose file already exists is not written again:
admind relays each file once, and the id says which event it is about."""

import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

ALERT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")      # admind's


def write(alerts_dir: Path, alert_id: str, text: str, *, at: int) -> bool:
    """Write the alert `alert_id` unless it exists; whether it was written. `at` is UTC epoch seconds."""
    if not ALERT_NAME.fullmatch(alert_id):
        raise ValueError("an alert id is not one admind reads")
    final = alerts_dir / f"{alert_id}.json"
    if final.exists():
        return False
    alerts_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    created = datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = json.dumps({"id": alert_id, "created_at": created, "text": text}).encode()
    temp = alerts_dir / f".{alert_id}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        if final.exists():
            temp.unlink()
            return False
        temp.rename(final)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return True
