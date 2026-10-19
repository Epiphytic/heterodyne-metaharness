"""Fulfillment v1: bounded metadata attestation, never external operation execution.

Disabled unless the daemon's explicit capability switch is enabled. Same-UID callers are trusted
administrators; the receipt is their attestation, not independently verified external success.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import msgspec

from heterodyne.admind.redact import redact

if TYPE_CHECKING:
    from heterodyne.admind.store import Store

SOCKET = "ask.sock"
ENABLED = False
MAX_REQUEST, MAX_REPLY, MAX_FILE = 65_536, 131_072, 24_576
TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ANSWER_ID = re.compile(r"(?:r:)?[0-9a-f]{64}\Z")


class Invalid(ValueError):
    """Fixed public refusal code, never includes request material."""


class Evidence(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    artifact_id: str
    sha256: str


class Binding(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    ask_store_id: str
    ask_id: str
    created_at: str
    ask_sha256: str
    answers_sha256: str
    consumed_answers: list[str]
    requested_operation_sha256: str
    original_authority_sha256: str


class Completion(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["operation-completion-v1"]
    binding: Binding
    namespace: str
    operation_id: str
    state: Literal["completed"]
    terminal_result_sha256: str
    evidence: list[Evidence]


class Declaration(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    schema: Literal["operator-confirmation-v1"]
    binding: Binding
    description: str
    evidence: list[Evidence]


class Apply(msgspec.Struct, frozen=True, tag="fulfillment.apply", forbid_unknown_fields=True):
    receipt: Completion


class Propose(msgspec.Struct, frozen=True, tag="fulfillment.propose", forbid_unknown_fields=True):
    ask_id: str
    declaration: Declaration
    replace_intent: str | None = None


class Dispose(msgspec.Struct, frozen=True, tag="fulfillment.dispose", forbid_unknown_fields=True):
    ask_id: str
    expect_intent: str
    reason: Literal["operator-withdrawal", "stale-answers", "expired"]


class Get(msgspec.Struct, frozen=True, tag="fulfillment.get", forbid_unknown_fields=True):
    ask_id: str


class Inspect(msgspec.Struct, frozen=True, tag="fulfillment.inspect", forbid_unknown_fields=True):
    pass


Request = Apply | Propose | Dispose | Get | Inspect


class Reply(msgspec.Struct, frozen=True):
    result: str
    message: str
    view: dict[str, object] | None = None


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(domain: str, value: object) -> str:
    return hashlib.sha256(("admind-fulfillment-v1:" + domain + "\n").encode() + canonical(value)).hexdigest()


def builtins(value: object) -> object:
    return msgspec.to_builtins(value)


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    obj: dict[str, object] = {}
    for key, value in pairs:
        if key in obj:
            raise Invalid("duplicate-key")
        obj[key] = value
    return obj


def parse[T](data: bytes, kind: type[T]) -> T:
    strict_json(data)
    try:
        return msgspec.json.decode(data, type=kind)
    except msgspec.DecodeError:
        raise Invalid("malformed") from None


def strict_json(data: bytes, limit: int = MAX_REQUEST) -> None:
    if len(data.rstrip(b"\n")) > limit:
        raise Invalid("oversized")
    try:
        json.loads(
            data,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Invalid("nonfinite")),
        )
    except (ValueError, RecursionError):
        raise Invalid("malformed") from None


def read_file(path: Path, *, manifest: bool = False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise Invalid("unsafe-file")
        limit = 262_144 if manifest else MAX_FILE
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise Invalid("oversized")
        return data
    finally:
        os.close(fd)


def check(req: Request) -> str | None:
    try:
        encoded = msgspec.json.encode(req)
        if len(encoded) > MAX_REQUEST:
            raise Invalid("oversized")
        msgspec.json.decode(encoded, type=Request)
        if isinstance(req, Apply):
            obj = req.receipt
            if not TOKEN.fullmatch(obj.namespace) or len(obj.namespace) > 64:
                raise Invalid("namespace")
            if not TOKEN.fullmatch(obj.operation_id) or not HEX.fullmatch(obj.terminal_result_sha256):
                raise Invalid("operation")
            validate_binding(obj.binding, obj.evidence)
        elif isinstance(req, Propose):
            validate_binding(req.declaration.binding, req.declaration.evidence)
            if req.ask_id != req.declaration.binding.ask_id:
                raise Invalid("ask-mismatch")
            text = req.declaration.description
            if not text.strip() or len(text) > 512 or redact(text) != text:
                raise Invalid("declaration")
            if req.replace_intent is not None and not HEX.fullmatch(req.replace_intent):
                raise Invalid("intent")
        elif isinstance(req, Dispose) and not HEX.fullmatch(req.expect_intent):
            raise Invalid("intent")
        if isinstance(req, Get | Dispose):
            check_id(req.ask_id)
    except Invalid as exc:
        return str(exc)
    except (msgspec.DecodeError, TypeError, ValueError, RecursionError):
        return "malformed"
    return None


def check_id(ask_id: str) -> None:
    from heterodyne.admind.asks import ID_ALPHABET, ID_LENGTH

    # Never normalize an attested identity.
    if len(ask_id) != ID_LENGTH or any(c not in ID_ALPHABET for c in ask_id):
        raise Invalid("identity")


def validate_binding(binding: Binding, evidence: list[Evidence]) -> None:
    if len(evidence) > 8 or not 1 <= len(binding.consumed_answers) <= 50:
        raise Invalid("bounds")
    if len(set(binding.consumed_answers)) != len(binding.consumed_answers):
        raise Invalid("answers")
    for value in (
        binding.ask_store_id,
        binding.ask_sha256,
        binding.answers_sha256,
        binding.requested_operation_sha256,
        binding.original_authority_sha256,
    ):
        if not HEX.fullmatch(value):
            raise Invalid("digest")
    check_id(binding.ask_id)
    if not binding.created_at or len(binding.created_at) > 64:
        raise Invalid("identity")
    for value in binding.consumed_answers:
        if not ANSWER_ID.fullmatch(value):
            raise Invalid("answer-reference")
    for item in evidence:
        if not TOKEN.fullmatch(item.artifact_id) or not HEX.fullmatch(item.sha256):
            raise Invalid("evidence")


DDL = (
    "CREATE TABLE IF NOT EXISTS ask_fulfillments (ask_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, "
    "receipt_id TEXT NOT NULL UNIQUE, receipt TEXT NOT NULL, namespace TEXT, operation_id TEXT, "
    "core_digest TEXT, valid INTEGER NOT NULL CHECK(valid IN (0,1)))",
    "CREATE INDEX IF NOT EXISTS fulfillment_operation ON ask_fulfillments(namespace,operation_id)",
    "CREATE TABLE IF NOT EXISTS ask_fulfillment_intents (intent_id TEXT PRIMARY KEY, ask_id TEXT NOT NULL, "
    "digest TEXT NOT NULL UNIQUE, declaration TEXT NOT NULL, notice TEXT NOT NULL, parts INTEGER NOT NULL, "
    "created_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','retired','consumed')), "
    "reason TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS fulfillment_current_intent ON ask_fulfillment_intents(ask_id) "
    "WHERE status='pending'",
    "CREATE TABLE IF NOT EXISTS ask_fulfillment_audit (seq INTEGER PRIMARY KEY, ask_id TEXT NOT NULL, "
    "request_digest TEXT NOT NULL, disposition TEXT NOT NULL, at TEXT NOT NULL, "
    "UNIQUE(ask_id,request_digest,disposition))",
)


def validate_schema_db(db: sqlite3.Connection) -> None:
    revision = db.execute("SELECT value FROM kv WHERE key='ask_fulfillment_schema'").fetchone()
    identity = db.execute("SELECT value FROM kv WHERE key='ask_store_id'").fetchone()
    if revision != ("1",) or identity is None or not HEX.fullmatch(str(identity[0])):
        raise Invalid("unsupported-schema")
    for statement in DDL:
        expected = statement.replace(" IF NOT EXISTS", "")
        name = expected.split()[2] if not expected.startswith("CREATE UNIQUE") else expected.split()[3]
        if db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone() != (expected,):
            raise Invalid("incompatible-schema")


class Lifecycle:
    def __init__(self, store: Store):
        self.store = store
        self.verification_changed: Callable[[], None] = lambda: None
        self.notice_authority: Callable[[], set[str] | None] = lambda: None
        self.post: Callable[[str, str, str | None, str | None, tuple[str, ...] | None], None] = self._post

    def _post(
        self, key: str, text: str, target: str | None, thread: str | None, owners: tuple[str, ...] | None
    ) -> None:
        """Offline Store adapter. The daemon installs its central redaction/wake pipeline."""
        self.store.enqueue(
            key,
            redact(text),
            target,
            thread_key=thread,
            ownership_kind="system" if owners is None else "request" if owners else "unknown",
            owners=owners or (),
        )

    def validate_schema(self) -> None:
        """Read-only compatibility check; never rehash history on a routine CLI open."""
        validate_schema_db(self.store.db)

    def migrate(self) -> None:
        with self.store.lock:
            if self.store.get("ask_fulfillment_schema") is not None:
                self.validate_schema()
                return
            with self.store.transaction():
                # Recheck after obtaining the writer lock: another process may have migrated first.
                if self.store.get("ask_fulfillment_schema") is None:
                    for statement in DDL:
                        self.store.db.execute(statement)
                    if self.store.get("ask_store_id") is None:
                        self.store.set("ask_store_id", secrets.token_hex(32))
                    self.store.set("ask_fulfillment_schema", "1")
                self.validate_schema()

    def rebuild(self) -> None:
        """Startup checks only. Hash work belongs to the independent bounded worker."""
        self.validate_schema()

    def capacity_predicate(self) -> str:
        from heterodyne.admind.fulfillment_projection import capacity_predicate

        return capacity_predicate(self.store.db)

    def require_projection(self) -> None:
        from heterodyne.admind.fulfillment_projection import validate

        validate(self.store.db)

    def binding(self, ask_id: str) -> dict[str, object]:
        with self.store.lock:
            row = self.store.ask(ask_id)
            if row is None:
                raise Invalid("missing")
            fields = (
                "kind",
                "poster",
                "title",
                "body",
                "pr_url",
                "head_sha",
                "bead",
                "digest",
                "question_form",
                "choices",
                "links",
                "created_at",
            )
            ask = {key: getattr(row, key) for key in fields}
            answers: list[dict[str, object]] = [
                dict(
                    seq=int(seq),
                    kind=kind,
                    operator=operator,
                    message_id=mid,
                    text=text,
                    at=at,
                    choice=choice,
                )
                for seq, kind, operator, mid, text, at, choice in self.store.db.execute(
                    "SELECT seq,kind,operator,message_id,text,at,choice FROM "
                    "ask_answers WHERE ask_id=? ORDER BY seq",
                    (ask_id,),
                )
            ]
            return {
                "ask_store_id": self.store.get("ask_store_id"),
                "ask_id": ask_id,
                "created_at": row.created_at,
                "ask_sha256": digest("ask", ask),
                "answers_sha256": digest("answers", answers),
                "answer_ids": [answer["message_id"] for answer in answers if answer["kind"] == "answer"],
            }

    def validate(self, binding: Binding, evidence: list[Evidence] | None = None) -> None:
        actual = self.binding(binding.ask_id)
        for key in ("ask_store_id", "created_at", "ask_sha256", "answers_sha256"):
            if actual[key] != getattr(binding, key):
                raise Invalid("stale")
        if not set(binding.consumed_answers) <= set(msgspec.convert(actual["answer_ids"], type=list[str])):
            raise Invalid("unretained-answer")
        row = self.store.ask(binding.ask_id)
        if row is None or row.kind not in ("question", "merge") or row.status != "answered":
            raise Invalid("unsupported")
        if row.kind == "merge" and evidence is not None and not evidence:
            raise Invalid("merge-evidence")

    def refresh(self, ask_id: str) -> None:
        record = self.stored(ask_id)
        if record is None:
            return
        try:
            self.validate(msgspec.convert(record["binding"], type=Binding, strict=True))
            valid = 1
        except (Invalid, msgspec.ValidationError):
            valid = 0
        self.store.db.execute("UPDATE ask_fulfillments SET valid=? WHERE ask_id=?", (valid, ask_id))

    def stored(self, ask_id: str) -> dict[str, object] | None:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT length(CAST(receipt AS BLOB)) FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
            ).fetchone()
            if row is None:
                return None
            if type(row[0]) is not int or row[0] > MAX_REPLY:
                raise Invalid("stored-receipt-unavailable")
            try:
                raw = self.store.db.execute(
                    "SELECT receipt FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
                ).fetchone()
                if raw is None:
                    raise Invalid("stored-receipt-unavailable")
                data = str(raw[0]).encode()
                strict_json(data)
                value = msgspec.json.decode(data, type=dict[str, object])
                if value.get("schema") != "fulfillment-receipt-v1" or not HEX.fullmatch(
                    str(value.get("request_digest"))
                ):
                    raise Invalid("stored-receipt-unavailable")
                return value
            except (msgspec.DecodeError, ValueError, TypeError, UnicodeError, sqlite3.Error):
                raise Invalid("stored-receipt-unavailable") from None

    def protected(self, ask_id: str) -> bool:
        with self.store.lock:
            return (
                self.store.db.execute("SELECT 1 FROM ask_fulfillments WHERE ask_id=?", (ask_id,)).fetchone()
                is not None
            )

    def view(self, ask_id: str) -> dict[str, object]:
        with self.store.transaction(read_only=True):
            from . import fulfillment_projection as projection

            receipt = self.stored(ask_id)
            original = self.store.db.execute(
                "SELECT kind,status FROM asks WHERE ask_id=?", (ask_id,)
            ).fetchone()
            projected = False
            state = None
            try:
                epoch, _ = projection.validate(self.store.db)
                projected = True
                stamp = self.store.db.execute(
                    "SELECT v.state,f.valid FROM fulfillment_verifications v "
                    "JOIN fulfillment_generations g ON g.ask_id=v.ask_id "
                    "JOIN ask_fulfillments f ON f.ask_id=v.ask_id "
                    "WHERE v.ask_id=? AND v.epoch=? AND v.generation=g.generation",
                    (ask_id, epoch),
                ).fetchone()
                state = None if stamp is None else stamp[0]
                if state == "valid" and stamp is not None and stamp[1] != 1:
                    state = None
            except (Invalid, sqlite3.Error):
                pass  # Invalid projection grants no lifecycle suppression or eligibility.
            binding = None
            try:
                if receipt is not None:
                    projection.measure(self.store.db, ask_id)
                    binding = projection.bounded_binding(self.store.db, ask_id)
                else:
                    binding = self.binding(ask_id)
            except (Invalid, sqlite3.Error, ValueError, TypeError, UnicodeError):
                if receipt is None:
                    raise
            lifecycle = "unfulfilled"
            if receipt is not None:
                lifecycle = "unverified"
                if projected and binding is not None:
                    if state == "valid":
                        lifecycle = "fulfilled"
                    elif state == "conflict":
                        lifecycle = "conflict"
            pending = self.store.db.execute(
                "SELECT intent_id,digest,status,created_at FROM "
                "ask_fulfillment_intents WHERE ask_id=? AND status='pending'",
                (ask_id,),
            ).fetchone()
            return {
                "schema": "fulfillment-view-v1",
                "binding": binding,
                "receipt": receipt,
                "lifecycle": lifecycle,
                "eligible": projected
                and receipt is None
                and original is not None
                and original[0] in ("question", "merge")
                and original[1] == "answered"
                and binding is not None
                and bool(binding["answer_ids"]),
                "pending": None
                if pending is None
                else {
                    **dict(zip(("intent_id", "digest", "status", "created_at"), pending, strict=True)),
                },
            }

    def apply(self, receipt: Completion, *, uid: int, pid: int | None) -> Reply:
        why = check(Apply(receipt))
        if why:
            raise Invalid(why)
        self.require_uid(uid)
        request_digest = digest("application", builtins(receipt))
        with self.store.transaction():
            existing = self.duplicate(receipt.binding.ask_id, request_digest)
            if existing:
                return existing
            self.validate(receipt.binding, receipt.evidence)
            core = digest(
                "operation",
                {
                    "namespace": receipt.namespace,
                    "operation_id": receipt.operation_id,
                    "requested_operation_sha256": receipt.binding.requested_operation_sha256,
                    "original_authority_sha256": receipt.binding.original_authority_sha256,
                    "terminal_result_sha256": receipt.terminal_result_sha256,
                },
            )
            if self.store.db.execute(
                "SELECT 1 FROM ask_fulfillments WHERE namespace=? AND operation_id=? AND core_digest<>?",
                (receipt.namespace, receipt.operation_id, core),
            ).fetchone():
                raise Invalid("operation-conflict")
            return self.commit(
                receipt.binding,
                request_digest,
                "controller",
                {"uid": uid, "pid": pid},
                {
                    "controller_receipt_sha256": digest("controller-receipt", builtins(receipt)),
                    "evidence": builtins(receipt.evidence),
                    "namespace": receipt.namespace,
                    "operation_id": receipt.operation_id,
                    "terminal_result_sha256": receipt.terminal_result_sha256,
                },
                receipt.namespace,
                receipt.operation_id,
                core,
            )

    @staticmethod
    def require_uid(uid: int | None) -> None:
        if uid is None or uid != os.getuid():
            raise Invalid("unverified-peer")

    def duplicate(self, ask_id: str, request_digest: str) -> Reply | None:
        row = self.store.db.execute(
            "SELECT request_digest,valid FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_digest:
            raise Invalid("conflict")
        return Reply("already-applied", "fulfilled; original answer retained", self.stored(ask_id))

    def commit(
        self,
        binding: Binding,
        request_digest: str,
        route: str,
        authority: dict[str, object],
        attestation: dict[str, object],
        namespace: str | None = None,
        operation_id: str | None = None,
        core: str | None = None,
    ) -> Reply:
        self.require_projection()
        before = self.store.capacity_count()
        receipt_id = secrets.token_hex(16)
        record = {
            "schema": "fulfillment-receipt-v1",
            "receipt_id": receipt_id,
            "binding": builtins(binding),
            "request_digest": request_digest,
            "route": route,
            "authority": authority,
            "attestation": attestation,
            "original_status": "answered",
            "original_answer_count": len(
                msgspec.convert(self.binding(binding.ask_id)["answer_ids"], type=list[str])
            ),
            "prior_fulfillment": None,
            "lifecycle": "fulfilled",
            "created_at": stamp(),
            "active_before": before,
            "active_after": before - 1,
        }
        self.store.db.execute(
            "INSERT INTO ask_fulfillments VALUES (?,?,?,?,?,?,?,1)",
            (
                binding.ask_id,
                request_digest,
                receipt_id,
                canonical(record).decode(),
                namespace,
                operation_id,
                core,
            ),
        )
        from .fulfillment_projection import stamp_current

        stamp_current(self.store.db, binding.ask_id)
        self.store.db.execute(
            "INSERT INTO ask_fulfillment_audit(ask_id,request_digest,disposition,at) "
            "VALUES (?,?,'applied',?)",
            (binding.ask_id, request_digest, stamp()),
        )
        self.retire_pending(binding.ask_id, "fulfilled")
        self.post(
            f"fulfill:{receipt_id}",
            f"Ask {binding.ask_id} fulfilled; original answer retained.",
            self.store.first_card_message(binding.ask_id),
            None,
            None,
        )
        return Reply("applied", "fulfilled; original answer retained", record)

    def propose(self, req: Propose, *, uid: int, chunk_chars: int) -> Reply:
        from heterodyne.admind import chunk

        why = check(req)
        if why:
            raise Invalid(why)
        self.require_uid(uid)
        with self.store.transaction():
            self.require_projection()
            self.validate(req.declaration.binding, req.declaration.evidence)
            if self.protected(req.ask_id):
                raise Invalid("conflict")
            old = self.store.db.execute(
                "SELECT intent_id,digest,declaration,created_at FROM ask_fulfillment_intents "
                "WHERE ask_id=? AND status='pending'",
                (req.ask_id,),
            ).fetchone()
            encoded = canonical(builtins(req.declaration)).decode()
            if old is not None and str(old[2]) == encoded and req.replace_intent is None:
                if datetime.now(UTC) - datetime.fromisoformat(str(old[3])) >= timedelta(hours=24):
                    raise Invalid("stale-intent")
                return Reply(
                    "pending",
                    "notice-" + self.notice_state(str(old[0]))[0],
                    {"intent_id": old[0], "digest": old[1]},
                )
            if old is not None:
                if req.replace_intent != old[1]:
                    raise Invalid("pending-conflict")
                if self.notice_state(str(old[0]), check_expiry=False)[0] not in ("pending", "delivered"):
                    raise Invalid("notice-owner-unavailable")
                self.retire(req.ask_id, str(old[1]), "operator-withdrawal")
            elif req.replace_intent is not None:
                raise Invalid("stale-intent")
            count = self.store.db.execute(
                "SELECT COUNT(*) FROM ask_fulfillment_intents WHERE status='pending'"
            ).fetchone()[0]
            if count >= 40:
                raise Invalid("pending-cap")
            intent = secrets.token_hex(16)
            at = stamp()
            payload = {
                "schema": "fulfillment-intent-v1",
                "intent_id": intent,
                "route": "operator",
                "declaration": builtins(req.declaration),
                "expected_fulfillment": None,
                "created_at": at,
            }
            intent_digest = digest("intent", payload)
            row = self.store.ask(req.ask_id)
            if row is None:
                raise Invalid("missing")
            response_ids = ", ".join(
                display_digest(value) for value in req.declaration.binding.consumed_answers
            )
            consumed = set(req.declaration.binding.consumed_answers)
            response_context = "\n".join(
                f"{answer.operator}: {answer.text}"
                for answer in self.store.answers(req.ask_id)
                if answer.message_id in consumed and answer.kind == "answer"
            )
            notice = (
                f"Confirm completed work for ask {req.ask_id}: {row.title}\n"
                f"Requested work: {row.body}\n"
                f"Intent {intent}; digest {display_digest(intent_digest)}\n"
                f"Consumed response IDs: {response_ids}\n"
                f"Consumed response context: {response_context}\n"
                f"Claimed completed work: {req.declaration.description}\n"
                "Evidence IDs: " + ", ".join(e.artifact_id for e in req.declaration.evidence) + "\n"
                "No machine proof: this is a controller declaration for your assessment.\n"
                "Reply !fulfill to this notice only if the requested work is complete.\n"
                "This releases capacity; it does not authorize execution or change the original answer."
            )
            if len(notice) > 3500 or notice.count("\n") > 40 or redact(notice) != notice:
                raise Invalid("notice-redaction-or-bounds")
            parts = chunk.split(notice, chunk_chars)
            if any(redact(part) != part for part in parts):
                raise Invalid("notice-redaction-or-bounds")
            if len(encoded.encode()) > MAX_FILE:
                raise Invalid("oversized-intent")
            self.store.db.execute(
                "INSERT INTO ask_fulfillment_intents VALUES (?,?,?,?,?,?,?,'pending',NULL)",
                (intent, req.ask_id, intent_digest, encoded, notice, len(parts), at),
            )
            # Durable inbound provenance, never the answer's display label. Any unknown source
            # quarantines the entire quoted context; a mixed set cannot become system-owned.
            senders = [
                self.store.inbound_sender(answer.message_id)
                for answer in self.store.answers(req.ask_id)
                if answer.message_id in req.declaration.binding.consumed_answers
            ]
            owners = tuple(sorted({sender for sender in senders if sender})) if all(senders) else ()
            for i, part in enumerate(parts):
                self.post(
                    f"fulfill-intent:{intent}:{i}",
                    part,
                    None,
                    None if i == 0 else f"fulfill-intent:{intent}:0",
                    owners,
                )
            return Reply(
                "pending",
                "notice-" + self.notice_state(intent)[0],
                {"intent_id": intent, "digest": intent_digest},
            )

    def delivery(self, intent: str, parts: object) -> list[tuple[object, ...]]:
        if type(parts) is not int or not 1 <= parts <= 3500:
            raise Invalid("delivery-unresolved")
        prefix = "fulfill-intent:" + intent
        rows = self.store.db.execute(
            "SELECT key,length(CAST(text AS BLOB)),status,message_id,ownership_kind,seq "
            "FROM outbox WHERE key>=? COLLATE BINARY AND key<? COLLATE BINARY ORDER BY key LIMIT ?",
            (prefix + ":", prefix + ";", parts + 1),
        ).fetchall()
        if len(rows) != parts or {r[0] for r in rows} != {prefix + ":" + str(i) for i in range(parts)}:
            raise Invalid("delivery-unresolved")
        if any(r[1] > 14000 for r in rows):
            raise Invalid("delivery-unresolved")
        rows.sort(key=lambda r: int(str(r[0]).rsplit(":", 1)[1]))
        return rows

    def notice_state(self, intent: str, *, check_expiry: bool = True) -> tuple[str, str]:
        row = self.store.db.execute(
            "SELECT parts,created_at,length(CAST(notice AS BLOB)) "
            "FROM ask_fulfillment_intents WHERE intent_id=?",
            (intent,),
        ).fetchone()
        if row is None:
            return "unavailable", "delivery-unresolved"
        try:
            if check_expiry and datetime.now(UTC) - datetime.fromisoformat(str(row[1])) >= timedelta(
                hours=24
            ):
                return "expired", "intent-expired"
            if row[2] > 14000:
                raise Invalid("delivery-unresolved")
            rows = self.delivery(intent, row[0])
            statuses = {r[2] for r in rows}
            if statuses == {"quarantined"}:
                return "quarantined", "unproven-owner"
            if statuses == {"suppressed"}:
                return "suppressed", "requester-revoked"
            kinds = {r[4] for r in rows}
            owners = [self.store.row_owners(cast(int, r[5])) for r in rows]
            if kinds == {"unknown"} or (kinds == {"request"} and all(not o for o in owners)):
                return "quarantined", "unproven-owner"
            if kinds != {"request"} or any(not o for o in owners) or len(set(owners)) != 1:
                raise Invalid("delivery-unresolved")
            authority = self.notice_authority()
            if authority is None:
                raise Invalid("delivery-unresolved")
            if not set(owners[0]) <= authority:
                return "suppressed", "requester-revoked"
            texts = [
                self.store.db.execute("SELECT text FROM outbox WHERE seq=?", (r[5],)).fetchone()[0]
                for r in rows
            ]
            text = "".join(texts)
            if len(text) > 3500 or text.count("\n") > 40:
                raise Invalid("delivery-unresolved")
            notice = self.store.db.execute(
                "SELECT notice FROM ask_fulfillment_intents WHERE intent_id=?", (intent,)
            ).fetchone()[0]
            if text != notice:
                raise Invalid("delivery-unresolved")
            if statuses == {"sent"} and all(r[3] for r in rows):
                return "delivered", "delivery-complete"
            if statuses == {"pending"} and all(not r[3] for r in rows):
                return "pending", "delivery-pending"
        except (Invalid, ValueError, TypeError, OverflowError):
            pass
        return "unavailable", "delivery-unresolved"

    def inspect(self) -> dict[str, object]:
        with self.store.transaction(read_only=True):
            rows = self.store.db.execute(
                "SELECT ask_id,intent_id,digest,created_at FROM ask_fulfillment_intents "
                "WHERE status='pending' ORDER BY intent_id LIMIT 41"
            ).fetchall()
            if len(rows) > 40:
                raise Invalid("fulfillment-intent-integrity")
            entries: list[dict[str, object]] = []
            for ask_id, intent, digest_value, created in rows:
                state, reason = self.notice_state(str(intent))
                actions = {
                    "pending": ["get", "dispose", "replace", "hold"],
                    "delivered": ["get", "dispose", "hold"],
                    "quarantined": ["dispose", "controller-receipt", "hold"],
                    "suppressed": ["dispose", "controller-receipt", "hold"],
                    "expired": ["dispose", "replace", "controller-receipt", "hold"],
                    "unavailable": ["get", "dispose", "controller-receipt", "hold"],
                }[state]
                entry = dict(
                    ask_id=ask_id,
                    intent_id=intent,
                    digest=digest_value,
                    created_at=created,
                    state=state,
                    reason=reason,
                    actions=actions,
                )
                if len(canonical(entry)) > 1024:
                    raise Invalid("fulfillment-intent-integrity")
                entries.append(entry)
            return {"schema": "fulfillment-inspect-v1", "entries": entries}

    def retire_pending(self, ask_id: str, reason: str) -> None:
        """Caller holds the mutation transaction; retain notice rows and an audit tombstone."""
        row = self.store.db.execute(
            "SELECT digest FROM ask_fulfillment_intents WHERE ask_id=? AND status='pending'", (ask_id,)
        ).fetchone()
        if row is None:
            return
        self.store.db.execute(
            "INSERT OR IGNORE INTO ask_fulfillment_audit(ask_id,request_digest,disposition,at) "
            "VALUES (?,?,?,?)",
            (ask_id, row[0], reason, stamp()),
        )
        self.store.db.execute(
            "UPDATE ask_fulfillment_intents SET status='retired',reason=? "
            "WHERE ask_id=? AND status='pending'",
            (reason, ask_id),
        )

    def retire(self, ask_id: str, expected: str, reason: str) -> Reply:
        if reason not in ("operator-withdrawal", "stale-answers", "expired"):
            raise Invalid("disposition")
        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT intent_id FROM ask_fulfillment_intents "
                "WHERE ask_id=? AND digest=? AND status='pending'",
                (ask_id, expected),
            ).fetchone()
            if row is None:
                raise Invalid("stale-intent")
            self.store.db.execute(
                "INSERT OR IGNORE INTO ask_fulfillment_audit(ask_id,request_digest,disposition,at) "
                "VALUES (?,?,?,?)",
                (ask_id, expected, reason, stamp()),
            )
            self.store.db.execute(
                "UPDATE ask_fulfillment_intents SET status='retired',reason=? WHERE intent_id=?",
                (reason, row[0]),
            )
            return Reply("retired", "intent retired; delivery tombstones retained")

    def confirm(self, target: str | None, *, principal: str, message_id: str) -> Reply:
        # Caller verifies the currently authenticated inbound principal under the daemon's actual gates.
        with self.store.transaction():
            targets = self.store.db.execute(
                "SELECT key FROM outbox WHERE message_id=? AND status='sent' LIMIT 2", (target,)
            ).fetchall()
            if len(targets) != 1:
                raise Invalid("unbound-confirmation")
            key = str(targets[0][0])
            match = re.fullmatch(r"fulfill-intent:([0-9a-f]{32}):[0-9]+", key)
            if match is None:
                raise Invalid("unbound-confirmation")
            intent = match[1]
            row = self.store.db.execute(
                "SELECT ask_id,digest,declaration,status FROM ask_fulfillment_intents WHERE intent_id=?",
                (intent,),
            ).fetchone()
            if row is None:
                raise Invalid("unbound-confirmation")
            ask_id, intent_digest, declaration, status = row
            duplicate = self.duplicate(str(ask_id), str(intent_digest))
            if duplicate:
                return duplicate
            state = self.notice_state(intent)[0]
            if status != "pending" or state == "expired":
                raise Invalid("stale-intent")
            if state != "delivered":
                raise Invalid("incomplete-notice")
            declared = msgspec.json.decode(str(declaration), type=Declaration)
            self.validate(declared.binding, declared.evidence)
            # Consume our own intent before commit retires *other* pending intents. Both changes
            # share this transaction, so a failed receipt/notice rolls the consumption back too.
            self.store.db.execute(
                "UPDATE ask_fulfillment_intents SET status='consumed',reason=NULL WHERE intent_id=?",
                (intent,),
            )
            result = self.commit(
                declared.binding,
                str(intent_digest),
                "operator",
                {"principal": principal, "message_id": message_id},
                {
                    "declaration_sha256": digest("declaration", builtins(declared)),
                    "intent_id": intent,
                    "namespace": "operator-confirmation",
                    "operation_id": intent,
                    "evidence": [builtins(item) for item in declared.evidence],
                },
            )
            return result


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def display_digest(value: str) -> str:
    """Generated audit digest groups; all supplied prose still passes ordinary redaction."""
    return " ".join(value[i : i + 16] for i in range(0, len(value), 16))
