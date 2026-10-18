"""Local retirement of fully proven not-issued attempts. No native writer lives here.

The daemon authenticates and serializes admission. This Store companion only checks durable facts;
proof tokens select immutable observations and confer no authority of their own.
"""

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

from heterodyne.admind.approvals import DIGEST, Attempt, MaintenanceHold, maintenance_hold

if TYPE_CHECKING:
    from heterodyne.admind.daemon import Admind
    from heterodyne.admind.store import Store


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Proof:
    ask_id: str
    message_id: str
    invocations: int
    body: str
    sha256: str

    @property
    def token(self) -> str:
        return self.sha256[:24]


def display_attempt(message_id: str) -> str:
    """Lossless grouped transport identity; never exempt arbitrary text from redaction."""
    return "-".join(message_id[i : i + 8] for i in range(0, len(message_id), 8))


class Retirement:
    def __init__(self, store: "Store") -> None:
        self.store = store

    def migrate(self) -> None:
        with self.store.transaction():
            db = self.store.db
            columns = {str(r[1]) for r in db.execute("PRAGMA table_info(ask_attempts)")}
            if "invocations" not in columns:
                db.execute("ALTER TABLE ask_attempts ADD COLUMN invocations INTEGER NOT NULL DEFAULT 0")
            if "proof_capable_since_begin" not in columns:
                db.execute(
                    "ALTER TABLE ask_attempts ADD COLUMN proof_capable_since_begin INTEGER NOT NULL DEFAULT 0"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS maintenance_proofs ("
                "ask_id TEXT NOT NULL, message_id TEXT NOT NULL, invocations INTEGER NOT NULL, "
                "body TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(ask_id,message_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS maintenance_recovery_notices ("
                "ask_id TEXT NOT NULL, message_id TEXT NOT NULL, sha256 TEXT NOT NULL, "
                "outbox_key TEXT NOT NULL UNIQUE, PRIMARY KEY(ask_id,message_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS maintenance_notice_history ("
                "outbox_key TEXT PRIMARY KEY, ask_id TEXT NOT NULL, message_id TEXT NOT NULL, "
                "sha256 TEXT NOT NULL)"
            )
            # Only existing exact mappings are safe to backfill. No inference from rendered text.
            db.execute(
                "INSERT OR IGNORE INTO maintenance_notice_history "
                "SELECT outbox_key,ask_id,message_id,sha256 FROM maintenance_recovery_notices"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS maintenance_retirements ("
                "ask_id TEXT NOT NULL, message_id TEXT NOT NULL, command_id TEXT NOT NULL UNIQUE, "
                "token TEXT NOT NULL, notice_id TEXT NOT NULL, body TEXT NOT NULL, "
                "PRIMARY KEY(ask_id,message_id))"
            )

    def generation(self, a: Attempt) -> tuple[int, bool] | None:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT invocations,proof_capable_since_begin FROM ask_attempts "
                "WHERE ask_id=? AND message_id=?",
                (a.ask_id, a.message_id),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0 or row[1] not in (0, 1):
                return None
            return row[0], row[1] == 1

    def record_hold(self, a: Attempt, hold: MaintenanceHold) -> bool:
        """Called only from the exact child-result branch, in the overlay's transaction."""
        with self.store.transaction():
            generation = self.generation(a)
            if (
                generation is None
                or not generation[1]
                or generation[0] < 1
                or not maintenance_hold(75, hold.stdout)
                or DIGEST.fullmatch(hold.adapter_identity) is None
            ):
                return False
            row = self.store.ask(a.ask_id)
            if (
                row is None
                or row.status != "deciding"
                or row.digest != a.digest
                or self.store.current_attempt(a.ask_id) != a
                or row.outcome not in ("decision-issuing", "maintenance-issuing")
            ):
                return False
            previous = self._proof(a)
            count = generation[0]
            # Missing any invocation is irreversible here, even if identical HOLD output returns later.
            if count != 1 and (previous is None or previous.invocations != count - 1):
                return False
            prior = None if previous is None else json.loads(previous.body)
            if prior is not None and prior["adapter_identity"] != hold.adapter_identity:
                return False
            not_spawned = 0 if prior is None else prior.get("not_spawned", 0)
            body = canonical(
                {
                    "version": 1,
                    "attempt": asdict(a),
                    "invocations": count,
                    "consecutive": count - not_spawned,
                    "not_spawned": not_spawned,
                    "exit_status": 75,
                    "envelope": json.loads(hold.stdout),
                    "stdout_sha256": hashlib.sha256(hold.stdout).hexdigest(),
                    "adapter_identity": hold.adapter_identity,
                }
            )
            self.store.db.execute(
                "INSERT INTO maintenance_proofs VALUES (?,?,?,?,?) "
                "ON CONFLICT(ask_id,message_id) DO UPDATE SET "
                "invocations=excluded.invocations,body=excluded.body,sha256=excluded.sha256",
                (a.ask_id, a.message_id, count, body, digest(body)),
            )
            return True

    def record_not_spawned(self, a: Attempt) -> bool:
        """Only the in-process pre-spawn OSError branch may cover an issued generation.

        Never decrements the monotonic intent counter; missing/crashed/foreign generations remain HOLD.
        A prior exact HOLD is required, so this does not manufacture an initial not-issued receipt.
        """
        with self.store.transaction():
            previous = self._proof(a)
            generation = self.generation(a)
            row = self.store.ask(a.ask_id)
            if (
                previous is None
                or generation is None
                or not generation[1]
                or generation[0] != previous.invocations + 1
                or row is None
                or row.status != "deciding"
                or row.digest != a.digest
                or row.outcome != "maintenance-issuing"
                or self.store.current_attempt(a.ask_id) != a
            ):
                return False
            value = json.loads(previous.body)
            value["invocations"] = generation[0]
            value["not_spawned"] = value.get("not_spawned", 0) + 1
            body = canonical(value)
            self.store.db.execute(
                "UPDATE maintenance_proofs SET invocations=?,body=?,sha256=? "
                "WHERE ask_id=? AND message_id=? AND sha256=?",
                (generation[0], body, digest(body), a.ask_id, a.message_id, previous.sha256),
            )
            return True

    def _proof(self, a: Attempt) -> Proof | None:
        row = self.store.db.execute(
            "SELECT invocations,body,sha256 FROM maintenance_proofs WHERE ask_id=? AND message_id=?",
            (a.ask_id, a.message_id),
        ).fetchone()
        if row is None or type(row[0]) is not int or not isinstance(row[1], str):
            return None
        if len(row[1].encode("utf-8")) > 8192 or digest(row[1]) != row[2]:
            return None
        try:
            decoded: object = json.loads(row[1])
            if type(decoded) is not dict:
                return None
            value = cast(dict[str, object], decoded)
            not_spawned = value.get("not_spawned", 0)
            if (
                set(value) - {"not_spawned"}
                != {
                    "version",
                    "attempt",
                    "invocations",
                    "consecutive",
                    "exit_status",
                    "envelope",
                    "stdout_sha256",
                    "adapter_identity",
                }
                or value["version"] != 1
                or value["attempt"] != asdict(a)
                or value["invocations"] != row[0]
                or type(value["consecutive"]) is not int
                or type(not_spawned) is not int
                or not_spawned < 0
                or value["consecutive"] < 1
                or value["consecutive"] + not_spawned != row[0]
                or value["exit_status"] != 75
                or not maintenance_hold(75, canonical(value["envelope"]).encode())
            ):
                return None
            for name in ("stdout_sha256", "adapter_identity"):
                text = value[name]
                if type(text) is not str or DIGEST.fullmatch(text) is None:
                    return None
        except (ValueError, TypeError, KeyError):
            return None
        return Proof(a.ask_id, a.message_id, row[0], row[1], row[2])

    def eligible(self, a: Attempt) -> Proof | None:
        with self.store.lock:
            row = self.store.ask(a.ask_id)
            generation = self.generation(a)
            if (
                row is None
                or row.status != "deciding"
                or row.digest != a.digest
                or self.store.current_attempt(a.ask_id) != a
                or generation is None
                or not generation[1]
                or not (
                    row.outcome == "pending-maintenance"
                    or (row.outcome or "").startswith("maintenance-held:")
                )
                or self.store.maintenance_retry_at(a.ask_id, a.message_id, include_suspended=True) is None
            ):
                return None
            proof = self._proof(a)
            return proof if proof is not None and proof.invocations == generation[0] else None

    def bind_notice(self, proof: Proof, key: str) -> None:
        self.store.db.execute(
            "INSERT OR IGNORE INTO maintenance_notice_history VALUES (?,?,?,?)",
            (key, proof.ask_id, proof.message_id, proof.sha256),
        )
        self.store.db.execute(
            "INSERT INTO maintenance_recovery_notices VALUES (?,?,?,?) "
            "ON CONFLICT(ask_id,message_id) DO UPDATE SET "
            "sha256=excluded.sha256,outbox_key=excluded.outbox_key",
            (proof.ask_id, proof.message_id, proof.sha256, key),
        )

    def select(self, token: str, target: str | None) -> tuple[Attempt, Proof] | None:
        """Only an actually sent current notice can select a proof; collisions always refuse."""
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT p.ask_id FROM maintenance_proofs p JOIN maintenance_recovery_notices n "
                "ON n.ask_id=p.ask_id AND n.message_id=p.message_id AND n.sha256=p.sha256 "
                "JOIN outbox o ON o.key=n.outbox_key WHERE substr(p.sha256,1,24)=? "
                "AND o.status='sent' AND o.message_id=? LIMIT 2",
                (token, target),
            ).fetchall()
            if len(rows) != 1:
                return None
            a = self.store.current_attempt(str(rows[0][0]))
            proof = None if a is None else self.eligible(a)
            return None if a is None or proof is None else (a, proof)

    def receipt(self, token: str, target: str | None) -> str | None:
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT r.body FROM maintenance_retirements r "
                "JOIN maintenance_notice_history h ON h.ask_id=r.ask_id AND h.message_id=r.message_id "
                "JOIN outbox o ON o.key=h.outbox_key "
                "WHERE substr(h.sha256,1,24)=? AND o.status='sent' AND o.message_id=? LIMIT 2",
                (token, target),
            ).fetchall()
            return str(rows[0][0]) if len(rows) == 1 else None

    def commit(
        self,
        a: Attempt,
        proof: Proof,
        *,
        command: str,
        sender: str,
        operator: str,
        notice: str,
        native: str,
        blocked: bool,
        text: str,
        reason: str,
    ) -> bool:
        """Caller authenticates with no await before this CAS and queues the acknowledgement atomically."""
        from heterodyne.admind.daemon import canonical_ref

        with self.store.transaction():
            if self.eligible(a) != proof or self.select(proof.token, notice) != (a, proof):
                return False
            before = self.store.ask(a.ask_id)
            status = "blocked" if blocked else "cancelled"
            if not self.store.close_attempt(
                a.ask_id,
                a.message_id,
                expect_status="deciding",
                settled="blocked" if blocked else "untouched",
                exit_status=75,
                new_status=status,
                outcome=text if blocked else "maintenance-retired",
                decided_by=operator,
            ):
                return False
            body = canonical(
                {
                    "version": 1,
                    "attempt": asdict(a),
                    "proof": json.loads(proof.body),
                    "proof_sha256": proof.sha256,
                    "command_id": command,
                    "sender": sender,
                    "operator": operator,
                    "thread": notice,
                    "ref": "marmot:" + canonical_ref(command),
                    "before": None if before is None else before.outcome,
                    "after": status,
                    "native_sha256": digest(native),
                    "reason": reason,
                }
            )
            if len(body.encode("utf-8")) > 16384:
                raise ValueError("retirement receipt capacity; original retained")
            self.store.db.execute(
                "INSERT INTO maintenance_retirements VALUES (?,?,?,?,?,?)",
                (a.ask_id, a.message_id, command, proof.token, notice, body),
            )
            self.store.db.execute(
                "DELETE FROM maintenance_proofs WHERE ask_id=? AND message_id=?", (a.ask_id, a.message_id)
            )
            self.store.set_inbound(a.message_id, "done")
            self.store.set_inbound(command, "done")
            return True


