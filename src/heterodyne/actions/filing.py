"""P3 disposable owner: durable intents, no default transport or live authority.

Observations are explicit trusted fixture-host attestations, never authentication.
An accepted refreshed reciprocal capsule is a prerequisite for a real composition.
This module provides no CLI, native client, permission Allow, or production factory.
"""

import hashlib
import os
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Protocol, cast

import msgspec

from heterodyne.actions import judgement as j
from heterodyne.actions import requests as r
from heterodyne.actions.codec import BEAD_ID, safe_label
from heterodyne.actions.contracts import Clock, Intent, Origin, Record, Resolved
from heterodyne.actions.decisions import Decision
from heterodyne.actions.dispatch import Dispatch
from heterodyne.actions.judgement import Invocation, Provider, Reply
from heterodyne.gatekeeper import contracts as g

Operation = Literal["allocate", "lookup", "blocker", "park", "land", "groom", "file", "withdraw", "provider"]
State = Literal["prepared", "issuing", "observed", "refused-unissued", "uncertain"]
MAX_REQUESTS = 4096
MAX_EVENTS = 16384
RESERVATION = 161
TERMINAL = {"completed", "denied", "cancelled", "reconciled-abandoned"}


SCHEMA_SQL = (
    "PRAGMA synchronous=FULL; PRAGMA user_version=1;"
    "CREATE TABLE requests(k TEXT PRIMARY KEY,ws TEXT,bead TEXT,active INTEGER,data BLOB);"
    "CREATE TABLE commands(id TEXT PRIMARY KEY,k TEXT,operation TEXT,state TEXT,data BLOB,"
    "result BLOB,clock BLOB,lookups INTEGER NOT NULL DEFAULT 0);"
    "CREATE TABLE events(seq INTEGER PRIMARY KEY,k TEXT,kind TEXT,data BLOB);"
    "CREATE TABLE details(k TEXT,id TEXT,data BLOB,PRIMARY KEY(k,id));"
    "CREATE TABLE assessments(id TEXT PRIMARY KEY,receipt BLOB);"
    "CREATE TABLE grooming(k TEXT PRIMARY KEY,data BLOB);"
    "CREATE TABLE groom_plans(id TEXT PRIMARY KEY,data BLOB);"
)


class Held(RuntimeError):
    """Safe fixed diagnostic; unknown effects retain the reservation."""


class BeforeIssue(Held):
    """Only an adapter's proven pre-effect failure can use this outcome."""


class Command(Record, frozen=True):
    command_id: str
    operation: Operation
    request: r.Request
    snapshot_sha256: str
    policy_revision: str
    clock: Clock
    payload: bytes = b""


class ProviderResult(Record, frozen=True):
    command_sha256: str
    invocation: Invocation
    reply: Reply
    start: Clock
    end: Clock


class Observation(Record, frozen=True):
    command_id: str
    snapshot_sha256: str
    policy_revision: str
    origin: Origin
    source: str
    outcome: Literal["observed", "definitely-unissued", "uncertain", "unavailable"]
    shell_ids: tuple[str, ...] = ()
    approval_id: str | None = None
    claim_holder: str | None = None
    blocked: bool = False
    parked: bool = False
    retained_claim: bool = False
    native_readback: bool = False
    whole_ask: bytes | None = None
    context_digest: str | None = None
    revision: int | None = None
    blockers_clear: bool = False
    native_terminal: bool = False
    groomed_source: str | None = None
    groomed_origin: Origin | None = None
    reducer_request: bytes | None = None
    blocked_bead: str | None = None
    blocker_ids: tuple[str, ...] = ()
    stopped_sessions: tuple[str, ...] = ()
    landed_repository: str | None = None
    landed_source: str | None = None
    landed_tree: str | None = None


class DetailOverflow(Record, frozen=True):
    count: int = 0
    samples: tuple[str, ...] = ()
    samples_sha256: str = ""


class Landing(Record, frozen=True):
    repository: str
    source: str
    tree: str


class GroomPlan(Record, frozen=True):
    after: r.Request
    advice: j.GroomState
    provider_id: str
    capability: Literal["plan5-origin-groom-v1"] = "plan5-origin-groom-v1"


class Cleanup(Record, frozen=True):
    phase: Literal["completed", "denied", "cancelled", "reconciled-abandoned"]
    decision: Decision | None = None
    dispatch: Dispatch | None = None
    cancellation: r.CancellationRecovery | None = None
    abandonment: r.AbandonmentRecovery | None = None


class Current(Record, frozen=True):
    """Independent fixture-host read; no native authentication claim."""

    command_id: str
    snapshot_sha256: str
    policy_revision: str
    origin: Origin
    source: str
    claim_holder: str
    authority_current: bool
    repository_current: bool


