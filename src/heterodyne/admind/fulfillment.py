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
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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


Request = Apply | Propose | Dispose | Get


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
    # Exact persisted native ask alphabet, never normalizing an attested identity.
    if len(ask_id) != 4 or any(c not in "23456789abcdefghjkmnpqrstuvwxyz" for c in ask_id):
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


class Lifecycle:
    def __init__(self, store: Store):
        self.store = store
        self.post: Callable[[str, str, str | None, str | None], None] = self._post

    def _post(self, key: str, text: str, target: str | None, thread: str | None) -> None:
        """Offline Store adapter. The daemon installs its central redaction/wake pipeline."""
        self.store.enqueue(key, redact(text), target, thread_key=thread, ownership_kind="system")

    def migrate(self) -> None:
        with self.store.transaction():
            version = self.store.get("ask_fulfillment_schema")
            if version not in (None, "1"):
                raise Invalid("unsupported-schema")
            if version == "1":
                present = {
                    str(row[0])
                    for row in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"ask_fulfillments", "ask_fulfillment_intents", "ask_fulfillment_audit"} <= present:
                    raise Invalid("incomplete-schema")
                identity = self.store.get("ask_store_id")
                if identity is None or not HEX.fullmatch(identity):
                    raise Invalid("missing-store-identity")
            for statement in DDL:
                self.store.db.execute(statement)
            if self.store.get("ask_store_id") is None:
                self.store.set("ask_store_id", secrets.token_hex(32))
            for statement in DDL:
                expected = statement.replace(" IF NOT EXISTS", "")
                name = (
                    expected.split()[2] if not expected.startswith("CREATE UNIQUE") else expected.split()[3]
                )
                actual = self.store.db.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (name,)
                ).fetchone()
                if actual is None or str(actual[0]) != expected:
                    raise Invalid("incompatible-schema")
            identity = self.store.get("ask_store_id")
            if identity is None or not HEX.fullmatch(identity):
                raise Invalid("missing-store-identity")
            self.store.set("ask_fulfillment_schema", "1")
            for (ask_id,) in self.store.db.execute("SELECT ask_id FROM ask_fulfillments").fetchall():
                self.refresh(str(ask_id))

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
                "SELECT receipt FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
            ).fetchone()
            return None if row is None else json.loads(str(row[0]))

    def protected(self, ask_id: str) -> bool:
        with self.store.lock:
            return (
                self.store.db.execute("SELECT 1 FROM ask_fulfillments WHERE ask_id=?", (ask_id,)).fetchone()
                is not None
            )

    def view(self, ask_id: str) -> dict[str, object]:
        with self.store.transaction():
            binding = self.binding(ask_id)
            original = self.store.ask(ask_id)
            row = self.store.db.execute(
                "SELECT valid FROM ask_fulfillments WHERE ask_id=?", (ask_id,)
            ).fetchone()
            pending = self.store.db.execute(
                "SELECT intent_id,digest,status,created_at FROM "
                "ask_fulfillment_intents WHERE ask_id=? AND status='pending'",
                (ask_id,),
            ).fetchone()
            return {
                "schema": "fulfillment-view-v1",
                "binding": binding,
                "receipt": self.stored(ask_id),
                "lifecycle": "unfulfilled" if row is None else "fulfilled" if row[0] else "conflict",
                "eligible": row is None
                and original is not None
                and original.kind in ("question", "merge")
                and original.status == "answered"
                and bool(binding["answer_ids"]),
                "pending": None
                if pending is None
                else dict(zip(("intent_id", "digest", "status", "created_at"), pending, strict=True)),
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
        if row[0] != request_digest or not row[1]:
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
        )
        return Reply("applied", "fulfilled; original answer retained", record)

    def propose(self, req: Propose, *, uid: int, chunk_chars: int) -> Reply:
        from heterodyne.admind import chunk

        why = check(req)
        if why:
            raise Invalid(why)
        self.require_uid(uid)
        with self.store.transaction():
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
                return Reply("pending", "existing notice retained", {"intent_id": old[0], "digest": old[1]})
            if old is not None:
                if req.replace_intent != old[1]:
                    raise Invalid("pending-conflict")
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
            for i, part in enumerate(parts):
                self.post(
                    f"fulfill-intent:{intent}:{i}",
                    part,
                    None,
                    None if i == 0 else f"fulfill-intent:{intent}:0",
                )
            return Reply(
                "pending", "confirmation notice queued", {"intent_id": intent, "digest": intent_digest}
            )

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
            rows = self.store.db.execute(
                "SELECT i.intent_id,i.ask_id,i.digest,i.declaration,i.parts,"
                "i.created_at,i.status FROM ask_fulfillment_intents i JOIN outbox o "
                "ON o.key LIKE 'fulfill-intent:' || i.intent_id || ':%' "
                "WHERE o.message_id=? AND o.status='sent'",
                (target,),
            ).fetchall()
            if len(rows) != 1:
                raise Invalid("unbound-confirmation")
            intent, ask_id, intent_digest, declaration, parts, created, status = rows[0]
            duplicate = self.duplicate(str(ask_id), str(intent_digest))
            if duplicate:
                return duplicate
            if status != "pending" or datetime.now(UTC) - datetime.fromisoformat(str(created)) >= timedelta(
                hours=24
            ):
                raise Invalid("stale-intent")
            delivery = self.store.db.execute(
                "SELECT text,status,message_id FROM outbox WHERE key LIKE ? ORDER BY seq",
                (f"fulfill-intent:{intent}:%",),
            ).fetchall()
            original_notice = self.store.db.execute(
                "SELECT notice FROM ask_fulfillment_intents WHERE intent_id=?", (intent,)
            ).fetchone()[0]
            if (
                len(delivery) != int(parts)
                or any(r[1] != "sent" or not r[2] for r in delivery)
                or "".join(str(r[0]) for r in delivery) != original_notice
            ):
                raise Invalid("incomplete-notice")
            declared = msgspec.json.decode(str(declaration), type=Declaration)
            self.validate(declared.binding, declared.evidence)
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
            self.store.db.execute(
                "UPDATE ask_fulfillment_intents SET status='consumed' WHERE intent_id=?", (intent,)
            )
            return result


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def display_digest(value: str) -> str:
    """Generated audit digest groups; all supplied prose still passes ordinary redaction."""
    return " ".join(value[i : i + 16] for i in range(0, len(value), 16))
