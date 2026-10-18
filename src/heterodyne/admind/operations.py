"""approval.operation.v1: durable, same-UID source correlation, never consent authority.

No native approval is edited here. A tombstone prevents new local decisions/cards; it does not
prove that publication or a native approval did not happen. Records have no automatic retention.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from typing import TYPE_CHECKING, Literal, NamedTuple

import msgspec

from heterodyne.admind import approvals, asks, fulfillment

if TYPE_CHECKING:
    from heterodyne.admind.ctl import Peer
    from heterodyne.admind.daemon import Admind
    from heterodyne.admind.store import AskRow, Store

MAX_REQUEST, MAX_REPLY = 16_384, 1_048_576
MAX_OPERATIONS, MAX_LINEAGE, MAX_PARTS = 4096, 256, 4096
BEAD = re.compile(r"[a-z][a-z0-9-]{0,31}-[0-9a-z]{5}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_operations (
    operation_key TEXT PRIMARY KEY, source TEXT NOT NULL, journal TEXT NOT NULL,
    operation TEXT NOT NULL, bead TEXT UNIQUE, context_digest TEXT, intent_hash TEXT NOT NULL,
    uid INTEGER NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
    receipt TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approval_operation_asks (
    ask_id TEXT PRIMARY KEY, operation_key TEXT NOT NULL,
    FOREIGN KEY(operation_key) REFERENCES approval_operations(operation_key));
CREATE INDEX IF NOT EXISTS approval_operation_lineage ON approval_operation_asks(operation_key);
CREATE TABLE IF NOT EXISTS approval_operation_events (
 seq INTEGER PRIMARY KEY, operation_key TEXT NOT NULL, action TEXT NOT NULL,
 uid INTEGER NOT NULL, peer_pid INTEGER, created_at TEXT NOT NULL,
 before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
 FOREIGN KEY(operation_key) REFERENCES approval_operations(operation_key));
CREATE TRIGGER IF NOT EXISTS approval_operation_revision AFTER UPDATE ON asks
WHEN EXISTS (SELECT 1 FROM approval_operations WHERE bead=NEW.bead)
BEGIN
 UPDATE approval_operations SET revision=revision+1 WHERE bead=NEW.bead;
END;
CREATE TRIGGER IF NOT EXISTS approval_operation_insert AFTER INSERT ON asks
BEGIN
 UPDATE approval_operations SET revision=revision+1 WHERE bead=NEW.bead;
END;
"""


class Invalid(ValueError):
    """Fixed protocol code, without request text."""


class Request(msgspec.Struct, frozen=True, tag="approval.operation.v1", forbid_unknown_fields=True):
    mode: Literal["post", "status", "withdraw"]
    source: str
    journal: str
    operation: str
    bead: str | None
    context_digest: str | None
    intent_hash: str


