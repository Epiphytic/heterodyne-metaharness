"""Transactional provenance upgrade and constant-size membership observations.

No method in this module sends, grants an operator key, or clears a recovery latch.
"""

import json
import re
import sqlite3
from typing import Any

REVISION = "1"
_STATUSES = "'pending', 'sent', 'failed', 'quarantined', 'suppressed', 'delayed'"
_CARD = re.compile(r"ask:([2-9a-hjkmnp-z]{4}):([0-9]+)\Z")
_NOTICE = re.compile(r"notice:([2-9a-hjkmnp-z]{6}):([0-9]+)\Z")
_GROUP = re.compile(
    r"((?:ask|notice):[2-9a-hjkmnp-z]+|ask(?:d|r):[2-9a-hjkmnp-z]{4}:[0-9a-f]{64}|"
    r"asknote:[2-9a-hjkmnp-z]{4}:[a-z-]+:[^:]+|reply:[^:]+:[0-9]+|"
    r"[a-z]+:(?:r:)?[0-9a-f]{64}):(?:[rs])?[0-9]+\Z")


def legacy_group(key: str, thread: str | None) -> str:
    """Whole grammar, never a prefix coincidence. Thread links are joined below as well."""
    value = thread or key
    match = _GROUP.fullmatch(value)
    return match.group(1) if match else value


def proven_system(db: sqlite3.Connection, key: str) -> bool:
    """Only a complete initial-card/notice key plus its durable matching part count proves system origin.

    Legacy automatic reminders lack a per-sequence durable link and remain quarantined; manual repeats
    and bumps are requests even when they quote a system ask.
    """
    for pattern, table, identity, count in ((_CARD, "asks", "ask_id", "card_parts"),
                                           (_NOTICE, "notices", "notice_id", "parts")):
        match = pattern.fullmatch(key)
        if match:
            row = db.execute(f"SELECT {count} FROM {table} WHERE {identity} = ?",  # noqa: S608
                             (match.group(1),)).fetchone()
            return row is not None and 0 <= int(match.group(2)) < int(row[0])
    return False


def migrate(db: sqlite3.Connection) -> None:
    """Caller holds Store transaction/RLock. Each DDL is transactional; never executescript here."""
    if "sender_key" not in {r[1] for r in db.execute("PRAGMA table_info(inbound)")}:
        db.execute("ALTER TABLE inbound ADD COLUMN sender_key TEXT")
    columns = {r[1] for r in db.execute("PRAGMA table_info(outbox)")}
    if "ownership_kind" in columns:
        required = {"ownership_kind", "delivery_group", "not_before", "hold_reason"}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if not required <= columns or not {"outbox_owners", "guard_migration"} <= tables:
            raise ValueError("incomplete guard schema")
        if db.execute("SELECT value FROM kv WHERE key = 'guard_schema_revision'").fetchone() != (REVISION,):
            raise ValueError("incomplete guard schema revision")
        return
    if db.execute("PRAGMA foreign_keys").fetchone()[0]:
        # Disabling enforcement inside a transaction is a silent no-op; reject before destructive DDL.
        # Store opens its private connection with SQLite's normal enforcement-off migration setting.
        raise ValueError("guard migration requires foreign-key enforcement disabled before its transaction")
    original = db.execute("SELECT seq, key, reply_to, lane, text, status, attempts, message_id, thread_key "
                          "FROM outbox ORDER BY seq").fetchall()
    high = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'outbox'").fetchone()
    objects = db.execute("SELECT sql FROM sqlite_master WHERE tbl_name = 'outbox' "
                         "AND type IN ('index', 'trigger') AND sql IS NOT NULL").fetchall()
    before_fk = db.execute("PRAGMA foreign_key_check").fetchall()
    db.execute(f"CREATE TABLE guard_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, "  # noqa: S608
               "key TEXT NOT NULL UNIQUE, reply_to TEXT, lane INTEGER NOT NULL DEFAULT 1 "
               "CHECK(lane IN (1,2)), text TEXT NOT NULL, status TEXT NOT NULL "
               f"CHECK(status IN ({_STATUSES})), attempts INTEGER NOT NULL DEFAULT 0, message_id TEXT, "
               "thread_key TEXT, ownership_kind TEXT NOT NULL CHECK(ownership_kind IN "
               "('system','request','unknown')), delivery_group TEXT NOT NULL, not_before REAL, "
               "hold_reason TEXT)")
    db.execute("CREATE TABLE outbox_owners (seq INTEGER NOT NULL, sender_key TEXT NOT NULL, "
               "PRIMARY KEY(seq, sender_key))")
    db.execute("CREATE TABLE guard_migration (seq INTEGER PRIMARY KEY, original_status TEXT NOT NULL, "
               "revision TEXT NOT NULL, reason TEXT NOT NULL, disposition TEXT NOT NULL)")
    groups = {str(r[1]): legacy_group(str(r[1]), r[8]) for r in original}
    # Follow persisted thread roots, bounded by the finite row count; cycles remain unknown/held.
    for _ in range(len(groups)):
        changed = False
        for row in original:
            if row[8] in groups and groups[str(row[1])] != groups[row[8]]:
                groups[str(row[1])] = groups[row[8]]
                changed = True
        if not changed:
            break
    unknown = {groups[str(r[1])] for r in original if not proven_system(db, str(r[1]))}
    for row in original:
        group = groups[str(row[1])]
        system = proven_system(db, str(row[1]))
        status = "quarantined" if row[5] == "pending" and group in unknown else row[5]
        reason = "legacy-unproven-owner" if group in unknown else "legacy-proven-system"
        db.execute("INSERT INTO guard_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                   (*row[:5], status, *row[6:], "system" if system else "unknown", group,
                    reason if status == "quarantined" else None))
        db.execute("INSERT INTO guard_migration VALUES (?, ?, ?, ?, ?)",
                   (row[0], row[5], REVISION, reason, status))
    # Foreign-key references continue naming outbox. Renaming the old table would rewrite them.
    db.execute("DROP TABLE outbox")
    db.execute("ALTER TABLE guard_outbox RENAME TO outbox")
    for (sql,) in objects:
        db.execute(str(sql))
    if high:
        db.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'outbox'", (high[0],))
        if not db.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'outbox'").fetchone():
            db.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('outbox', ?)", (high[0],))
    if db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] != len(original):
        raise ValueError("guard migration row mismatch")
    if db.execute("PRAGMA foreign_key_check").fetchall() != before_fk:
        raise ValueError("guard migration foreign-key mismatch")
    db.execute("INSERT INTO kv(key,value) VALUES ('guard_schema_revision', ?)", (REVISION,))


def notice_state(raw: str | None) -> dict[str, Any]:
    """One bounded accumulator and at most one unresolved delivery group."""
    if raw:
        return json.loads(raw)
    return {"first": None, "last": None, "kinds": {}, "pending": False, "sequence": 0,
            "unresolved": None, "last_sent": None, "version": 0}


def observe(state: dict[str, Any], *, count: int | None = None, kind: str | None = None) -> None:
    if count is not None:
        if state["first"] is None:
            state["first"] = count
        if state["last"] is not None and count != state["last"]:
            state["pending"] = True
        state["last"] = count
    if kind:
        state["kinds"][kind] = min(65535, state["kinds"].get(kind, 0) + 1)
        state["pending"] = True
    state["version"] = (state["version"] + 1) % (2 ** 63)
