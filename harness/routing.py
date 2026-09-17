"""Durable routing for already-authorized Marmot text, independent of agent type."""
from __future__ import annotations

import asyncio
import logging
import hashlib
import json
import tempfile
from itertools import islice
import os
from pathlib import Path
import sqlite3
import time
from typing import Iterable, Mapping


class RoutingConflict(RuntimeError):
    """The channel has ambiguous ownership; never run a gateway fallback turn."""


def enqueue(group_id: str, message_id: str, text: str, sender: str, *,
            db_path: str | Path | None = None) -> bool:
    """Queue once for a managed channel; false means no active owner.

    The caller MUST authenticate the sender. Local CLI access is trusted, like
    direct filesystem access. Storage errors and ambiguous ownership propagate:
    a gateway caller must not interpret either as permission to handle the turn.
    """
    if not group_id or not message_id or not sender or not text.strip():
        raise ValueError("group, message ID, sender, and text are required")
    path = _database_path(db_path)
    if not path.exists():
        return False
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT id FROM runs WHERE lower(group_id)=? "
            "AND state NOT IN ('completed', 'stopped', 'archived')",
            (group_id.lower(),),
        ).fetchall()
        if not rows:
            return False
        if len(rows) != 1:
            raise RoutingConflict("multiple active runs own this Marmot channel")
        # Namespace the deduplication key by channel; replay remains consumed even
        # if a newer run later takes ownership of this same channel.
        key = hashlib.sha256((group_id.lower() + "\0" + message_id.lower()).encode()).hexdigest()
        db.execute(
            "INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state) VALUES(?,?,?,?,?)",
            (key, rows[0][0], f"[Marmot sender {sender}]\n{text}", time.time(), "pending"),
        )
    return True


def route_inbound(event: Mapping, *, account_id: str,
                  allowed_senders: Iterable[str], db_path: str | Path | None = None) -> bool:
    """Route an explicitly authorized normalized inbound event by channel ownership.

    Only explicitly allowed, non-self senders may enter a managed run. Rejected
    events return false so the gateway's ordinary authorization path still runs.
    This deliberately does not adopt MARMOT_ALLOW_ALL_USERS as authorization.
    """
    sender = str(event.get("sender_account_id_hex") or "").lower()
    account = account_id.lower()
    if (not sender or sender == account
            or str(event.get("account_id_hex") or "").lower() != account
            or sender not in {value.lower() for value in allowed_senders}):
        return False
    text = str(event.get("text") or "")
    if not text.strip():
        return False
    record = {"group_id": str(event.get("group_id_hex") or ""),
              "message_id": str(event.get("message_id_hex") or ""),
              "text": text, "sender": sender}
    if not record["group_id"] or not record["message_id"]:
        raise ValueError("group and message ID are required")
    # Admission becomes crash-durable BEFORE entering the independently failing
    # SQLite transaction. No model prompt participates in this handoff.
    spool = _spool_record(record, db_path)
    handled = enqueue(**record, db_path=db_path)
    # On healthy fallthrough the ordinary gateway retains responsibility. If we
    # crash before this point, an unowned record remains for explicit recovery.
    _remove_spool(spool)
    return handled



async def route_with_retry(event: Mapping, *, account_id: str,
                           allowed_senders: Iterable[str],
                           db_path: str | Path | None = None) -> bool:
    """Keep the per-group queue occupied until durable handoff is possible.

    DB failures and ownership conflicts cannot become gateway turns or silently
    consumed messages. Authenticated text is first fsynced to a private spool;
    the supervisor drains owned records after gateway/process/host restart.
    Disk write failure remains a failed admission, not an exactly-once guarantee.
    """
    while True:
        try:
            return await asyncio.to_thread(route_inbound, event, account_id=account_id,
                                           allowed_senders=allowed_senders, db_path=db_path)
        except (sqlite3.Error, RoutingConflict, OSError):
            logging.getLogger(__name__).warning(
                "Workstream inbound handoff unavailable; retaining queued turn for retry",
                exc_info=True,
            )
            await asyncio.sleep(30)


def _database_path(db_path):
    return Path(db_path) if db_path is not None else (
        Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        / "workstreams" / "harness.sqlite3"
    )


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _spool_record(record, db_path):
    directory = _database_path(db_path).parent / "incoming-spool"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    _sync_directory(directory.parent)
    key = hashlib.sha256((record["group_id"].lower() + "\0" + record["message_id"].lower()).encode()).hexdigest()
    target = directory / (key + ".json")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".pending-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, target)
            _sync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def _remove_spool(path):
    path.unlink(missing_ok=True)
    _sync_directory(path.parent)


def drain_spool(db_path=None):
    """Retry up to 100 durable admissions. Never dispatch unowned records.

    Unowned/error records are retained for gateway replay or explicit recovery;
    returned counts make them visible to the supervisor. Private spool contents
    were authorized by the gateway before writing, like local CLI submissions.
    """
    directory = _database_path(db_path).parent / "incoming-spool"
    counts = {"delivered": 0, "unowned": 0, "errors": 0}
    for path in islice(directory.glob("*.json"), 100):
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("oversized spool record")
            record = json.loads(path.read_text())
            if enqueue(**record, db_path=db_path):
                _remove_spool(path)
                counts["delivered"] += 1
            else:
                counts["unowned"] += 1
        except (sqlite3.Error, RoutingConflict, OSError, ValueError, TypeError):
            counts["errors"] += 1
    return counts