class Ports(Protocol):
    """No default implementation. Whole commands, never ad-hoc mutation arguments."""

    fixture_only: bool

    def current(self, command: Command) -> Current: ...

    def invoke(self, command: Command) -> Observation: ...

    def read(self, command: Command) -> Observation: ...


def encoded(value: object) -> bytes:
    return msgspec.json.encode(value)


def digest(value: object) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def key(value: r.Request) -> str:
    return digest(value.key)


def decode(raw: bytes, kind: type[r.Request]) -> r.Request:
    try:
        return msgspec.json.decode(raw, type=kind, strict=True)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        raise Held("journal-record-invalid") from exc


class FilingJournal:
    """Explicit disposable fixture DB, schema independent of the accepted P1 DB.

    One SQLite write transaction commits each intent/issuance/result. No writer
    transaction crosses a port/provider call. Issuing records survive process death;
    only exact command-correlated readback can advance them, never effect replay.
    """

    def __init__(
        self, db: sqlite3.Connection, clock: Callable[[], Clock], nonce: Callable[[], str], ports: Ports
    ) -> None:
        self.db, self.clock, self.nonce, self.ports = db, clock, nonce, ports

    @classmethod
    def create(
        cls, path: Path, *, clock: Callable[[], Clock], nonce: Callable[[], str], ports: Ports | None
    ) -> "FilingJournal":
        cls._capability(ports)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, isolation_level=None, timeout=1)
        db.executescript(SCHEMA_SQL)
        if ports is None:
            raise Held("filing-capability-unavailable")
        return cls(db, clock, nonce, ports)

    @staticmethod
    def _capability(ports: Ports | None) -> None:
        if ports is None or ports.fixture_only is not True:
            raise Held("filing-capability-unavailable")

    @classmethod
    def open(
        cls, path: Path, *, clock: Callable[[], Clock], nonce: Callable[[], str], ports: Ports
    ) -> "FilingJournal":
        cls._capability(ports)
        st = path.lstat()
        if (
            not path.is_file()
            or path.is_symlink()
            or st.st_uid != os.getuid()
            or st.st_mode & 0o077
            or st.st_nlink != 1
        ):
            raise Held("journal-file-refused")
        db = sqlite3.connect(f"file:{path}?mode=rw", uri=True, isolation_level=None, timeout=1)
        db.execute("PRAGMA synchronous=FULL")
        reference = sqlite3.connect(":memory:")
        try:
            reference.executescript(SCHEMA_SQL)
            query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            compatible = db.execute(query).fetchall() == reference.execute(query).fetchall()
        finally:
            reference.close()
        if db.execute("PRAGMA user_version").fetchone()[0] != 1 or not compatible:
            db.close()
            raise Held("journal-schema-refused")
        return cls(db, clock, nonce, ports)

    @contextmanager
    def transaction(self) -> Generator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def sample(self) -> Clock:
        value = self.clock()
        if (
            type(value) is not Clock
            or not safe_label(value.boot_id, 128)
            or type(value.mono_ns) is not int
            or value.mono_ns < 0
            or type(value.utc_ns) is not int
            or value.utc_ns < 0
        ):
            raise Held("journal-clock-refused")
        return value

    def append_event(self, request_key: str, kind: str, data: bytes) -> None:
        count = self.db.execute("SELECT count(*) FROM events WHERE k=?", (request_key,)).fetchone()[0]
        # 128 lifecycle slots plus bounded details/overflow; NEVER reuse or prune.
        lifecycle = self.db.execute(
            "SELECT count(*) FROM events WHERE k=? AND kind!='detail'", (request_key,)
        ).fetchone()[0]
        if count >= RESERVATION or (kind != "detail" and lifecycle >= 128):
            raise Held("request-event-capacity-held")
        self.db.execute("INSERT INTO events(k,kind,data) VALUES(?,?,?)", (request_key, kind, data))

    def reserve(self, request: r.Request) -> None:
        if type(request) is not r.Request or request != r.new_request(
            request.origin,
            request.key.sequence,
            request.intent,
            request.resolved,
            request.config,
            request.requested_source,
            prior_denials=request.prior_denials,
        ):
            raise Held("initial-request-refused")
        with self.transaction():
            prior = self.db.execute("SELECT data FROM requests WHERE k=?", (key(request),)).fetchone()
            if prior:
                if bytes(prior[0]) != encoded(request):
                    raise Held("creation-key-conflict")
                return
            count = self.db.execute("SELECT count(*) FROM requests").fetchone()[0]
            outstanding = self.db.execute("SELECT count(*) FROM requests WHERE active=1").fetchone()[0]
            bead = self.db.execute(
                "SELECT count(*) FROM requests WHERE active=1 AND ws=? AND bead=?",
                (request.key.workstream, request.key.bead),
            ).fetchone()[0]
            retained = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
            reserved = self.db.execute(
                "SELECT coalesce(sum(161 - (SELECT count(*) FROM events WHERE events.k=requests.k)),0) "
                "FROM requests WHERE active=1"
            ).fetchone()[0]
            if (
                count >= MAX_REQUESTS
                or retained + reserved + RESERVATION > MAX_EVENTS
                or outstanding >= 64
                or bead >= 2
            ):
                raise Held("request-capacity-held")
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?)",
                (key(request), request.key.workstream, request.key.bead, 1, encoded(request)),
            )
            self.append_event(key(request), "reservation", encoded(request))

    def request(self, request_key: str) -> r.Request:
        row = self.db.execute("SELECT data FROM requests WHERE k=?", (request_key,)).fetchone()
        if row is None:
            raise Held("request-missing")
        return decode(bytes(row[0]), r.Request)

    def _save(self, before: r.Request, after: r.Request) -> None:
        if self.request(key(before)) != before:
            raise Held("request-cas-refused")
        if after.key != before.key or after.version != before.version + 1:
            raise Held("request-transition-refused")
        self.db.execute(
            "UPDATE requests SET data=?,active=? WHERE k=?",
            (encoded(after), int(after.phase not in TERMINAL), key(before)),
        )

    def prepare(self, request_key: str, operation: Operation, policy: str, payload: bytes = b"") -> Command:
        if (
            operation not in {"allocate", "blocker", "park", "land", "groom", "file", "withdraw", "provider"}
            or not r.digest_syntax(policy)
            or type(payload) is not bytes
            or len(payload) > 131072
        ):
            raise Held("command-input-refused")
        with self.transaction():
            request = self.request(request_key)
            pending = self.db.execute(
                "SELECT id,operation,state FROM commands WHERE k=? "
                "AND (state IN ('prepared','issuing','uncertain') OR "
                "(operation='provider' AND state='observed' AND NOT EXISTS "
                "(SELECT 1 FROM assessments WHERE assessments.id=commands.id)))",
                (request_key,),
            ).fetchone()
            recoverable = (
                operation == "withdraw"
                and pending is not None
                and pending[1] != "withdraw"
                and pending[2] == "uncertain"
            )
            if (pending and not recoverable) or request.phase in TERMINAL:
                raise Held("command-pending-held")
            phases = {
                "allocate": {"allocation-intent"},
                "blocker": {"allocated"},
                "park": {"blocker-pending"},
                "land": {"parked"},
                "groom": {"ask-pinned", "sufficient"},
                "file": {"sufficient"},
                "provider": {"ask-pinned"},
                "withdraw": {
                    "allocation-unknown",
                    "allocated",
                    "blocker-pending",
                    "parked",
                    "landed",
                    "ask-pinned",
                    "sufficient",
                    "filed",
                },
            }
            if request.phase not in phases[operation]:
                raise Held("command-phase-refused")
            used = self.db.execute(
                "SELECT count(*) FROM events WHERE k=? AND kind!='detail'", (request_key,)
            ).fetchone()[0]
            if used > (124 if operation == "withdraw" else 116):
                raise Held("terminal-lifecycle-reserved")
            clock = self.sample()
            nonce = self.nonce()
            if (
                type(nonce) is not str
                or len(nonce) != 32
                or any(ch not in "0123456789abcdef" for ch in nonce)
            ):
                raise Held("nonce-refused")
            command = Command(nonce, operation, request, digest(request), policy, clock, payload)
            self.db.execute(
                "INSERT INTO commands(id,k,operation,state,data,result,clock) VALUES(?,?,?,?,?,?,?)",
                (nonce, request_key, operation, "prepared", encoded(command), None, encoded(clock)),
            )
            self.append_event(request_key, "prepared", encoded(command))
            return command

    def command(self, command_id: str) -> tuple[Command, State]:
        row = self.db.execute("SELECT data,state FROM commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            raise Held("command-missing")
        command = msgspec.json.decode(bytes(row[0]), type=Command)
        return command, cast(State, row[1])

    def issue(self, command_id: str, current: Current) -> Command:
        with self.transaction():
            command, state = self.command(command_id)
            if state != "prepared" or self.request(key(command.request)) != command.request:
                raise Held("command-issue-refused")
            if (
                type(current) is not Current
                or current.command_id != command_id
                or current.snapshot_sha256 != command.snapshot_sha256
                or current.policy_revision != command.policy_revision
                or current.origin != command.request.origin
                or current.source != command.request.requested_source
                or current.claim_holder != command.request.origin.session_key
                or current.authority_current is not True
                or current.repository_current is not True
            ):
                raise Held("current-observation-held")
            if command.operation == "land":
                self._landing(command)
            if command.operation == "groom":
                plan = self.db.execute("SELECT data FROM groom_plans WHERE id=?", (command_id,)).fetchone()
                if plan is None or bytes(plan[0]) != command.payload:
                    raise Held("groom-owner-required")
            now = self.sample()
            if now.boot_id != command.clock.boot_id or now.mono_ns < command.clock.mono_ns:
                raise Held("command-clock-held")
            if command.operation == "allocate":
                issued_request = r.issue_allocation(command.request)
                self._save(command.request, issued_request)
                command = msgspec.structs.replace(
                    command, request=issued_request, snapshot_sha256=digest(issued_request)
                )
            self.db.execute(
                "UPDATE commands SET state='issuing',data=? WHERE id=?", (encoded(command), command_id)
            )
            self.append_event(key(command.request), "issuing", encoded((now, current)))
        return command

    def mark_unknown(self, command: Command) -> None:
        with self.transaction():
            self.db.execute(
                "UPDATE commands SET state='uncertain' WHERE id=? AND state='issuing'", (command.command_id,)
            )

    def execute(self, command_id: str) -> Observation:
        prepared, _ = self.command(command_id)
        current = self.ports.current(prepared)
        command = self.issue(command_id, current)
        try:
            result = self.ports.invoke(command)
        except BeforeIssue:
            result = Observation(
                command.command_id,
                command.snapshot_sha256,
                command.policy_revision,
                command.request.origin,
                command.request.requested_source,
                "definitely-unissued",
            )
            with self.transaction():
                if self.command(command_id) != (command, "issuing"):
                    raise Held("preissue-refusal-state-held") from None
                self.db.execute(
                    "UPDATE commands SET state='refused-unissued',result=? WHERE id=?",
                    (encoded(result), command_id),
                )
                self.append_event(key(command.request), "preissue-refusal", encoded(result))
            return result
        except BaseException:
            self.mark_unknown(command)
            raise
        try:
            self.observe(command_id, result)
        except BaseException:
            self.mark_unknown(command)
            raise
        return result

    def reconcile(self, command_id: str) -> Observation:
        with self.transaction():
            command, state = self.command(command_id)
            if state not in {"issuing", "uncertain"}:
                raise Held("reconcile-state-refused")
            if command.operation == "allocate":
                row = self.db.execute(
                    "SELECT lookups,clock FROM commands WHERE id=?", (command_id,)
                ).fetchone()
                count = int(row[0])
                anchor = msgspec.json.decode(bytes(row[1]), type=Clock)
                now = self.sample()
                if (
                    count >= 3
                    or now.boot_id != anchor.boot_id
                    or now.mono_ns - anchor.mono_ns < (1, 5, 30)[count] * 1_000_000_000
                ):
                    raise Held("lookup-deadline-held")
                # Count and spaced anchor durable BEFORE the read. Failure is not absence.
                self.db.execute(
                    "UPDATE commands SET lookups=?,clock=? WHERE id=?", (count + 1, encoded(now), command_id)
                )
                # Consume the actual P1 lookup allowance before the read, including failures.
                before = command.request
                after = r.observe_allocation(before, (), lookup_ok=False, elapsed_seconds=(1, 5, 30)[count])
                self._save(before, after)
                command = msgspec.structs.replace(command, request=after, snapshot_sha256=digest(after))
                self.db.execute("UPDATE commands SET data=? WHERE id=?", (encoded(command), command_id))
                self.append_event(key(command.request), "lookup", encoded(now))
        result = self.ports.read(command)
        self.observe(command_id, result)
        return result

    def _correlated(self, command: Command, observation: Observation) -> None:
        try:
            raw = encoded(observation)
            if len(raw) > 131072:
                raise Held("observation-size-refused")
            msgspec.json.decode(raw, type=Observation, strict=True)
        except (msgspec.DecodeError, TypeError, ValueError) as exc:
            raise Held("observation-type-refused") from exc
        if (
            type(observation) is not Observation
            or observation.command_id != command.command_id
            or observation.snapshot_sha256 != command.snapshot_sha256
            or observation.policy_revision != command.policy_revision
            or observation.origin != command.request.origin
            or observation.source != command.request.requested_source
        ):
            raise Held("observation-binding-refused")

    def observe(self, command_id: str, observation: Observation) -> None:
        with self.transaction():
            command, state = self.command(command_id)
            self._correlated(command, observation)
            if state == "observed":
                row = self.db.execute("SELECT result FROM commands WHERE id=?", (command_id,)).fetchone()
                if bytes(row[0]) != encoded(observation):
                    raise Held("observation-replay-conflict")
                return
            if state not in {"issuing", "uncertain"}:
                raise Held("observation-state-refused")
            if self.request(key(command.request)) != command.request:
                raise Held("observation-snapshot-refused")
            outcome: State = "uncertain"
            if observation.outcome == "definitely-unissued":
                # Only execute's immediate BeforeIssue exception can establish this.
                # A read after restart cannot prove that no earlier write happened.
                raise Held("readback-unissued-refused")
            elif observation.outcome == "observed":
                after = self._reduce(command, observation)
                if after != command.request:
                    self._save(command.request, after)
                outcome = (
                    "uncertain"
                    if command.operation == "allocate" and after.phase == "allocation-unknown"
                    else "observed"
                )
                if outcome == "uncertain":
                    command = msgspec.structs.replace(command, request=after, snapshot_sha256=digest(after))
                    self.db.execute("UPDATE commands SET data=? WHERE id=?", (encoded(command), command_id))
            self.db.execute(
                "UPDATE commands SET state=?,result=? WHERE id=?",
                (outcome, encoded(observation), command_id),
            )
            self.append_event(key(command.request), "result", encoded(observation))

    def _reduce(self, command: Command, observation: Observation) -> r.Request:
        old = command.request
        if command.operation == "allocate":
            if len(observation.shell_ids) != 1 or not set(old.shell_ids) <= set(observation.shell_ids):
                ids = tuple(sorted(set(old.shell_ids) | set(observation.shell_ids)))
                if len(ids) > 8 or any(not BEAD_ID.fullmatch(item) for item in ids):
                    raise Held("allocation-shells-held")
                return r.changed(old, shell_ids=ids)
            # A create acknowledgement is not a timed lookup. Reconciliation already
            # consumed its P1 lookup before read; reuse that ordinal without doubling it.
            count = self.db.execute(
                "SELECT lookups FROM commands WHERE id=?", (command.command_id,)
            ).fetchone()[0]
            ordinal = max(0, count - 1)
            candidate = r.observe_allocation(
                msgspec.structs.replace(old, allocation_lookups=ordinal),
                observation.shell_ids,
                lookup_ok=True,
                elapsed_seconds=(1, 5, 30)[ordinal],
            )
            return msgspec.structs.replace(candidate, allocation_lookups=count)
        if command.operation in {"blocker", "park", "land"}:
            if (
                observation.approval_id != old.approval_id
                or not observation.blocked
                or observation.blocked_bead != old.origin.bead
                or observation.blocker_ids != (old.approval_id,)
            ):
                raise Held("blocker-proof-held")
            if command.operation in {"park", "land"} and not (
                observation.parked
                and observation.retained_claim
                and observation.claim_holder == old.origin.session_key
                and observation.stopped_sessions == (old.origin.session_key,)
            ):
                raise Held("park-proof-held")
            if command.operation == "land":
                landing = self._landing(command)
                if (
                    observation.landed_source != landing.source
                    or observation.landed_repository != landing.repository
                    or observation.landed_tree != landing.tree
                ):
                    raise Held("landing-proof-held")
            return r.observe_stage(
                old,
                cast(Literal["blocker", "park", "land"], command.operation),
                key=old.key,
                approval_id=old.approval_id or "",
                origin=old.origin,
                source=old.requested_source,
                readback_ok=True,
            )
        if command.operation == "file":
            if not (
                observation.native_readback
                and observation.blocked
                and observation.blocked_bead == old.origin.bead
                and observation.blocker_ids == (old.approval_id,)
                and observation.stopped_sessions == (old.origin.session_key,)
                and observation.parked
                and observation.retained_claim
                and observation.claim_holder == old.origin.session_key
                and observation.approval_id == old.approval_id
                and observation.whole_ask == old.ask_bytes
                and observation.context_digest == old.context_digest
                and observation.revision == old.request_revision
            ):
                raise Held("file-readback-held")
            return r.observe_filed(
                old,
                digest=observation.context_digest or "",
                revision=observation.revision or 0,
                native_readback=True,
            )
        if command.operation == "groom":
            plan = msgspec.json.decode(command.payload, type=GroomPlan)
            candidate = r.groom(
                old,
                plan.after.intent,
                plan.after.resolved,
                plan.after.requested_source,
                origin=plan.after.origin,
            )
            if (
                candidate != plan.after
                or candidate.requested_source == old.requested_source
                or observation.groomed_source != candidate.requested_source
                or observation.groomed_origin != candidate.origin
                or not observation.parked
                or not observation.retained_claim
                or observation.claim_holder != old.origin.session_key
                or not observation.blocked
                or observation.approval_id != old.approval_id
                or observation.blocked_bead != old.origin.bead
                or observation.blocker_ids != (old.approval_id,)
                or observation.stopped_sessions != (old.origin.session_key,)
            ):
                raise Held("groom-readback-held")
            # Only the owning prepare_groom path may create this bounded advice.
            prior = self.db.execute(
                "SELECT receipt FROM assessments WHERE id=?", (plan.provider_id,)
            ).fetchone()
            if (
                prior is None
                or not plan.advice.receipts
                or encoded(plan.advice.receipts[-1]) != bytes(prior[0])
            ):
                raise Held("groom-advice-held")
            self.db.execute(
                "INSERT INTO grooming VALUES(?,?) ON CONFLICT(k) DO UPDATE SET data=excluded.data",
                (key(old), encoded(plan.advice)),
            )
            return candidate
        if command.operation == "withdraw":
            cleanup = msgspec.json.decode(command.payload, type=Cleanup)
            if (
                cleanup.cancellation is not None
                and cleanup.abandonment is not None
                or cleanup.cancellation is not None
                and cleanup.phase != "cancelled"
                or cleanup.abandonment is not None
                and cleanup.phase != "reconciled-abandoned"
            ):
                raise Held("cleanup-recovery-shape-refused")
            candidate = r.settle_request(
                old,
                cleanup.phase,
                key=old.key,
                origin=old.origin,
                cleaned_shells=observation.shell_ids,
                blockers_clear=observation.blockers_clear,
                native_terminal=observation.native_terminal,
                decision=cleanup.decision,
                dispatch=cleanup.dispatch,
                recovery=cleanup.cancellation or cleanup.abandonment,
            )
            if observation.reducer_request != encoded(candidate):
                raise Held("cleanup-reducer-proof-held")
            return candidate
        raise Held("operation-capability-unavailable")

    def pin(self, request_key: str, whole_ask: bytes, context: str) -> r.Request:
        with self.transaction():
            old = self.request(request_key)
            self._idle(old)
            after = r.pin_ask(old, whole_ask, context, revision=old.request_revision)
            self._save(old, after)
            self.append_event(request_key, "pin", encoded(after))
            return after

    def _landing(self, command: Command) -> Landing:
        try:
            value = msgspec.json.decode(command.payload, type=Landing, strict=True)
        except (msgspec.DecodeError, TypeError, ValueError) as exc:
            raise Held("landing-plan-required") from exc
        if (
            value.repository != command.request.origin.repo_alias
            or value.source != command.request.requested_source
            or not r.source_syntax(value.tree)
        ):
            raise Held("landing-plan-required")
        return value

    def prepare_landing(self, request_key: str, policy: str, *, tree: str) -> Command:
        before = self.request(request_key)
        if not r.source_syntax(tree):
            raise Held("landing-tree-refused")
        return self.prepare(
            request_key,
            "land",
            policy,
            encoded(Landing(before.origin.repo_alias, before.requested_source, tree)),
        )

    def prepare_groom(
        self,
        command_id: str,
        request: object,
        privacy: g.PrivacyContext,
        model: j.ModelSpec,
        intent: Intent,
        resolved: Resolved,
        source: str,
        *,
        origin: Origin,
    ) -> Command:
        command, _ = self.command(command_id)
        if not isinstance(self.projection(command_id, request, privacy, model), g.NeedsGrooming):
            raise Held("groom-advice-required")
        before = self.request(key(command.request))
        row = self.db.execute("SELECT data FROM grooming WHERE k=?", (key(before),)).fetchone()
        state = msgspec.json.decode(bytes(row[0]), type=j.GroomState) if row else j.GroomState(before.origin)
        receipt_row = self.db.execute("SELECT receipt FROM assessments WHERE id=?", (command_id,)).fetchone()
        receipt = msgspec.json.decode(bytes(receipt_row[0]), type=j.Receipt)
        advice = j.record_groom(
            state, receipt, before, request, privacy, model, boot_id=command.clock.boot_id
        )
        after = j.groom_request(
            advice,
            receipt,
            before,
            request,
            privacy,
            model,
            boot_id=command.clock.boot_id,
            intent=intent,
            resolved=resolved,
            source=source,
            origin=origin,
        )
        prepared = self.prepare(
            key(before), "groom", command.policy_revision, encoded(GroomPlan(after, advice, command_id))
        )
        with self.transaction():
            self.db.execute("INSERT INTO groom_plans VALUES(?,?)", (prepared.command_id, prepared.payload))
        return prepared

    def prepare_cleanup(self, request_key: str, policy: str, cleanup: Cleanup) -> Command:
        # Actual Q1 withdrawal + actual P1 returned terminal Request are both required.
        return self.prepare(request_key, "withdraw", policy, encoded(cleanup))

    def _idle(self, request: r.Request) -> None:
        if self.db.execute(
            "SELECT 1 FROM commands WHERE k=? AND "
            "(state IN ('prepared','issuing','uncertain') OR "
            "(operation='provider' AND state='observed' AND NOT EXISTS "
            "(SELECT 1 FROM assessments WHERE assessments.id=commands.id)))",
            (key(request),),
        ).fetchone():
            raise Held("command-pending-held")

    async def assess(
        self,
        request_key: str,
        request: object,
        privacy: g.PrivacyContext,
        model: j.ModelSpec,
        provider: Provider,
        *,
        policy_revision: str,
    ) -> tuple[str, j.Receipt]:
        before = self.request(request_key)
        # Bind/validate before allocating a nonce or starting a command.
        j.bind(before, request, privacy, model)
        command = self.prepare(request_key, "provider", policy_revision)
        owned = DurableProvider(self, command, provider)
        receipt = await j.assess(
            before,
            request,
            privacy,
            model,
            owned,
            clock=owned.assessment_clock,
            boot_id=command.clock.boot_id,
            nonce=command.command_id,
        )
        with self.transaction():
            completed, state = self.command(command.command_id)
            if state != "observed" or self.request(request_key) != before:
                raise Held("provider-outcome-unknown")
            DurableProvider.verify_receipt(self, completed, receipt)
            # Receipt includes end/deadline and the complete actual reply, before any projection.
            self.db.execute("INSERT INTO assessments VALUES(?,?)", (command.command_id, encoded(receipt)))
            self.append_event(request_key, "assessment", encoded(receipt))
        return command.command_id, receipt

    def projection(
        self,
        command_id: str,
        request: object,
        privacy: g.PrivacyContext,
        model: j.ModelSpec,
    ) -> g.Projection:
        command, state = self.command(command_id)
        row = self.db.execute("SELECT receipt FROM assessments WHERE id=?", (command_id,)).fetchone()
        if state != "observed" or row is None or self.request(key(command.request)) != command.request:
            raise Held("provider-receipt-held")
        receipt = msgspec.json.decode(bytes(row[0]), type=j.Receipt)
        DurableProvider.verify_receipt(self, command, receipt)
        now = self.sample()
        if (
            now.boot_id != command.clock.boot_id
            or now.mono_ns < command.clock.mono_ns
            or now.mono_ns - command.clock.mono_ns >= j.DEADLINE_NS
        ):
            raise Held("provider-receipt-expired")
        return j.current_projection(receipt, command.request, request, privacy, model, boot_id=now.boot_id)

    def sufficient(
        self,
        command_id: str,
        request: object,
        privacy: g.PrivacyContext,
        model: j.ModelSpec,
    ) -> r.Request:
        with self.transaction():
            command, _ = self.command(command_id)
            if not isinstance(self.projection(command_id, request, privacy, model), g.SufficientProposal):
                raise Held("sufficiency-held")
            old = self.request(key(command.request))
            self._idle(old)
            after = r.observe_sufficient(
                old,
                digest=old.context_digest or "",
                policy_revision=command.policy_revision,
                source=old.requested_source,
                sufficient=True,
            )
            self._save(old, after)
            self.append_event(key(old), "sufficient", encoded(after))
            return after

    def record_detail(self, request_key: str, event_id: str, detail: bytes) -> None:
        if (
            not safe_label(event_id, 128)
            or event_id == "overflow"
            or type(detail) is not bytes
            or len(detail) > 1024
        ):
            raise Held("detail-refused")
        with self.transaction():
            self.request(request_key)
            prior = self.db.execute(
                "SELECT data FROM details WHERE k=? AND id=?", (request_key, event_id)
            ).fetchone()
            if prior:
                if bytes(prior[0]) != detail:
                    raise Held("detail-replay-conflict")
                return
            count = self.db.execute("SELECT count(*) FROM details WHERE k=?", (request_key,)).fetchone()[0]
            if count >= 32:
                # One saturated aggregate, at most four hashed samples. No projection
                # work or unlimited-ID deduplication is claimed for overflow traffic.
                aggregate = self.db.execute(
                    "SELECT data FROM details WHERE k=? AND id='overflow'", (request_key,)
                ).fetchone()
                prior_aggregate = (
                    msgspec.json.decode(bytes(aggregate[0]), type=DetailOverflow)
                    if aggregate
                    else DetailOverflow()
                )
                samples = prior_aggregate.samples
                if len(samples) < 4:
                    samples += (digest((event_id, detail)),)
                data = encoded(
                    DetailOverflow(
                        count=min(65535, prior_aggregate.count + 1),
                        samples=samples,
                        samples_sha256=digest(samples),
                    )
                )
                self.db.execute(
                    "INSERT INTO details VALUES(?,?,?) ON CONFLICT(k,id) DO UPDATE SET data=excluded.data",
                    (request_key, "overflow", data),
                )
                if aggregate is None:
                    self.append_event(request_key, "detail", b"overflow")
                return
            self.db.execute("INSERT INTO details VALUES(?,?,?)", (request_key, event_id, detail))
            self.append_event(request_key, "detail", detail)


class DurableProvider:
    """Own the exact P2 invocation before its single underlying provider attempt.

    Owner uses assess(..., nonce=command.command_id, clock=..., boot_id=...). Lost
    replies remain uncertain. There is no automatic reroll or permission allowance.
    """

    def __init__(self, journal: FilingJournal, command: Command, provider: Provider) -> None:
        self.journal, self.command, self.provider = journal, command, provider
        self.started = False
        self.result: ProviderResult | None = None

    def assessment_clock(self) -> int:
        if not self.started:
            self.started = True
            return self.command.clock.mono_ns
        if self.result is None:
            raise j.Refused("provider-end-missing")
        return self.result.end.mono_ns

    @staticmethod
    def verify_receipt(journal: FilingJournal, command: Command, receipt: j.Receipt) -> None:
        row = journal.db.execute("SELECT result FROM commands WHERE id=?", (command.command_id,)).fetchone()
        try:
            if row is None:
                raise Held("provider-result-held")
            result = msgspec.json.decode(bytes(row[0]), type=ProviderResult)
        except (TypeError, ValueError, msgspec.DecodeError) as exc:
            raise Held("provider-result-held") from exc
        if (
            result.command_sha256 != digest(command)
            or encoded(result.invocation) != command.payload
            or result.start != command.clock
            or result.end.boot_id != result.start.boot_id
            or result.end.mono_ns < result.start.mono_ns
            or receipt.attempt is None
            or receipt.attempt.nonce != command.command_id
            or receipt.attempt.started_ns != result.start.mono_ns
            or receipt.attempt.ended_ns != result.end.mono_ns
            or receipt.attempt.boot_id != result.start.boot_id
            or (receipt.reply is not None and receipt.reply != result.reply)
        ):
            raise Held("provider-result-binding-held")

    async def judge(self, invocation: Invocation) -> Reply:
        if self.command.operation != "provider" or invocation.nonce != self.command.command_id:
            raise Held("provider-owner-refused")
        with self.journal.transaction():
            current, state = self.journal.command(self.command.command_id)
            if current != self.command or state != "prepared":
                raise Held("provider-call-once-held")
            # Complete actual public input/model/schema/nonce persisted, not just its digest.
            updated = self.journal.db.execute(
                "UPDATE commands SET data=? WHERE id=? AND state='prepared' AND data=?",
                (
                    encoded(msgspec.structs.replace(current, payload=encoded(invocation))),
                    current.command_id,
                    encoded(current),
                ),
            )
            if updated.rowcount != 1:
                raise Held("provider-input-cas-held")
        prepared, _ = self.journal.command(self.command.command_id)
        current = self.journal.ports.current(prepared)
        issued = self.journal.issue(self.command.command_id, current)
        try:
            reply = await self.provider.judge(invocation)
            if type(reply) is not Reply or type(reply.output) is not bytes or len(reply.output) > 32768:
                raise Held("provider-reply-refused")
        except BaseException:
            self.journal.mark_unknown(issued)
            raise
        # Persist the original end observation with the exact issued input before
        # P2 can construct a receipt. Missing receipt after a crash stays HOLD.
        try:
            end = self.journal.sample()
            if end.boot_id != issued.clock.boot_id or end.mono_ns < issued.clock.mono_ns:
                raise Held("provider-end-clock-held")
            result = ProviderResult(digest(issued), invocation, reply, issued.clock, end)
            with self.journal.transaction():
                if self.journal.request(key(issued.request)) != issued.request:
                    raise Held("provider-request-cas-held")
                updated = self.journal.db.execute(
                    "UPDATE commands SET state='observed',result=? WHERE id=? AND state='issuing' AND data=?",
                    (encoded(result), issued.command_id, encoded(issued)),
                )
                if updated.rowcount != 1:
                    raise Held("provider-result-cas-held")
                self.journal.append_event(key(issued.request), "provider-reply", encoded(result))
            self.result = result
        except BaseException:
            self.journal.mark_unknown(issued)
            raise
        return reply