def publish_notice(daemon: "Admind", a: Attempt, *, refresh: bool = False) -> None:
    """One current selector mapping. Automatic retries never allocate per-generation notices.

    An explicit !asks inspection may publish a fresh selector after a later invocation, as may the
    transition to a suspended hold. A stale previously delivered selector never selects the new proof.
    """
    proof = daemon.store.retirement.eligible(a)
    if proof is None:
        daemon.ask_notice(
            a.ask_id,
            "retirement-unavailable",
            a.message_id,
            f"Ask {a.ask_id}, attempt {display_attempt(a.message_id)}: retirement unavailable: "
            "complete since-begin not-issued proof is missing. History is preserved.",
        )
        return
    row = daemon.store.db.execute(
        "SELECT n.sha256,n.outbox_key,o.seq,o.status,o.message_id "
        "FROM maintenance_recovery_notices n LEFT JOIN outbox o ON o.key=n.outbox_key "
        "WHERE n.ask_id=? AND n.message_id=?",
        (a.ask_id, a.message_id),
    ).fetchone()
    if row is not None:
        usable = row[3] == "pending" or (row[3] == "sent" and row[4] is not None)
        if not refresh or (row[0] == proof.sha256 and usable):
            return
    key = f"retirement:{a.ask_id}:{proof.token}"
    if row is not None and row[0] == proof.sha256:
        # Explicit inspection repairs delivery, never an automatic replay of ambiguous delivery.
        # The old outbox row/history stays immutable; a new notice has its own transport identity.
        key += f":repair:{row[2]}"
    # Single bounded notice. System provenance permits current operators to see it after the original
    # sender is removed; it grants no sender privilege and still passes the normal outbound group gate.
    text = (
        f"Ask {a.ask_id}: held {a.action} attempt {display_attempt(a.message_id)}. "
        f"Reply directly to this notice: !asks retire {proof.token}. "
        "This retires only the proven not-issued local attempt; it makes no native decision."
    )
    daemon.post(key, text, daemon.attempt_thread(a.ask_id, a.message_id), owner=None)
    daemon.store.retirement.bind_notice(proof, key)


