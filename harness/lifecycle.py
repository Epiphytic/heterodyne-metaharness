"""Read native lifecycle records by exact session identity; never infer task completion."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

READ_LIMIT = 1024 * 1024
SUMMARY_LIMIT = 16000


def _transcript(run):
    session = run.get("native_session_id", "")
    if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return None
    config = run.get("config", {})
    if run.get("agent") == "codex":
        home = Path(config.get("codex_home") or os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        # The highest numeric schema version is the current native database.
        databases = sorted(home.glob("state_*.sqlite"),
                           key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else -1,
                           reverse=True)
        for database in databases:
            with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=1) as db:
                try:
                    row = db.execute("SELECT rollout_path FROM threads WHERE id=?", (session,)).fetchone()
                except sqlite3.OperationalError:
                    continue
            if row:
                return Path(row[0]).expanduser()
    elif run.get("agent") == "claude":
        home = Path(config.get("claude_home") or os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
        matches = list((home / "projects").glob(f"*/{session}.jsonl"))
        # Ambiguous identities must never select someone else's transcript.
        if len(matches) == 1:
            return matches[0]
    # Hermes does not expose a verified turn-completion marker in its messages DB.
    return None


def _native_event(agent, record):
    if agent == "codex":
        payload = record.get("payload") or {}
        kind = payload.get("type") if isinstance(payload, dict) else None
        if record.get("type") == "compacted" or kind == "context_compacted":
            return {"kind": "compacted", "summary": "Native context compacted; session identity retained."}
        if record.get("type") != "event_msg":
            return None
        if kind in {"task_complete", "turn_complete"}:
            return {"kind": "turn_completed", "summary": str(payload.get("last_agent_message") or "")[:SUMMARY_LIMIT]}
        if kind in {"task_started", "turn_started"}:
            return {"kind": "working", "summary": "Native agent turn started."}
        if kind in {"exec_approval_request", "apply_patch_approval_request"}:
            return {"kind": "approval", "summary": "Native agent requests approval."}
    elif agent == "claude":
        if record.get("isSidechain"):
            return None
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            return {"kind": "compacted", "summary": "Native context compacted; session identity retained."}
        message = record.get("message") or {}
        if not isinstance(message, dict):
            return None
        if record.get("type") == "assistant" and message.get("stop_reason") == "end_turn":
            content = message.get("content", [])
            summary = "\n".join(block.get("text", "") for block in content
                                if isinstance(block, dict) and block.get("type") == "text")
            return {"kind": "turn_completed", "summary": summary[:SUMMARY_LIMIT]}
    return None


def observe_events(run) -> list[dict]:
    """Consume at most 1 MiB, mutating run['lifecycle_cursor'] for caller to persist.

    Cursor and resulting outbox/inbox events should be saved in one transaction.
    Events have deterministic IDs so retries may safely deduplicate. Unknown
    records and malformed records are ignored. Huge individual records are
    skipped in bounded chunks. No screen scraping or tool output is inspected.
    Lack of events never implies completion.
    """
    if run.get("agent") == "hermes":
        return _hermes_events(run)
    path = _transcript(run)
    if path is None or not path.is_file():
        return []
    cursor = run.get("lifecycle_cursor") or {}
    events = []
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        identity = f"{path}:{stat.st_dev}:{stat.st_ino}"
        if cursor.get("identity") != identity or cursor.get("offset", 0) > stat.st_size:
            cursor = {"identity": identity, "offset": 0}
        offset = cursor["offset"]
        stream.seek(offset)
        data = stream.read(READ_LIMIT)
        consumed = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                # A short final line is still being written: retry it next poll.
                # A full-size chunk with no newline is an oversized native record.
                if consumed == 0 and len(data) == READ_LIMIT:
                    consumed = len(data)
                    cursor["dropping"] = True
                break
            consumed += len(line)
            if cursor.pop("dropping", False):
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    continue
                # Claude embeds its native ID in every conversational record.
                if record.get("sessionId", run["native_session_id"]) != run["native_session_id"]:
                    continue
                event = _native_event(run["agent"], record)
            except (ValueError, TypeError):
                continue
            if event:
                event["id"] = hashlib.sha256(
                    f"{run['native_session_id']}:{identity}:{offset + consumed}".encode()
                ).hexdigest()
                events.append(event)
        cursor["offset"] = offset + consumed
    run["lifecycle_cursor"] = cursor
    return events


def _hermes_events(run):
    """Read durable text-turn finals and follow explicit compression lineage."""
    home = Path(run.get("config", {}).get("hermes_home") or os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    path = home / "state.db"
    session = run.get("native_session_id")
    if not session or not path.is_file():
        return []
    cursor = run.get("lifecycle_cursor") or {}
    last_id = cursor.get("message_id", 0) if cursor.get("session_id", session) == session else 0
    origin = cursor.get("origin_session_id", session)
    seen = cursor.get("seen", [])
    events = []
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1) as db:
        # Read only completed text replies, never historical tool results. SQL
        # substring caps each UTF-8 payload at 64 KiB; 16 rows fit the poll budget.
        rows = db.execute(
            "SELECT id,substr(content,1,?),timestamp FROM messages WHERE session_id=? AND id>? "
            "AND role='assistant' AND finish_reason='stop' "
            "AND (tool_calls IS NULL OR tool_calls='' OR tool_calls='[]') "
            "AND COALESCE(_compressed_summary,0)=0 ORDER BY id LIMIT 16",
            (SUMMARY_LIMIT, session, last_id),
        ).fetchall()
        for message_id, content, timestamp in rows:
            # Hermes clones protected-tail rows with the same content/timestamp
            # during compaction. Match its display-generation identity, not row ID.
            fingerprint = hashlib.sha256(json.dumps([origin, content, timestamp]).encode()).hexdigest()
            if fingerprint not in seen:
                events.append({"kind": "turn_completed", "summary": content or "",
                               "id": f"hermes:{fingerprint}"})
                seen.append(fingerprint)
            last_id = message_id
        # Drain parent replies before advancing into the continuation.
        if len(rows) < 16:
            children = db.execute(
                "SELECT child.id FROM sessions parent JOIN sessions child "
                "ON child.parent_session_id=parent.id WHERE parent.id=? "
                "AND parent.end_reason='compression' "
                "AND json_extract(COALESCE(child.model_config,'{}'),'$._branched_from') IS NULL "
                "AND json_extract(COALESCE(child.model_config,'{}'),'$._delegate_from') IS NULL "
                "AND COALESCE(child.source,'') != 'tool' "
                "AND (child.ended_at IS NULL OR child.end_reason='compression') LIMIT 2",
                (session,),
            ).fetchall()
            if len(children) == 1:
                successor = children[0][0]
                run["native_session_id"] = successor
                events.append({"kind": "compacted", "summary": "Hermes context compacted; following its native continuation.",
                               "id": f"hermes:compact:{session}:{successor}"})
                last_id = 0
    run["lifecycle_cursor"] = {"message_id": last_id, "session_id": run["native_session_id"],
                               "origin_session_id": origin, "seen": seen[-128:]}
    return events
