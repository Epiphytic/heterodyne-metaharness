"""ask.binding.v1: bounded source-owned evidence, never a new approval or authentication claim.

The local reader takes one store read snapshot under daemon serialization. It returns the stored inputs
and exact original/full-repeat outbox bytes only if the current renderer reproduces them without
redaction or shortening. A changed chunk configuration or legacy missing provenance is an explicit
refusal. Delivery means the existing local outbox reports sent, not human acceptance. Decision attempts
are separately named; an attempt is not a recorded native decision.
"""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.admind import approvals, asks, chunk
from heterodyne.admind import links as ask_links
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import Operator
from heterodyne.admind.store import AskRow, Store

MAX_PARTS = 256
MAX_REPEATS = 32
MAX_BYTES = 900_000  # leaves room below ask.sock's 1 MiB reply limit
MAX_INPUT_CHARS = 200_000
HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")
NATIVE_ID = re.compile(r"(?:r:)?[0-9a-f]{64}\Z")
STATES = frozenset((*asks.ACTIVE, *asks.TERMINAL))
RefusalCode = Literal["missing", "incomplete", "oversized", "redacted", "truncated", "render-mismatch",
                      "invalid-state", "policy-unavailable", "clock-unavailable",
                      "invalid-reply", "transport-unavailable", "response-timeout"]


class Refusal(Exception):
    def __init__(self, code: RefusalCode) -> None:
        self.code: RefusalCode = code


class StoredForm(msgspec.Struct, forbid_unknown_fields=True):
    choices: list[str]
    recommended: str


class StoredApproval(msgspec.Struct, forbid_unknown_fields=True):
    readout: approvals.Readout
    details: list[str]


