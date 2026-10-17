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
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.actions.codec import encode_record
from heterodyne.actions.decisions import Decision
from heterodyne.actions.dispatch import Dispatch
from heterodyne.actions.policy import request_denial_key
from heterodyne.actions.requests import TERMINAL_REQUESTS, Refused, Request, request_bytes
from heterodyne.actions.transitions import decision_transition, dispatch_transition, request_transition

APPLICATION_ID = 0x50315031
SCHEMA_VERSION = 2
MAX_OUTSTANDING = 128
MAX_RECORDS = 4096
MAX_EVENTS = 16_384
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


class FixtureJournal:
    """Tests may open/reopen ONLY private journals created by create(); never an installed journal."""

    def __init__(self, path: Path):
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
    def create(cls, path: Path) -> "FixtureJournal":
        create_fixture(path)
        return cls(path)

    def close(self) -> None:
        self.db.close()

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _event(self, kind: str, key: str, version: int, data: bytes) -> None:
        if self.db.execute("SELECT coalesce(max(seq), 0) FROM events").fetchone()[0] >= MAX_EVENTS:
            raise Refused("fixture-event-capacity-needs-recovery")
        self.db.execute(
            "INSERT INTO events(kind,key,version,data) VALUES(?,?,?,?)", (kind, key, version, data)
        )

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
            return msgspec.json.decode(row[0], type=cls)
        except msgspec.DecodeError:
            raise Refused("fixture-record-corrupt") from None

    def add_request(self, value: Request) -> str:
        key, data = request_key(value), request_bytes(value)
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
                    return "duplicate" if old.event == value.event else "superseded"
            else:
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
            decision = self.get("decision", value.approval_id)
            if (
                not isinstance(decision, Decision)
                or decision.phase != "committed"
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

    def _add_reconciliation(self, value: Dispatch, key: str, data: bytes) -> None:
        parent = self.get("dispatch", value.approval_id)
        if (
            not isinstance(parent, Dispatch)
            or parent.dispatch_kind != "initial"
            or parent.settled
            or parent.state not in {"held", "uncertain", "failed"}
            or (parent.attempt_id, parent.revision, parent.digest, parent.target, parent.resource)
            != (value.parent_attempt_id, value.revision, value.digest, value.target, value.resource)
            or not set((*parent.retired_startups, parent.startup_hash)).issubset(value.retired_startups)
            or not set(
                (*parent.retired_nonces, *((parent.claim_hash,) if parent.claim_hash else ()))
            ).issubset(value.retired_nonces)
        ):
            raise Refused("reconcile-parent-binding")
        reconciliations = 0
        for row in self.db.execute(
            "SELECT data FROM dispatches WHERE key=? OR key LIKE ?",
            (value.approval_id, value.approval_id + ":reconcile:%"),
        ):
            previous = msgspec.json.decode(bytes(row[0]), type=Dispatch)
            if previous.dispatch_kind == "reconcile":
                reconciliations += 1
                if not previous.settled:
                    raise Refused("reconcile-invocation-still-held")
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
        if reconciliations >= 3:
            raise Refused("reconcile-needs-authenticated-recovery")
        self.db.execute("INSERT INTO dispatches VALUES(?,?,?)", (key, value.version, data))
        self._event("dispatch", key, value.version, data)

    def transition(
        self,
        kind: Literal["request", "decision", "dispatch"],
        key: str,
        expected_version: int,
        reducer: Callable[[Request | Decision | Dispatch], Request | Decision | Dispatch],
    ) -> Request | Decision | Dispatch:
        """Read, reduce and conditional update share one transaction; no port calls inside it."""
        _, cls = TABLES[kind]
        with self.transaction():
            old = self.get(kind, key)
            try:
                value = reducer(old)
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
            if isinstance(value, Request):
                if not isinstance(old, Request):
                    raise Refused("immutable-request-binding")
                value = self._request_proposal(old, value)
                data = request_bytes(value)
                self.db.execute(
                    "UPDATE requests SET data=?,version=?,outstanding=? WHERE key=? AND version=?",
                    (data, value.version, _outstanding(value), key, expected_version),
                )
            else:
                if isinstance(old, Decision) and isinstance(value, Decision):
                    decision_transition(old, value)
                elif isinstance(old, Dispatch) and isinstance(value, Dispatch):
                    dispatch_transition(old, value)
                if (dispatch_key(value) if isinstance(value, Dispatch) else value.approval_id) != key:
                    raise Refused("immutable-approval-binding")
                query = {
                    "decision": "UPDATE decisions SET data=?,version=? WHERE key=? AND version=?",
                    "dispatch": "UPDATE dispatches SET data=?,version=? WHERE key=? AND version=?",
                }[kind]
                self.db.execute(query, (data, value.version, key, expected_version))
            if isinstance(value, Dispatch) and value.settled and value.dispatch_kind == "initial":
                self.db.execute("DELETE FROM resources WHERE dispatch_key=?", (key,))
            self._event(kind, key, value.version, data)
        return value

    def _request_proposal(self, old: Request, value: Request) -> Request:
        decision = self.get("decision", value.approval_id or "") if value.phase == "denied" else None
        dispatch = self.get("dispatch", value.approval_id or "") if value.phase == "completed" else None
        request_transition(old, value, decision=decision, dispatch=dispatch)
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
            if durable != decision:
                raise Refused("denial-not-durable")
            self._denial(value, durable)

    def denied(self, identity: str, session_key: str) -> bool:
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