async def retire(daemon: "Admind", command: str, token: str, target: str | None) -> str | None:
    """Authenticated command under the daemon's existing work_lock; no native mutation or child retry."""
    import msgspec

    from heterodyne.admind.approvals import Readout, settle

    store = daemon.store
    if not daemon.authorised(command):
        daemon.deny(command, "retirement")
        return
    inbound = store.inbound_thread(command)
    if (
        inbound != target
        or target is None
        or len(token) != 24
        or any(c not in "0123456789abcdef" for c in token)
    ):
        daemon.finish(
            command,
            "done",
            "Retirement refused: direct verified held-notice reply required.",
            "retirement-refused",
        )
        return
    previous = store.retirement.receipt(token, target)
    if previous is not None:
        # Inbound dedup normally intercepts redelivery; direct recovery calls remain idempotent too.
        if json.loads(previous)["command_id"] != command:
            daemon.finish(
                command,
                "done",
                "This exact local attempt was already retired; receipt "
                + digest(previous)[:24]
                + ". History retained.",
                "retirement-existing",
            )
        return previous
    selected = store.retirement.select(token, target)
    if selected is None:
        daemon.finish(
            command,
            "done",
            "Retirement refused: reply to the current eligible held notice "
            "with its exact proof token. No decision was changed.",
            "retirement-refused",
        )
        return
    a, proof = selected
    row = store.ask(a.ask_id)
    ab = daemon.approve_bead
    if row is None or row.bead is None or ab is None:
        daemon.finish(
            command, "done", "Retirement held: native read unavailable; original retained.", "retirement-held"
        )
        return
    # The read uses the same captured adapter identity as the exact HOLD. A changed source must not
    # silently supply a different interpretation of the earlier not-issued proof.
    identity = json.loads(proof.body)["adapter_identity"]
    if await asyncio.to_thread(ab.identity) != identity:
        daemon.finish(
            command, "done", "Retirement held: native adapter changed; original retained.", "retirement-held"
        )
        return
    try:
        back = await ab.read(row.bead)
    except Exception:  # noqa: BLE001 - bounded refusal; no absence is inferred from transport failure
        back = None
    if (
        not isinstance(back, Readout)
        or back.busy
        or back.id != row.bead
        or await asyncio.to_thread(ab.identity) != identity
    ):
        daemon.finish(
            command, "done", "Retirement held: no current native read; original retained.", "retirement-held"
        )
        return
    if not daemon.authorised(command):
        daemon.deny(command, "retirement")
        return
    # Current native decisions matching the original follow ordinary reconciliation, not retirement.
    result = settle(back, a)
    if result == "recorded":
        if store.retirement.eligible(a) != proof:
            daemon.finish(
                command, "done", "Retirement held: attempt changed; original retained.", "retirement-held"
            )
            return
        daemon.settle_maintenance_recorded(a, row, back)
        daemon.finish(
            command,
            "done",
            "Original native decision reconciled; no retirement was made.",
            "retirement-reconciled",
        )
        return
    blocked = result != "untouched" or not back.kind_approval
    reason = (
        "not-approval"
        if not back.kind_approval
        else "native-conflict"
        if back.decided
        else "not-open"
        if back.status != "open"
        else "maintenance-retired"
    )
    text = (
        daemon.settlement("blocked", back, a, row.bead, "")[1]
        if blocked
        else "Proven not-issued local attempt retired; no native decision changed."
    )
    native = canonical(msgspec.to_builtins(back))
    sender = store.inbound_sender(command)
    # No await from this fresh authority check through the final Store/outbox commit.
    if sender is None or not daemon.authorised(command):
        daemon.deny(command, "retirement")
        return
    operator = daemon.operator_name(command)
    with store.transaction():
        if not store.retirement.commit(
            a,
            proof,
            command=command,
            sender=sender,
            operator=operator,
            notice=target or "",
            native=native,
            blocked=blocked,
            text=text,
            reason=reason,
        ):
            daemon.finish(
                command, "done", "Retirement held: attempt changed; original retained.", "retirement-held"
            )
            return
        daemon.post(
            f"retired:{a.ask_id}:{a.message_id}",
            f"Ask {a.ask_id}: original {a.action} by {a.operator}; local attempt retired by "
            f"{operator}. {text}",
            target,
            owner=daemon.request_owners(command),
        )
    daemon.audit_quietly(
        "ask",
        action="maintenance-retired",
        ask_id=a.ask_id,
        message_id=command,
        outcome="blocked" if blocked else "cancelled",
    )
