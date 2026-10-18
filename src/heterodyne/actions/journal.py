"""Private OFFLINE fixture journal, separate from installed WSD/native stores.

Creation is exclusive, opening checks this module's exact application/schema identity.
No production migration, queue adapter or deployment entrypoint is provided. Native Beads
remain authoritative in future integration: this SQLite journal is never proof of consent.
"""

import contextlib
import hashlib
import os
import sqlite3
import stat
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, overload

import msgspec

from heterodyne.actions.codec import encode_record
from heterodyne.actions.decisions import Decision, begin_decision
from heterodyne.actions.dispatch import (
    DISPATCH_WINDOW_NS,
    PREPARATION_WINDOW_NS,
    Clock,
    Dispatch,
    DispatchRecovery,
    clock_elapsed,
    reconciliation_disposition,
)
from heterodyne.actions.policy import DENIAL_DOMAIN, request_denial_key
from heterodyne.actions.requests import (
    TERMINAL_REQUESTS,
    CancellationRecovery,
    Refused,
    Request,
    request_bytes,
)
from heterodyne.actions.transitions import (
    EMPTY_OBSERVATION,
    TransitionObservation,
    decision_transition,
    dispatch_transition,
    request_transition,
)

APPLICATION_ID = 0x50315031
SCHEMA_VERSION = 2
MAX_OUTSTANDING = 128
MAX_RECORDS = 4096
MAX_EVENTS = 16_384
TERMINAL_RESERVE = 96
_BASE = (
    "CREATE TABLE meta (version INTEGER NOT NULL)",
    "CREATE TABLE requests (key TEXT PRIMARY KEY, ws TEXT NOT NULL, bead TEXT NOT NULL, "
    "version INTEGER NOT NULL, outstanding INTEGER NOT NULL CHECK(outstanding IN (0,1)), data BLOB NOT NULL)",
    "CREATE TABLE events (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, "
    "version INTEGER NOT NULL, data BLOB NOT NULL, UNIQUE(kind,key,version))",
)
_V2 = (
    "CREATE TABLE decisions (key TEXT PRIMARY KEY, version INTEGER NOT NULL, data BLOB NOT NULL)",
    "CREATE TABLE dispatches (key TEXT PRIMARY KEY, version INTEGER NOT NULL, data BLOB NOT NULL)",
    "CREATE TABLE resources (resource TEXT PRIMARY KEY, dispatch_key TEXT NOT NULL)",
    "CREATE TABLE denials (identity TEXT PRIMARY KEY, session_key TEXT NOT NULL, approval TEXT NOT NULL)",
)
TABLES = {
    "request": ("requests", Request),
    "decision": ("decisions", Decision),
    "dispatch": ("dispatches", Dispatch),
}


def _schema(db: sqlite3.Connection) -> set[tuple[str, str, str]]:
    return {
        (row[0], row[1], row[2])
        for row in db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }


def _expected(version: int) -> set[tuple[str, str, str]]:
    with contextlib.closing(sqlite3.connect(":memory:")) as db:
        for statement in (*_BASE, *(_V2 if version == 2 else ())):
            db.execute(statement)
        return _schema(db)


EXPECTED = {version: _expected(version) for version in (1, 2)}


def _checked(db: sqlite3.Connection, *, current: bool = True) -> int:
    if db.execute("PRAGMA application_id").fetchone() != (APPLICATION_ID,):
        raise Refused("not-actions-fixture-journal")
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise Refused("fixture-journal-corrupt")
    try:
        rows = db.execute("SELECT version FROM meta").fetchall()
    except sqlite3.DatabaseError:
        raise Refused("fixture-journal-schema") from None
    if len(rows) != 1 or rows[0][0] not in EXPECTED or _schema(db) != EXPECTED[rows[0][0]]:
        raise Refused("fixture-journal-schema")
    version = rows[0][0]
    if current and version != SCHEMA_VERSION:
        raise Refused("fixture-journal-upgrade-required")
    return version


def request_key(value: Request) -> str:
    # Internal row locator ONLY; not native approval/context digest.
    return hashlib.sha256(encode_record(value.key)).hexdigest()


def dispatch_key(value: Dispatch) -> str:
    return (
        value.approval_id
        if value.dispatch_kind == "initial"
        else value.approval_id + ":reconcile:" + value.attempt_id
    )