class Row(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    ask_id: str
    status: str
    digest: str | None
    refreshed_from: str | None
    attempt: str | None
    answers: list[str]
    decision_refs: list[str]
    outbox: list[str]


class Reply(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: str
    code: str
    receipt: str | None = None
    revision: int | None = None
    withdrawn: bool = False
    lineage: list[Row] = []


def check(req: Request) -> str | None:
    try:
        # Typed local objects are not trusted merely because the socket decoder is strict.
        checked = msgspec.convert(msgspec.to_builtins(req), type=Request, strict=True)
        if checked != req or len(msgspec.json.encode(req)) > MAX_REQUEST:
            return "malformed"
        if any(fulfillment.HEX.fullmatch(x) is None for x in (req.source, req.journal, req.intent_hash)):
            return "identity"
        if str(uuid.UUID(req.operation)) != req.operation:
            return "identity"
        if (req.bead is None) != (req.context_digest is None):
            return "binding"
        if req.bead is not None and (BEAD.fullmatch(req.bead) is None
                                    or fulfillment.HEX.fullmatch(req.context_digest or "") is None):
            return "binding"
        if req.mode == "post" and req.bead is None:
            return "bead-required"
    except (ValueError, TypeError):
        return "malformed"
    return None


def decode(data: bytes) -> Request:
    try:
        fulfillment.strict_json(data, limit=MAX_REQUEST)
        req = msgspec.json.decode(data, type=Request)
        why = check(req)
        if why:
            raise Invalid(why)
        return req
    except (ValueError, msgspec.DecodeError, RecursionError):
        raise Invalid("malformed") from None


def key(req: Request) -> str:
    return hashlib.sha256(msgspec.json.encode((req.source, req.journal, req.operation))).hexdigest()


class Bound(NamedTuple):
    bead: str | None
    context_digest: str | None
    intent_hash: str
    uid: int
    withdrawn: bool
    revision: int
    receipt: str


def existing(store: Store, req: Request, uid: int) -> Bound | None:
    row = store.db.execute("SELECT bead, context_digest, intent_hash, uid, withdrawn, revision, receipt "
                           "FROM approval_operations WHERE operation_key=?", (key(req),)).fetchone()
    if row is not None and tuple(row[:4]) != (req.bead, req.context_digest, req.intent_hash, uid):
        raise Invalid("withdrawn-payload-conflict" if row[4] else "payload-conflict")
    if row is None:
        return None
    return Bound(row[0], row[1], row[2], row[3], bool(row[4]), row[5], row[6])


def bead_binding(store: Store, bead: str | None) -> tuple[str, bool] | None:
    if bead is None:
        return None
    row = store.db.execute("SELECT operation_key, withdrawn FROM approval_operations WHERE bead=?",
                           (bead,)).fetchone()
    return None if row is None else (str(row[0]), bool(row[1]))


def decision_allowed(store: Store, ask_id: str) -> bool:
    row = store.db.execute("SELECT o.withdrawn FROM approval_operations o JOIN asks a ON a.bead=o.bead "
                           "WHERE a.ask_id=?", (ask_id,)).fetchone()
    return row is None or not row[0]


def lineage(store: Store, bead: str | None) -> list[AskRow]:
    if bead is None:
        return []
    ids = store.db.execute("SELECT ask_id FROM asks WHERE bead=? ORDER BY rowid LIMIT ?",
                           (bead, MAX_LINEAGE + 1)).fetchall()
    if len(ids) > MAX_LINEAGE:
        raise Invalid("lineage-capacity")
    rows: list[AskRow] = []
    seen: set[str] = set()
    for item in ids:
        row = store.ask(str(item[0]))
        if row is None or row.kind != "approval" or asks.normalise_id(row.ask_id) != row.ask_id:
            raise Invalid("lineage-incomplete")
        if row.digest is None or fulfillment.HEX.fullmatch(row.digest) is None:
            raise Invalid("lineage-incomplete")
        if row.refreshed_from is not None and row.refreshed_from not in seen:
            raise Invalid("lineage-incomplete")
        rows.append(row)
        seen.add(row.ask_id)
    return rows


def enroll(store: Store, req: Request, uid: int) -> None:
    if existing(store, req, uid) is not None:
        return
    if store.db.execute("SELECT COUNT(*) FROM approval_operations").fetchone()[0] >= MAX_OPERATIONS:
        raise Invalid("operation-capacity")
    if bead_binding(store, req.bead) is not None:
        raise Invalid("bead-key-conflict")
    rows = lineage(store, req.bead)
    if rows and not any(row.digest == req.context_digest for row in rows):
        raise Invalid("lineage-context-conflict")
    receipt = hashlib.sha256(msgspec.json.encode(("approval.operation.v1", key(req), req.bead,
                                                req.context_digest, req.intent_hash, uid))).hexdigest()
    store.db.execute("INSERT INTO approval_operations(operation_key, source, journal, operation, bead, "
                     "context_digest, intent_hash, uid, receipt) VALUES(?,?,?,?,?,?,?,?,?)",
                     (key(req), req.source, req.journal, req.operation, req.bead,
                      req.context_digest, req.intent_hash, uid, receipt))
    for row in rows:
        attach(store, key(req), row.ask_id)


def attach(store: Store, operation_key: str, ask_id: str) -> None:
    old = store.db.execute("SELECT operation_key FROM approval_operation_asks WHERE ask_id=?",
                          (ask_id,)).fetchone()
    if old is not None and old[0] != operation_key:
        raise Invalid("lineage-key-conflict")
    store.db.execute("INSERT OR IGNORE INTO approval_operation_asks VALUES(?,?)", (ask_id, operation_key))


def before_commit(store: Store, bead: str | None, operation_key: str | None) -> str | None:
    binding = bead_binding(store, bead)
    if binding is None:
        if operation_key is not None:
            raise Invalid("missing-operation")
        return None
    if binding[1]:
        raise Invalid("withdrawn")
    if operation_key != binding[0]:
        raise Invalid("keyed-bead")
    if len(lineage(store, bead)) >= MAX_LINEAGE:
        raise Invalid("lineage-capacity")
    return binding[0]


def view(store: Store, req: Request, uid: int, result: str = "ok", code: str = "status") -> Reply:
    bound = existing(store, req, uid)
    if bound is None:
        return Reply("not-found", "not-found")
    rows = lineage(store, req.bead)
    views: list[Row] = []
    total = 0
    for row in rows:
        linked = store.db.execute("SELECT operation_key FROM approval_operation_asks WHERE ask_id=?",
                                  (row.ask_id,)).fetchone()
        if linked is None or linked[0] != key(req):
            raise Invalid("lineage-incomplete")
        answers = store.db.execute("SELECT message_id FROM ask_answers WHERE ask_id=? ORDER BY seq LIMIT ?",
                                  (row.ask_id, MAX_PARTS + 1)).fetchall()
        attempts = store.db.execute("SELECT ref FROM ask_attempts WHERE ask_id=? ORDER BY rowid LIMIT ?",
                                   (row.ask_id, MAX_PARTS + 1)).fetchall()
        parts = store.db.execute("SELECT key FROM outbox WHERE key GLOB ? OR key GLOB ? OR key GLOB ? "
                                "ORDER BY seq LIMIT ?",
                                (f"ask:{row.ask_id}:*", f"asknote:{row.ask_id}:*",
                                 f"askd:{row.ask_id}:*", MAX_PARTS + 1)).fetchall()
        total += len(parts) + len(answers) + len(attempts)
        if total > MAX_PARTS:
            raise Invalid("lineage-parts-capacity")
        attempt = store.current_attempt(row.ask_id)
        views.append(Row(row.ask_id, row.status, row.digest, row.refreshed_from,
                         None if attempt is None else attempt.message_id,
                         [str(a[0]) for a in answers], [str(a[0]) for a in attempts],
                         [str(a[0]) for a in parts]))
    if bound[4] and result == "ok":
        result = ("decision-in-flight" if any(r.status in {"deciding", "uncertain"} or r.attempt is not None
                                             for r in views) else "withdrawn")
    reply = Reply(result, code, bound.receipt, bound.revision, bound.withdrawn, views)
    if len(msgspec.json.encode(reply)) > MAX_REPLY:
        raise Invalid("reply-capacity")
    return reply


def record_transition(store: Store, req: Request, uid: int, pid: int | None,
                      before: Reply, after: Reply) -> None:
    """Same transaction as the effect; no request text or operator consent is inferred."""
    store.db.execute("INSERT INTO approval_operation_events(operation_key,action,uid,peer_pid,created_at,"
                     "before_hash,after_hash) VALUES(?,?,?,?,?,?,?)",
                     (key(req), req.mode, uid, pid, asks.stamp(asks.now()),
                      hashlib.sha256(msgspec.json.encode(before)).hexdigest(),
                      hashlib.sha256(msgspec.json.encode(after)).hexdigest()))


async def handle(daemon: Admind, req: Request, peer: Peer) -> Reply:
    uid = peer.uid
    if uid is None or uid != os.getuid():
        return Reply("refused", "trusted-uid-required")
    why = check(req)
    if why:
        return Reply("refused", why)
    if req.mode == "post" and daemon.posts_in_flight >= asks.MAX_IN_FLIGHT:
        return Reply("refused", "posts-busy")
    if req.mode == "post":
        daemon.posts_in_flight += 1
    try:
        if req.mode == "status":
            with daemon.store.transaction(read_only=True):
                return view(daemon.store, req, uid)
        async with daemon.ask_post_lock:
            if req.mode == "withdraw":
                return withdraw(daemon, req, uid, peer.pid)
            with daemon.store.transaction(read_only=True):
                bound = existing(daemon.store, req, uid)
                if bound is not None:
                    return view(daemon.store, req, uid)
            return await post(daemon, req, peer)
    except Invalid as exc:
        return Reply("refused", str(exc))
    except sqlite3.Error:
        return Reply("unavailable", "store-unavailable")
    finally:
        if req.mode == "post":
            daemon.posts_in_flight -= 1


async def post(daemon: Admind, req: Request, peer: Peer) -> Reply:
    if req.bead is None or daemon.approve_bead is None:
        return Reply("refused", "approvals-unavailable")
    refusal = daemon.approval_post_refusal(req.bead)
    if refusal:
        return Reply("refused", "post-gate")
    try:
        readout = await daemon.approve_bead.read(req.bead)
    except approvals.BtqError:
        return Reply("unavailable", "readout-unavailable")
    if isinstance(readout, approvals.Busy):
        return Reply("unavailable", "readout-busy")
    if readout.id != req.bead or readout.digest != req.context_digest:
        return Reply("refused", "context-conflict")
    # Existing history is enrolled completely or not at all. Never manufacture a replacement card
    # to make adoption succeed. A caller must reconcile its own original operation association.
    with daemon.store.transaction():
        rows = lineage(daemon.store, req.bead)
        if rows:
            if rows[-1].digest != req.context_digest:
                raise Invalid("lineage-context-conflict")
            enroll(daemon.store, req, peer.uid if peer.uid is not None else -1)
            result = view(daemon.store, req, peer.uid if peer.uid is not None else -1)
            record_transition(daemon.store, req, peer.uid if peer.uid is not None else -1, peer.pid,
                              Reply("not-found", "not-found"), result)
            return result
        fresh = daemon.prepare_approval("wsd", req.bead, readout)
        # Local import avoids the runtime daemon/module cycle.
        from heterodyne.admind.daemon import FreshCard
        if not isinstance(fresh, FreshCard):
            return Reply("refused", "post-gate")
        enroll(daemon.store, req, peer.uid if peer.uid is not None else -1)
        daemon.commit_approval(fresh, peer.pid, operation_key=key(req))
        reply = view(daemon.store, req, peer.uid if peer.uid is not None else -1, "posted", "posted")
        record_transition(daemon.store, req, peer.uid if peer.uid is not None else -1, peer.pid,
                          Reply("not-found", "not-found"), reply)
    return reply


def withdraw(daemon: Admind, req: Request, uid: int, pid: int | None = None) -> Reply:
    with daemon.store.transaction():
        before = view(daemon.store, req, uid)
        if before.withdrawn and not any(row.status in {"open", "answered"} for row in before.lineage):
            return before
        enroll(daemon.store, req, uid)
        rows = lineage(daemon.store, req.bead)
        daemon.store.db.execute("UPDATE approval_operations SET withdrawn=1, revision=revision+1 "
                                "WHERE operation_key=? AND withdrawn=0", (key(req),))
        for row in rows:
            if (row.status not in {"open", "answered"}
                    or daemon.store.current_attempt(row.ask_id) is not None):
                continue
            if daemon.store.fulfillment.protected(row.ask_id):
                raise Invalid("fulfillment-protected")
            daemon.store.set_ask(row.ask_id, "cancelled")
            daemon.ask_notice(row.ask_id, "cancelled", key(req),
                              f"Ask {row.ask_id} was withdrawn by its source. "
                              "Original decisions are retained.")
        after = view(daemon.store, req, uid)
        record_transition(daemon.store, req, uid, pid, before, after)
        return after
