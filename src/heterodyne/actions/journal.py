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
from heterodyne.actions.requests import TERMINAL_REQUESTS, Refused, Request, request_bytes

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
            if self.db.execute("SELECT count(*) FROM events").fetchone()[0] >= MAX_EVENTS:
                raise Refused("fixture-event-capacity-needs-recovery")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _event(self, kind: str, key: str, version: int, data: bytes) -> None:
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
            raise Refused("fixture-record-missing")
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
                if bytes(row[0]) != data:
                    raise Refused("creation-key-conflict")
                return key
            if value.denial_identity is None or self.denied(value.denial_identity, value.origin.session_key):
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

    def add_decision(self, value: Decision) -> None:
        """The first valid event wins; repeated identical event is idempotent, conflicts never overwrite."""
        key, data = value.approval_id, encode_record(value)
        if value.version != 0 or value.phase != "pending":
            raise Refused("decision-not-initial")
        with self.transaction():
            row = self.db.execute("SELECT data FROM decisions WHERE key=?", (key,)).fetchone()
            if row is not None:
                if bytes(row[0]) != data:
                    raise Refused("first-decision-already-recorded")
                return
            self.db.execute("INSERT INTO decisions VALUES(?,?,?)", (key, value.version, data))
            self._event("decision", key, value.version, data)

    def add_dispatch(self, value: Dispatch) -> None:
        key, data = value.approval_id, encode_record(value)
        if value.version != 0 or value.state != "preparing":
            raise Refused("dispatch-not-initial")
        with self.transaction():
            if self.db.execute("SELECT 1 FROM dispatches WHERE key=?", (key,)).fetchone():
                raise Refused("dispatch-already-exists-no-initial-replay")
            resources = sorted({"resource:" + value.resource, "effect:" + value.target, "approval:" + key})
            for resource in resources:
                if self.db.execute("SELECT 1 FROM resources WHERE resource=?", (resource,)).fetchone():
                    raise Refused("resource-still-held")
            self.db.executemany("INSERT INTO resources VALUES(?,?)", ((r, key) for r in resources))
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
            if old.version != expected_version:
                raise Refused("stale-fixture-version")
            value = reducer(old)
            if type(value) is not cls or value.version != old.version + 1:
                raise Refused("invalid-fixture-transition")
            data = request_bytes(value) if isinstance(value, Request) else encode_record(value)
            if isinstance(value, Request):
                if not isinstance(old, Request) or value.key != old.key or value.origin != old.origin:
                    raise Refused("immutable-request-binding")
                self.db.execute(
                    "UPDATE requests SET data=?,version=?,outstanding=? WHERE key=? AND version=?",
                    (data, value.version, _outstanding(value), key, expected_version),
                )
            else:
                if value.approval_id != key:
                    raise Refused("immutable-approval-binding")
                query = {
                    "decision": "UPDATE decisions SET data=?,version=? WHERE key=? AND version=?",
                    "dispatch": "UPDATE dispatches SET data=?,version=? WHERE key=? AND version=?",
                }[kind]
                self.db.execute(query, (data, value.version, key, expected_version))
            if isinstance(value, Dispatch) and value.settled:
                self.db.execute("DELETE FROM resources WHERE dispatch_key=?", (key,))
            self._event(kind, key, value.version, data)
        return value

    def record_denial(self, value: Request, decision: Decision) -> None:
        if (
            decision.phase != "committed"
            or decision.event.value != "deny"
            or value.approval_id != decision.approval_id
            or value.context_digest != decision.digest
            or value.denial_identity is None
        ):
            raise Refused("denial-not-committed")
        with self.transaction():
            result = self.db.execute(
                "INSERT OR IGNORE INTO denials VALUES(?,?,?)",
                (value.denial_identity, value.origin.session_key, decision.approval_id),
            )
            if result.rowcount:
                self._event("denial", value.denial_identity, 0, encode_record((value.key, decision)))

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