def _observation(payload: bytes) -> TransitionObservation:
    """Decode exact old-null compatibility without trusting old elapsed counters."""
    try:
        raw = msgspec.json.decode(payload)
        if type(raw) is dict:
            fields = cast(dict[str, object], raw)
            if (
                "allocation_elapsed_seconds" in fields
                and fields.pop("allocation_elapsed_seconds") is not None
            ):
                raise Refused("legacy-observation-needs-provenance")
        return msgspec.convert(raw, type=TransitionObservation, strict=True)
    except msgspec.DecodeError:
        raise Refused("fixture-observation-corrupt") from None


def _outstanding(value: Request) -> int:
    return int(value.phase not in TERMINAL_REQUESTS or not value.cleanup_confirmed)


def _open_fixture(path: Path) -> sqlite3.Connection:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise Refused("fixture-journal-file")
    return sqlite3.connect(path.absolute().as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=1)


@dataclass(frozen=True)
class ObservedReducer[RecordT: (Request, Decision, Dispatch)]:
    """Use the transaction's sampled observation without predicting its clock."""

    reduce: Callable[[RecordT, TransitionObservation], RecordT]


class FixtureJournal:
    """Tests may open/reopen ONLY private journals created by create(); never an installed journal."""

    def __init__(self, path: Path, *, clock: Callable[[], Clock] | None = None):
        self.clock = clock
        self._capacity_owner: str | None = None
        self._capacity_spent = 0
        self.path = path
        self.db = _open_fixture(path)
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=1000")
            _checked(self.db)
        except BaseException:
            self.db.close()
            raise

    @classmethod
    def create(cls, path: Path, *, clock: Callable[[], Clock] | None = None) -> "FixtureJournal":
        create_fixture(path)
        return cls(path, clock=clock)

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        self._capacity_owner = None
        self._capacity_spent = 0
        try:
            yield
            if self._capacity_spent:
                owner = self._capacity_owner
                if owner is None:
                    raise Refused("fixture-capacity-owner-required")
                version = self.db.execute(
                    "SELECT coalesce(max(version),0)+1 FROM events WHERE kind='capacity-recovery' AND key=?",
                    (owner,),
                ).fetchone()[0]
                self._event("capacity-recovery", owner, version, encode_record(self._capacity_spent + 1))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        finally:
            self._capacity_owner = None
            self._capacity_spent = 0

    def _reserve_used(self, key: str) -> int:
        return sum(
            msgspec.json.decode(row[0], type=int)
            for row in self.db.execute(
                "SELECT data FROM events WHERE kind='capacity-recovery' AND key=?", (key,)
            )
        )

    def _reserved(self) -> int:
        return sum(
            max(0, TERMINAL_RESERVE - self._reserve_used(row[0]))
            for row in self.db.execute("SELECT key FROM requests WHERE outstanding=1")
        )

    def _terminal_budget(self, request: Request) -> None:
        if request.phase not in TERMINAL_REQUESTS:
            self._capacity_owner = request_key(request)

    def _event(self, kind: str, key: str, version: int, data: bytes) -> None:
        next_seq = self.db.execute("SELECT coalesce(max(seq),0)+1 FROM events").fetchone()[0]
        reserved = self._reserved()
        if next_seq > MAX_EVENTS - reserved or self._capacity_spent:
            owner = self._capacity_owner
            if owner is None:
                raise Refused("fixture-event-capacity-needs-recovery")
            remaining = TERMINAL_RESERVE - self._reserve_used(owner)
            pending = self.db.execute("SELECT outstanding FROM requests WHERE key=?", (owner,)).fetchone()
            other_reserves = reserved - (remaining if pending and pending[0] else 0)
            # Leave room for the durable budget-usage event as well as this event.
            required = self._capacity_spent + 1 + int(kind != "capacity-recovery")
            if (
                required > remaining
                or next_seq + int(kind != "capacity-recovery") > MAX_EVENTS - other_reserves
            ):
                raise Refused("fixture-terminal-capacity-budget-exhausted")
            self._capacity_spent += 1
        if next_seq > MAX_EVENTS:
            raise Refused("fixture-event-capacity-needs-recovery")
        self.db.execute(
            "INSERT INTO events(kind,key,version,data) VALUES(?,?,?,?)", (kind, key, version, data)
        )

    @overload
    def get(self, kind: Literal["request"], key: str) -> Request: ...

    @overload
    def get(self, kind: Literal["decision"], key: str) -> Decision: ...

    @overload
    def get(self, kind: Literal["dispatch"], key: str) -> Dispatch: ...

    def get(
        self, kind: Literal["request", "decision", "dispatch"], key: str
    ) -> Request | Decision | Dispatch:
        _, cls = TABLES[kind]
        query = {
            "request": "SELECT data FROM requests WHERE key=?",
            "decision": "SELECT data FROM decisions WHERE key=?",
            "dispatch": "SELECT data FROM dispatches WHERE key=?",
        }[kind]
        row = self.db.execute(query, (key,)).fetchone()
        if row is None:
            raise Refused("fixture-record-missing-decision-or-request")
        try:
            value = msgspec.json.decode(row[0], type=cls)
            if isinstance(value, Request) and value.denial_domain != DENIAL_DOMAIN:
                raise Refused("legacy-denial-domain-needs-provenance")
            return value
        except msgspec.DecodeError:
            raise Refused("fixture-record-corrupt") from None

    def add_request(self, value: Request) -> str:
        key, data = request_key(value), request_bytes(value)
        if value.denial_domain != DENIAL_DOMAIN:
            raise Refused("legacy-denial-domain-needs-provenance")
        if value.version != 0 or value.phase != "allocation-intent":
            raise Refused("request-not-initial")
        with self.transaction():
            row = self.db.execute("SELECT data FROM requests WHERE key=?", (key,)).fetchone()
            if row is not None:
                original = self.db.execute(
                    "SELECT data FROM events WHERE kind='request' AND key=? AND version=0", (key,)
                ).fetchone()
                if original is None or bytes(original[0]) != data:
                    raise Refused("creation-key-conflict")
                return key
            if value.denial_identity != request_denial_key(
                value.intent, value.origin, value.resolved, value.config
            ):
                raise Refused("denial-identity-not-derived")
            if self.denied(value.denial_identity or "", value.origin.session_key):
                raise Refused("same-session-denied-intent")
            history = tuple(
                row[0]
                for row in self.db.execute(
                    "SELECT identity FROM denials WHERE session_key=? ORDER BY identity",
                    (value.origin.session_key,),
                )
            )
            if len(history) > 64 or not set(history).issubset(value.prior_denials):
                raise Refused("denial-history-required")
            if self.db.execute("SELECT count(*) FROM requests").fetchone()[0] >= MAX_RECORDS:
                raise Refused("fixture-record-capacity-needs-recovery")
            if (
                self.db.execute("SELECT count(*) FROM requests WHERE outstanding=1").fetchone()[0]
                >= MAX_OUTSTANDING
            ):
                raise Refused("global-pending-capacity")
            count = self.db.execute(
                "SELECT count(*) FROM requests WHERE ws=? AND bead=? AND outstanding=1",
                (value.origin.workstream, value.origin.bead),
            ).fetchone()[0]
            if count >= 2:
                raise Refused("two-outstanding-per-bead")
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?)",
                (key, value.origin.workstream, value.origin.bead, value.version, 1, data),
            )
            self._event("request", key, value.version, data)
        return key

    def _requests_for_approvals(self, approvals: set[str]) -> dict[str, Request]:
        """One bounded scan per lookup set; legacy domains never enter effect paths."""
        matches: dict[str, Request] = {}
        try:
            for row in self.db.execute("SELECT data FROM requests"):
                value = msgspec.json.decode(bytes(row[0]), type=Request)
                if value.approval_id not in approvals:
                    continue
                if value.denial_domain != DENIAL_DOMAIN:
                    raise Refused("legacy-denial-domain-needs-provenance")
                if value.approval_id in matches:
                    raise Refused("unique-durable-request-required")
                matches[value.approval_id] = value
        except msgspec.DecodeError:
            raise Refused("fixture-record-corrupt") from None
        if set(matches) != approvals:
            raise Refused("unique-durable-request-required")
        return matches

    def _request_for_approval(self, approval_id: str) -> Request:
        return self._requests_for_approvals({approval_id})[approval_id]

    def add_decision(self, value: Decision) -> str:
        """Retain every inbox event. First valid native decision wins, never first invalid candidate."""
        key, data = value.approval_id, encode_record(value)
        if value.version != 0 or value.phase != "pending":
            raise Refused("decision-not-initial")
        inbox_key = key + ":" + hashlib.sha256(encode_record(value.event.event_id)).hexdigest()
        with self.transaction():
            prior = self.db.execute(
                "SELECT data FROM events WHERE kind='decision-inbox' AND key=?", (inbox_key,)
            ).fetchone()
            row = self.db.execute("SELECT data FROM decisions WHERE key=?", (key,)).fetchone()
            old = self.get("decision", key) if row else None
            if prior is not None:
                if bytes(prior[0]) != data:
                    raise Refused("decision-event-id-conflict")
                if isinstance(old, Decision) and old.phase != "invalid":
                    # Exact retained inbox replay acknowledges history without admitting
                    # a new decision, even after request terminal settlement.
                    return "duplicate" if old.event == value.event else "superseded"
            request = self._request_for_approval(value.approval_id)
            expected = begin_decision(
                request,
                value.event,
                expected_purpose=value.purpose,
                current_digest=request.context_digest or "",
                heart=value.heart_proposal_pending,
            )
            if expected != value:
                raise Refused("decision-not-bound-to-durable-request")
            if old is None and value.event.value == "deny":
                self._terminal_budget(request)
            if prior is None:
                self._event("decision-inbox", inbox_key, 0, data)
            if isinstance(old, Decision):
                if old.phase != "invalid":
                    return "superseded"
                if old.observed_native_event is not None and old.observed_native_event != value.event:
                    return "superseded"
                if old.observed_native_event is None and any(
                    (record := msgspec.json.decode(bytes(history[0]), type=Decision)).phase == "invalid"
                    and record.event == value.event
                    for history in self.db.execute(
                        "SELECT data FROM events WHERE kind='decision' AND key=?", (key,)
                    )
                ):
                    return "superseded"
                value = msgspec.structs.replace(
                    value,
                    version=old.version + 1,
                    phase="native-written" if old.observed_native_event is not None else "pending",
                    native_event_id=old.observed_native_event.event_id if old.observed_native_event else None,
                )
                data = encode_record(value)
                self.db.execute(
                    "UPDATE decisions SET version=?,data=? WHERE key=?", (value.version, data, key)
                )
            else:
                self.db.execute("INSERT INTO decisions VALUES(?,?,?)", (key, value.version, data))
            self._event("decision", key, value.version, data)
        return "recorded"

    def add_dispatch(self, value: Dispatch) -> None:
        key, data = dispatch_key(value), encode_record(value)
        if value.version != 0 or value.state != "preparing":
            raise Refused("dispatch-not-initial")
        with self.transaction():
            sampled = self._sample_clock()
            value = msgspec.structs.replace(
                value,
                started=sampled,
                deadline_ns=sampled.mono_ns + DISPATCH_WINDOW_NS,
                preparation_deadline_ns=sampled.mono_ns + PREPARATION_WINDOW_NS,
            )
            data = encode_record(value)
            decision = self.get("decision", value.approval_id)
            request = self._request_for_approval(value.approval_id)
            if (
                request.phase != "filed"
                or request.context_digest != value.digest
                or request.request_revision != value.revision
            ):
                raise Refused("dispatch-request-not-filed")
            if (
                decision.phase != "committed"
                or decision.event.value != "approve"
                or decision.purpose != "execution"
                or (decision.digest, decision.request_revision) != (value.digest, value.revision)
            ):
                raise Refused("dispatch-decision-not-committed-approve")
            if self.db.execute("SELECT 1 FROM dispatches WHERE key=?", (key,)).fetchone():
                raise Refused("dispatch-already-exists-no-initial-replay")
            if self.db.execute("SELECT count(*) FROM dispatches").fetchone()[0] >= MAX_RECORDS:
                raise Refused("fixture-record-capacity-needs-recovery")
            if value.dispatch_kind == "reconcile":
                self._add_reconciliation(value, key, data)
                self._clock_event("dispatch", key, value.version, sampled)
                return
            if value.dispatch_kind != "initial" or value.parent_attempt_id is not None:
                raise Refused("dispatch-kind-binding")
            resources = sorted({"resource:" + value.resource, "effect:" + value.target, "approval:" + key})
            for resource in resources:
                if self.db.execute("SELECT 1 FROM resources WHERE resource=?", (resource,)).fetchone():
                    raise Refused("resource-still-held")
            self.db.executemany("INSERT INTO resources VALUES(?,?)", ((r, key) for r in resources))
            self.db.execute("INSERT INTO dispatches VALUES(?,?,?)", (key, value.version, data))
            self._event("dispatch", key, value.version, data)
            self._clock_event("dispatch", key, value.version, sampled)

    def _reconciliation_children(self, approval_id: str) -> tuple[Dispatch, ...]:
        return tuple(
            msgspec.json.decode(bytes(row[0]), type=Dispatch)
            for row in self.db.execute(
                "SELECT data FROM dispatches WHERE key LIKE ? ORDER BY rowid", (approval_id + ":reconcile:%",)
            )
        )

    def _add_reconciliation(self, value: Dispatch, key: str, data: bytes) -> None:
        parent = self.get("dispatch", value.approval_id)
        if (
            parent.dispatch_kind != "initial"
            or parent.settled
            or parent.state not in {"uncertain", "failed"}
            or (parent.attempt_id, parent.revision, parent.digest, parent.target, parent.resource)
            != (value.parent_attempt_id, value.revision, value.digest, value.target, value.resource)
            or not set((*parent.retired_startups, parent.startup_hash)).issubset(value.retired_startups)
            or not set(
                (*parent.retired_nonces, *((parent.claim_hash,) if parent.claim_hash else ()))
            ).issubset(value.retired_nonces)
        ):
            raise Refused("reconcile-parent-binding")
        reconciliations = 0
        unread = 0
        previous_start = parent.started
        for previous in (parent, *self._reconciliation_children(value.approval_id)):
            if previous.dispatch_kind == "reconcile":
                reconciliations += 1
                previous_start = previous.started
                if previous.launch_attempts != 1:
                    raise Refused("reconcile-attempt-history-needs-recovery")
                if not previous.settled:
                    raise Refused("reconcile-invocation-still-held")
                if previous.reconciliation_effect is not None:
                    unread += previous.attempt_id not in parent.reconciliation_attempts
            used = {
                previous.startup_hash,
                previous.claim_hash,
                *previous.retired_startups,
                *previous.retired_nonces,
            }
            if value.attempt_id == previous.attempt_id or value.startup_hash in used:
                raise Refused("reconcile-identity-reused")
            if not {v for v in used if v is not None}.issubset(
                set(value.retired_startups) | set(value.retired_nonces)
            ):
                raise Refused("reconcile-retired-history-required")
        if unread:
            raise Refused("reconcile-parent-observation-required")
        if reconciliations >= 3:
            raise Refused("reconcile-needs-authenticated-recovery")
        if clock_elapsed(previous_start, value.started) < (1, 5, 30)[reconciliations]:
            raise Refused("reconcile-attempt-not-due")
        self._terminal_budget(self._request_for_approval(value.approval_id))
        self.db.execute("INSERT INTO dispatches VALUES(?,?,?)", (key, value.version, data))
        self._event("dispatch", key, value.version, data)

    @overload
    def transition(
        self,
        kind: Literal["request"],
        key: str,
        expected_version: int,
        reducer: Callable[[Request], Request] | ObservedReducer[Request],
        *,
        observation: TransitionObservation = EMPTY_OBSERVATION,
    ) -> Request: ...

    @overload
    def transition(
        self,
        kind: Literal["decision"],
        key: str,
        expected_version: int,
        reducer: Callable[[Decision], Decision] | ObservedReducer[Decision],
        *,
        observation: TransitionObservation = EMPTY_OBSERVATION,
    ) -> Decision: ...

    @overload
    def transition(
        self,
        kind: Literal["dispatch"],
        key: str,
        expected_version: int,
        reducer: Callable[[Dispatch], Dispatch] | ObservedReducer[Dispatch],
        *,
        observation: TransitionObservation = EMPTY_OBSERVATION,
    ) -> Dispatch: ...

    def transition(
        self,
        kind: Literal["request", "decision", "dispatch"],
        key: str,
        expected_version: int,
        reducer: Callable[[Any], Request | Decision | Dispatch] | ObservedReducer[Any],
        *,
        observation: TransitionObservation = EMPTY_OBSERVATION,
    ) -> Request | Decision | Dispatch:
        """Read, sample the injected fixture clock, reduce and update in one transaction."""
        _, cls = TABLES[kind]
        with self.transaction():
            old = self.get(kind, key)
            sampled = self._sample_clock()
            observation = msgspec.structs.replace(observation, now=sampled)
            for field in ("dispatch_recovery", "allocation_recovery", "abandonment_recovery"):
                recovery = getattr(observation, field)
                if recovery is not None and recovery.clock_discontinuity:
                    observation = msgspec.structs.replace(
                        observation, **{field: msgspec.structs.replace(recovery, observed_now=sampled)}
                    )
            observation = self._recovery_clock(old, observation, sampled)
            try:
                value = (
                    cast(ObservedReducer[Any], reducer).reduce(old, observation)
                    if isinstance(reducer, ObservedReducer)
                    else reducer(old)
                )
            except Refused:
                if old.version != expected_version:
                    raise Refused("stale-fixture-version") from None
                raise
            if type(value) is cls and value == old:
                return old
            if old.version != expected_version:
                raise Refused("stale-fixture-version")
            if type(value) is not cls or value.version != old.version + 1:
                raise Refused("invalid-fixture-transition")
            data = request_bytes(value) if isinstance(value, Request) else encode_record(value)
            if self._recovery_progress(old, value):
                request = old if isinstance(old, Request) else self._request_for_approval(old.approval_id)
                self._terminal_budget(request)
            if isinstance(value, Request):
                if not isinstance(old, Request):
                    raise Refused("immutable-request-binding")
                value = self._request_proposal(old, value, observation)
                data = request_bytes(value)
                self.db.execute(
                    "UPDATE requests SET data=?,version=?,outstanding=? WHERE key=? AND version=?",
                    (data, value.version, _outstanding(value), key, expected_version),
                )
            else:
                if isinstance(old, Decision) and isinstance(value, Decision):
                    request = self._request_for_approval(old.approval_id)
                    decision_transition(old, value, request=request, observation=observation)
                    if value.phase == "committed" and value.event.value == "deny":
                        self._denial(request, value)
                elif isinstance(old, Dispatch) and isinstance(value, Dispatch):
                    reconciliation = None
                    if observation.reconciliation_key is not None:
                        reconciliation = self.get("dispatch", observation.reconciliation_key)
                    if value.reconciliation_lookups != old.reconciliation_lookups:
                        observation = msgspec.structs.replace(
                            observation, reconciliation_started=self._dispatch_lookup_clock(old, proven=True)
                        )
                    dispatch_transition(old, value, observation=observation, reconciliation=reconciliation)
                    if value.reconciliation_lookups != old.reconciliation_lookups:
                        if reconciliation is None:
                            raise Refused("reconciliation-record-required")
                        value = msgspec.structs.replace(
                            value,
                            reconciliation_attempts=(*old.reconciliation_attempts, reconciliation.attempt_id),
                        )
                        data = encode_record(value)
                if (dispatch_key(value) if isinstance(value, Dispatch) else value.approval_id) != key:
                    raise Refused("immutable-approval-binding")
                query = {
                    "decision": "UPDATE decisions SET data=?,version=? WHERE key=? AND version=?",
                    "dispatch": "UPDATE dispatches SET data=?,version=? WHERE key=? AND version=?",
                }[kind]
                self.db.execute(query, (data, value.version, key, expected_version))
            if isinstance(value, Dispatch) and value.settled and value.dispatch_kind == "initial":
                for child in self._reconciliation_children(value.approval_id):
                    if not child.settled:
                        raise Refused("reconciliation-invocation-still-held")
                self.db.execute("DELETE FROM resources WHERE dispatch_key=?", (key,))
            self._event(kind, key, value.version, data)
            self._clock_event(kind, key, value.version, sampled)
            if observation != EMPTY_OBSERVATION:
                self._event(kind + "-observation", key, value.version, encode_record(observation))
        return value

    @staticmethod
    def _recovery_progress(old: Request | Decision | Dispatch, value: Request | Decision | Dispatch) -> bool:
        if isinstance(old, Request) and isinstance(value, Request):
            return value.phase in TERMINAL_REQUESTS or old.phase == "allocation-unknown"
        if isinstance(old, Decision) and isinstance(value, Decision):
            # Every phase is finite; invalidation/substitution loops cannot spend this reserve.
            return value.phase != old.phase
        if isinstance(old, Dispatch) and isinstance(value, Dispatch):
            return (
                value.settled
                or value.reconciliation_lookups > old.reconciliation_lookups
                or (value.state == "held" and old.state in {"preparing", "ready"})
                or (value.state == "uncertain" and old.state in {"ready", "executing"})
                or (value.state == "failed" and old.state in {"executing", "uncertain"})
            )
        return False

    def _sample_clock(self) -> Clock:
        if self.clock is None:
            raise Refused("journal-clock-sampler-required")
        sampled = self.clock()
        clock_elapsed(sampled, sampled)
        return sampled

    def _clock_event(self, kind: str, key: str, version: int, sampled: Clock | None) -> None:
        if sampled is not None:
            self._event(kind + "-clock", key, version, encode_record(sampled))

    def _proven_clock(self, kind: str, key: str, version: int, expected: Clock | None) -> None:
        row = self.db.execute(
            "SELECT data FROM events WHERE kind=? AND key=? AND version=?",
            (kind + "-clock", key, version),
        ).fetchone()
        if row is None or expected is None or msgspec.json.decode(row[0], type=Clock) != expected:
            raise Refused("journal-clock-provenance-required")

    def _recovery_clock(
        self,
        old: Request | Decision | Dispatch,
        observation: TransitionObservation,
        sampled: Clock | None,
    ) -> TransitionObservation:
        recoveries = tuple(
            item
            for item in (
                observation.dispatch_recovery,
                observation.allocation_recovery,
                observation.abandonment_recovery,
                observation.cancellation_recovery,
            )
            if item is not None
        )
        if len(recoveries) > 1:
            raise Refused("multiple-recovery-observations")
        recovery = recoveries[0] if recoveries else None
        if recovery is not None and (
            isinstance(old, Dispatch) != isinstance(recovery, DispatchRecovery) or isinstance(old, Decision)
        ):
            raise Refused("clock-recovery-record-mismatch")
        if (
            isinstance(old, Dispatch)
            and old.dispatch_kind == "initial"
            and isinstance(recovery, DispatchRecovery)
        ):
            children = self._reconciliation_children(old.approval_id)
            facts = tuple(reconciliation_disposition(old, child) for child in children)
            unread = any(item.effect is not None and not item.applied for item in facts)
            if recovery.effect_definitively_absent and any(item.effect == "succeeded" for item in facts):
                raise Refused("reconciliation-success-conflicts-with-absence")
            if (
                unread or recovery.completed_reconciliation_attempts or recovery.observed_reconciliations
            ) and (
                any(
                    not child.settled or not child.cleanup_proven or child.launch_attempts != 1
                    for child in children
                )
                or tuple(sorted(facts, key=lambda item: item.attempt_id))
                != tuple(sorted(recovery.observed_reconciliations, key=lambda item: item.attempt_id))
                or (
                    recovery.completed_reconciliation_attempts
                    and (
                        len(children) != 3
                        or tuple(sorted(child.attempt_id for child in children))
                        != tuple(sorted(recovery.completed_reconciliation_attempts))
                    )
                )
            ):
                raise Refused("reconciliation-exhaustion-provenance-required")
        if recovery is None or isinstance(recovery, CancellationRecovery) or not recovery.clock_discontinuity:
            return observation
        if sampled is None:
            raise Refused("journal-clock-sampler-required")
        now = sampled
        started = None
        if isinstance(old, Dispatch) and isinstance(recovery, DispatchRecovery):
            started = self._dispatch_lookup_clock(old, proven=True)
            if recovery.clock_anchor != started:
                raise Refused("recovery-clock-anchor-mismatch")
        elif isinstance(old, Request):
            row = self.db.execute(
                "SELECT data FROM events WHERE kind='request-observation' AND key=? AND version=?",
                (request_key(old), old.version),
            ).fetchone()
            started = _observation(row[0]).now if row else None
            self._proven_clock("request", request_key(old), old.version, started)
        if started is None:
            raise Refused("clock-recovery-history-required")
        try:
            clock_elapsed(started, now)
        except Refused:
            return msgspec.structs.replace(observation, now=now)
        raise Refused("clock-recovery-not-discontinuous")

    def _dispatch_lookup_clock(self, old: Dispatch, *, proven: bool = False) -> Clock:
        if old.reconciliation_lookups == 0:
            if proven:
                self._proven_clock("dispatch", dispatch_key(old), 0, old.started)
            return old.started
        # Find the first retained state with the current lookup count, not a later
        # worker/hold event or a caller-supplied timestamp. Storage is bounded.
        rows = self.db.execute(
            "SELECT version,data FROM events WHERE kind='dispatch' AND key=? ORDER BY version",
            (dispatch_key(old),),
        )
        for version, data in rows:
            record = msgspec.json.decode(data, type=Dispatch)
            if record.reconciliation_lookups == old.reconciliation_lookups:
                row = self.db.execute(
                    "SELECT data FROM events WHERE kind='dispatch-observation' AND key=? AND version=?",
                    (dispatch_key(old), version),
                ).fetchone()
                observed = _observation(row[0]) if row else EMPTY_OBSERVATION
                if observed.now is not None:
                    if proven:
                        self._proven_clock("dispatch", dispatch_key(old), version, observed.now)
                    return observed.now
                break
        raise Refused("reconciliation-clock-history-required")

    def _request_proposal(self, old: Request, value: Request, observation: TransitionObservation) -> Request:
        row = self.db.execute("SELECT 1 FROM decisions WHERE key=?", (value.approval_id or "",)).fetchone()
        decision = self.get("decision", value.approval_id or "") if row else None
        dispatch_row = self.db.execute(
            "SELECT 1 FROM dispatches WHERE key=?", (value.approval_id or "",)
        ).fetchone()
        dispatch = self.get("dispatch", value.approval_id or "") if dispatch_row else None
        # The issuance clock is write-ahead journal history, never caller-supplied elapsed time.
        if old.phase == "allocation-unknown":
            row = self.db.execute(
                "SELECT data FROM events WHERE kind='request-observation' AND key=? AND version=?",
                (request_key(old), old.version),
            ).fetchone()
            issued = _observation(row[0]) if row else EMPTY_OBSERVATION
            self._proven_clock("request", request_key(old), old.version, issued.now)
            observation = msgspec.structs.replace(observation, allocation_started=issued.now)
        request_transition(old, value, decision=decision, dispatch=dispatch, observation=observation)
        if value.phase == "denied":
            self._denial(value, decision)
        if value.phase in TERMINAL_REQUESTS:
            return value
        revised = value.request_revision != old.request_revision
        if (revised or value.phase == "filed") and self.denied(
            value.denial_identity or "", value.origin.session_key
        ):
            raise Refused("same-session-denied-intent")
        # A later unrelated denial cannot freeze an already admitted request.
        # Refresh history from the journal, never from a caller assertion.
        history = set(value.prior_denials)
        history.update(
            row[0]
            for row in self.db.execute(
                "SELECT identity FROM denials WHERE session_key=?", (value.origin.session_key,)
            )
        )
        if len(history) > 64:
            raise Refused("denial-history-capacity-needs-recovery")
        return msgspec.structs.replace(value, prior_denials=tuple(sorted(history)))

    def _denial(self, value: Request, decision: object) -> None:
        if (
            not isinstance(decision, Decision)
            or decision.phase != "committed"
            or decision.event.value != "deny"
            or value.approval_id != decision.approval_id
            or value.context_digest != decision.digest
            or value.request_revision != decision.request_revision
            or value.denial_identity
            != request_denial_key(value.intent, value.origin, value.resolved, value.config)
        ):
            raise Refused("denial-not-committed")
        result = self.db.execute(
            "INSERT OR IGNORE INTO denials VALUES(?,?,?)",
            (value.denial_identity, value.origin.session_key, decision.approval_id),
        )
        if result.rowcount:
            self._event("denial", value.denial_identity or "", 0, encode_record((value.key, decision)))

    def record_denial(self, value: Request, decision: Decision) -> None:
        with self.transaction():
            durable = self.get("decision", decision.approval_id)
            request = self.get("request", request_key(value))
            if durable != decision or request != value:
                raise Refused("denial-not-durable")
            self._denial(request, durable)

    def _denial_provenance(self, session_key: str) -> None:
        """Legacy opaque hashes cannot be silently reinterpreted in a new domain."""
        rows = self.db.execute(
            "SELECT identity,approval FROM denials WHERE session_key=?", (session_key,)
        ).fetchall()
        requests = self._requests_for_approvals({row[1] for row in rows}) if rows else {}
        for identity, approval in rows:
            value = requests[approval]
            if (
                value.denial_domain != DENIAL_DOMAIN
                or value.origin.session_key != session_key
                or value.denial_identity != identity
                or identity != request_denial_key(value.intent, value.origin, value.resolved, value.config)
            ):
                raise Refused("legacy-denial-domain-needs-provenance")
            decision = self.get("decision", approval)
            if (
                decision.phase != "committed"
                or decision.event.value != "deny"
                or decision.approval_id != value.approval_id
                or decision.digest != value.context_digest
                or decision.request_revision != value.request_revision
                or decision.event.digest != decision.digest
                or decision.event.approval_id != decision.approval_id
            ):
                raise Refused("denial-history-not-committed")

    def denied(self, identity: str, session_key: str) -> bool:
        self._denial_provenance(session_key)
        return (
            self.db.execute(
                "SELECT 1 FROM denials WHERE identity=? AND session_key=?", (identity, session_key)
            ).fetchone()
            is not None
        )


def create_fixture(path: Path, *, version: Literal[1, 2] = 2) -> None:
    """Explicit empty fixture creation. Existing files, including real journals, are never replaced."""
    if type(version) is not int or version not in {1, 2}:
        raise Refused("fixture-schema-version")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with contextlib.closing(sqlite3.connect(path, isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            for statement in (*_BASE, *(_V2 if version == 2 else ())):
                db.execute(statement)
            db.execute("INSERT INTO meta VALUES(?)", (version,))
            db.commit()
        except BaseException:
            db.rollback()
            raise


def upgrade_fixture(path: Path, checkpoint: Callable[[str], None] = lambda _: None) -> None:
    """Fixture-only v1→v2, exact schema check and one rollback-able transaction; never WSD schema."""
    with contextlib.closing(_open_fixture(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            if _checked(db, current=False) != 1:
                raise Refused("fixture-upgrade-not-v1")
            for statement in _V2:
                db.execute(statement)
                checkpoint("fixture-table-created")
            db.execute("UPDATE meta SET version=2")
            checkpoint("fixture-version-updated")
            _checked(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