class RenderInputs(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    ask_id: str
    kind: asks.AskKind
    title: str
    body: str
    poster: str
    form: asks.FormKind | None
    choices: list[str]
    recommended: str | None
    links: list[ask_links.LinkSpec]
    pr_url: str | None
    head_sha: str | None
    bead: str | None
    digest: str | None
    refreshed_from: str | None


class CardPart(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    key: str
    text: str
    status: Literal["pending", "sent", "failed", "quarantined", "suppressed", "delayed"]
    message_id: str | None


class BoundAnswer(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    sequence: int
    kind: Literal["answer", "note"]
    message_id: str
    operator: str
    sender_key: str
    current_policy: bool
    text: str
    choice: str | None
    recorded_at: str


class DecisionAttempt(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    message_id: str
    operator: str
    sender_key: str
    current_policy: bool
    action: Literal["approve", "deny"]
    ref: str
    digest: str
    note: str | None
    started_at: str
    settled: Literal["refused", "recorded", "untouched", "blocked", "uncertain"] | None
    exit_status: int | None


class PolicyPrincipal(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: str
    sender_key: str


class Envelope(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: Literal[1]
    inputs: RenderInputs
    status: str
    outcome: str | None
    decided_by: str | None
    current_attempt: str | None
    render_input_sha256: str
    delivered: bool
    delivery_source: Literal["original", "repeat"]
    card_parts: list[CardPart]
    truncated: Literal[False]
    answers: list[BoundAnswer]
    answer_history_complete: Literal[True]
    decision_attempts: list[DecisionAttempt]
    decision_history_complete: Literal[True]
    policy: list[PolicyPrincipal]
    policy_sha256: str
    read_at_boottime_ns: int
    boot_id: str


class AskBindingReply(asks.AskReply, frozen=True, forbid_unknown_fields=True):
    """Only this new operation extends the old reply shape; original responses are unchanged."""
    code: RefusalCode | None = None
    binding: Envelope | None = None


def canonical(value: object) -> bytes:
    """Canonical v1 JSON: sorted keys, compact UTF-8, no ASCII escapes or final newline."""
    return json.dumps(msgspec.to_builtins(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def clock() -> tuple[int, str]:
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if str(uuid.UUID(boot)) != boot:
            raise ValueError("boot ID")
        return time.clock_gettime_ns(time.CLOCK_BOOTTIME), boot
    except (OSError, ValueError, AttributeError):
        raise Refusal("clock-unavailable") from None


def policy_snapshot(load: Callable[[], tuple[Operator, ...]]) -> list[PolicyPrincipal]:
    try:
        rows = load()
        if not rows or len(rows) > 32:
            raise ValueError("policy size")
        names: set[str] = set()
        keys: set[str] = set()
        result: list[PolicyPrincipal] = []
        for row in rows:
            if (not row.name or len(row.name) > 128 or any(ord(c) < 32 for c in row.name)
                    or not HEX_KEY.fullmatch(row.hex) or row.name in names or row.hex in keys):
                raise ValueError("policy identity")
            names.add(row.name)
            keys.add(row.hex)
            result.append(PolicyPrincipal(row.name, row.hex))
        return sorted(result, key=lambda r: (r.name, r.sender_key))
    except Exception:  # noqa: BLE001 - no stale policy fallback or exception text
        raise Refusal("policy-unavailable") from None


def stored_inputs(row: AskRow) -> RenderInputs:
    if row.kind not in ("question", "merge", "approval") or row.status not in STATES:
        raise Refusal("invalid-state")
    if row.truncated:
        raise Refusal("truncated")
    if not 1 <= row.card_parts <= MAX_PARTS:
        raise Refusal("incomplete")
    # Strict conversion, unlike the legacy display helper's permissive str coercions.
    choices: list[str] = []
    recommended = None
    if row.question_form is not None:
        if row.kind != "question" or row.question_form not in ("choices", "yes-no"):
            raise Refusal("invalid-state")
        form = msgspec.json.decode(row.choices or "", type=StoredForm)
        choices, recommended = form.choices, form.recommended
    elif row.choices is not None:
        raise Refusal("invalid-state")
    specs = msgspec.json.decode(row.links, type=list[ask_links.LinkSpec]) if row.links is not None else []
    inputs = RenderInputs(row.ask_id, row.kind, row.title, row.body, row.poster,
                          row.question_form, choices, recommended, specs,
                          row.pr_url, row.head_sha, row.bead, row.digest, row.refreshed_from)
    if len(canonical(inputs)) > MAX_INPUT_CHARS:
        raise Refusal("oversized")
    if row.kind != "approval":
        request = asks.AskPost(inputs.kind, row.title, row.body, row.pr_url, row.head_sha, row.bead,
                               row.poster, inputs.form, choices, recommended, specs)
        if asks.check(request) is not None:
            raise Refusal("invalid-state")
        if redact(asks.context(row)) != asks.context(row):
            raise Refusal("redacted")
    elif row.question_form is not None or row.choices is not None or row.links is not None:
        raise Refusal("invalid-state")
    return inputs


def _parts(db: sqlite3.Connection, prefix: str, count: int) -> list[CardPart]:
    if not 1 <= count <= MAX_PARTS:
        raise Refusal("incomplete")
    # Exact keys, no caller SQL or broad prefix match; size checked before materializing text.
    actual = db.execute("SELECT count(*) FROM (SELECT 1 FROM outbox WHERE key GLOB ? LIMIT ?)",
                        (prefix + ":*", MAX_PARTS + 1)).fetchone()[0]
    if actual != count:
        raise Refusal("incomplete")
    keys = [f"{prefix}:{i}" for i in range(count)]
    parts: list[CardPart] = []
    total = 0
    for key in keys:
        row = db.execute("SELECT length(CAST(text AS BLOB)) FROM outbox WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise Refusal("incomplete")
        total += int(row[0])
        if total > MAX_BYTES // 2:
            raise Refusal("oversized")
        raw = db.execute("SELECT key, text, status, message_id FROM outbox WHERE key = ?", (key,)).fetchone()
        part = msgspec.convert(dict(zip(("key", "text", "status", "message_id"), raw, strict=True)),
                               type=CardPart)
        if part.status == "sent" and (part.message_id is None or not HEX_KEY.fullmatch(part.message_id)):
            raise Refusal("incomplete")
        if redact(part.text) != part.text:
            raise Refusal("redacted")
        parts.append(part)
    return parts


def _expected(row: AskRow, chunk_chars: int, repeat: bool) -> list[str]:
    if row.kind == "approval":
        stored = msgspec.json.decode(row.body, type=StoredApproval)
        if (row.digest is None or not HEX_KEY.fullmatch(row.digest)
                or stored.readout.id != row.bead or stored.readout.digest != row.digest):
            raise Refusal("invalid-state")
        if len(stored.details) != row.card_parts:
            raise Refusal("incomplete")
        # These historical presentation counts were not stored as columns. Recover only the renderer's
        # closed counter line, then reproduce ALL card bytes from the typed native readout, not from a
        # self-asserted details copy. Changed layouts or missing counters fail closed.
        counts = _approval_counts(stored.details)
        card = asks.approval_card(row, stored.readout, chunk_chars, counts)
        if isinstance(card, str):
            raise Refusal("redacted" if card == asks.REDACTED else "render-mismatch")
        if card.card_chunks != stored.details:
            raise Refusal("render-mismatch")
        return card.card_chunks
    # Reuse the approval renderer's whole-and-packed check BEFORE posting redacts its output.
    parts, truncated = asks.checked_card_chunks(row, chunk_chars, repeat=repeat)
    if truncated:
        raise Refusal("truncated")
    if isinstance(parts, str):
        raise Refusal("redacted" if parts == asks.REDACTED else "render-mismatch")
    return parts


def _approval_counts(parts: list[str]) -> tuple[int, int]:
    for part in parts:
        for line in part.splitlines():
            if line == asks.in_flight(0, 0):
                return 0, 0
            match = re.fullmatch(r"(No other asks outstanding|([0-9]{1,4}) other asks? outstanding)"
                                 r"(?:, ([0-9]{1,4}) in flight)?\.", line)
            if match is not None:
                counts = int(match[2] or 0), int(match[3] or 0)
                if asks.in_flight(*counts) == line:
                    return counts
    raise Refusal("incomplete")


def delivery(db: sqlite3.Connection, row: AskRow, chunk_chars: int) -> tuple[list[CardPart], bool, bool]:
    parts = _parts(db, f"ask:{row.ask_id}", row.card_parts)
    if [p.text for p in parts] != _expected(row, chunk_chars, False):
        raise Refusal("render-mismatch")
    if all(p.status == "sent" for p in parts):
        return parts, True, False
    repeats = db.execute("SELECT request, parts FROM ask_repeats WHERE ask_id = ? "
                         "ORDER BY created_at, rowid LIMIT ?", (row.ask_id, MAX_REPEATS + 1)).fetchall()
    if len(repeats) > MAX_REPEATS:
        raise Refusal("oversized")
    for request, count in repeats:
        if not isinstance(request, str) or not HEX_KEY.fullmatch(request):
            raise Refusal("invalid-state")
        repeated = _parts(db, f"askr:{row.ask_id}:{request}", int(count))
        if [p.text for p in repeated] != _expected(row, chunk_chars, True):
            raise Refusal("render-mismatch")
        if all(p.status == "sent" for p in repeated):
            return repeated, True, True
    return parts, False, False


def _identity(mid: str, operator: str, sender: str | None,
              policy: dict[str, str]) -> tuple[str, bool]:
    if not NATIVE_ID.fullmatch(mid) or sender is None or not HEX_KEY.fullmatch(sender) or operator == "?":
        raise Refusal("incomplete")
    # Preserve historical actors and contrary choices; do not drop revoked actors from history.
    return sender, policy.get(operator) == sender


def _at(text: str) -> str:
    if datetime.fromisoformat(text).tzinfo is None:
        raise Refusal("invalid-state")
    return text


def _history_budget(db: sqlite3.Connection, ask_id: str) -> None:
    # Bounds before fetching any text. Each subquery reads at most 51 rows from its ask-id index.
    for query in (
        "SELECT count(*), coalesce(sum(n),0) FROM (SELECT length(CAST(text AS BLOB)) + "
        "length(operator) + length(message_id) + "
        "coalesce(length(choice),0) + length(at) AS n FROM ask_answers WHERE ask_id = ? LIMIT ?)",
        "SELECT count(*), coalesce(sum(n),0) FROM (SELECT coalesce(length(CAST(note AS BLOB)),0) + "
        "length(operator) + length(message_id) + "
        "length(ref) + length(digest) + length(started_at) AS n FROM ask_attempts WHERE ask_id = ? LIMIT ?)",
    ):
        budget = db.execute(query,
                            (ask_id, asks.MAX_ANSWERS + 1)).fetchone()
        if budget[0] > asks.MAX_ANSWERS or budget[1] > asks.MAX_ANSWER_TOTAL * 4:
            raise Refusal("oversized")


def histories(db: sqlite3.Connection, ask_id: str, policy: dict[str, str]
              ) -> tuple[list[BoundAnswer], list[DecisionAttempt]]:
    _history_budget(db, ask_id)
    rows = db.execute("SELECT a.seq, a.kind, a.message_id, a.operator, i.sender_key, a.text, a.choice, a.at "
                      "FROM ask_answers a LEFT JOIN inbound i ON i.message_id = a.message_id "
                      "WHERE a.ask_id = ? ORDER BY a.seq LIMIT ?", (ask_id, asks.MAX_ANSWERS + 1)).fetchall()
    if len(rows) > asks.MAX_ANSWERS:
        raise Refusal("oversized")
    answers: list[BoundAnswer] = []
    for seq, kind, mid, operator, sender, text, choice, at in rows:
        if len(text) > asks.MAX_ANSWER:
            raise Refusal("oversized")
        if seq != len(answers) + 1:
            raise Refusal("incomplete")
        sender, current = _identity(mid, operator, sender, policy)
        if redact(text) != text:
            raise Refusal("redacted")
        answers.append(msgspec.convert(dict(sequence=seq, kind=kind, message_id=mid, operator=operator,
                                           sender_key=sender, current_policy=current, text=text,
                                           choice=choice,
                                           recorded_at=_at(at)), type=BoundAnswer))
    attempts = db.execute("SELECT a.message_id, a.operator, i.sender_key, a.action, a.ref, a.digest, "
                          "a.note, a.started_at, a.settled, a.exit_status FROM ask_attempts a "
                          "LEFT JOIN inbound i ON i.message_id = a.message_id WHERE a.ask_id = ? "
                          "ORDER BY a.started_at, a.rowid LIMIT ?", (ask_id, asks.MAX_ANSWERS + 1)).fetchall()
    if len(attempts) > asks.MAX_ANSWERS:
        raise Refusal("oversized")
    decisions: list[DecisionAttempt] = []
    for mid, operator, sender, action, ref, digest, note, at, settled, exit_status in attempts:
        sender, current = _identity(mid, operator, sender, policy)
        if note is not None:
            if len(note) > asks.MAX_ANSWER:
                raise Refusal("oversized")
            if redact(note) != note:
                raise Refusal("redacted")
        decisions.append(msgspec.convert(dict(message_id=mid, operator=operator, sender_key=sender,
                                             current_policy=current, action=action, ref=ref, digest=digest,
                                             note=note, started_at=_at(at), settled=settled,
                                             exit_status=exit_status), type=DecisionAttempt))
    return answers, decisions


def read(store: Store, ask_id: str, chunk_chars: int,
         load_policy: Callable[[], tuple[Operator, ...]]) -> AskBindingReply:
    """Synchronous, inside the daemon's ask_post_lock; bounded refusal payloads never echo bad data."""
    try:
        if asks.normalise_id(ask_id) != ask_id:
            raise Refusal("invalid-state")
        policy = policy_snapshot(load_policy)
        with store.read_snapshot() as db:
            size = db.execute("SELECT length(CAST(body AS BLOB)) + length(CAST(title AS BLOB)) + "
                              "coalesce(length(choices),0) + coalesce(length(links),0) + "
                              "coalesce(length(CAST(outcome AS BLOB)),0) + "
                              "coalesce(length(decided_by),0) + coalesce(length(attempt),0) FROM asks "
                              "WHERE ask_id = ?", (ask_id,)).fetchone()
            if size is None:
                raise Refusal("missing")
            if size[0] > MAX_INPUT_CHARS:
                raise Refusal("oversized")
            row = store.ask(ask_id)
            if row is None:
                raise Refusal("missing")
            inputs = stored_inputs(row)
            parts, delivered, repeated = delivery(db, row, chunk_chars)
            answers, attempts = histories(db, ask_id, {p.name: p.sender_key for p in policy})
            if policy_snapshot(load_policy) != policy:
                raise Refusal("policy-unavailable")
            current, = db.execute("SELECT attempt FROM asks WHERE ask_id = ?", (ask_id,)).fetchone()
            if current is not None and current not in {a.message_id for a in attempts}:
                raise Refusal("incomplete")
            for text in (row.outcome, row.decided_by):
                if text is not None:
                    if len(text) > asks.MAX_ANSWER:
                        raise Refusal("oversized")
                    if redact(text) != text:
                        raise Refusal("redacted")
            at, boot = clock()
            result = Envelope(1, inputs, row.status, row.outcome, row.decided_by, current,
                              sha256(inputs), delivered,
                              "repeat" if repeated else "original", parts, False, answers, True, attempts,
                              True, policy, sha256(policy), at, boot)
            reply = AskBindingReply("ok", "ask.binding.v1 complete", binding=result)
            if len(msgspec.json.encode(reply)) > MAX_BYTES:
                raise Refusal("oversized")
            return reply
    except Refusal as exc:
        return AskBindingReply("refused", "ask.binding.v1 unavailable", code=exc.code)
    except (ValueError, TypeError, KeyError, OverflowError, chunk.TooWide, RuntimeError, sqlite3.Error):
        return AskBindingReply("refused", "ask.binding.v1 unavailable", code="invalid-state")
