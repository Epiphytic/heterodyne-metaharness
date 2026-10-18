"""Single-owner scheduled-effect journal. No producer or activation is supplied by this module.

The daemon owns this connection on its dedicated lane. Cached health is immutable and never queries
SQLite. Missing/corrupt stores hold effects alone; initialization is an explicit deployment operation.
"""

import json
import math
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heterodyne.wsd.effect_slots import Clock, Sample

State = Literal["prepared", "dispatched", "succeeded", "definite_unsent", "uncertain", "held"]
Result = Literal["succeeded", "definite_unsent", "uncertain", "held"]
SCHEMA = """
PRAGMA user_version=1;
CREATE TABLE identity(version INTEGER NOT NULL CHECK(version=1), installation TEXT NOT NULL,
 generation TEXT NOT NULL, utc_water REAL NOT NULL);
CREATE TABLE jobs(name TEXT PRIMARY KEY, high_water INTEGER NOT NULL DEFAULT -1,
 skipped INTEGER NOT NULL DEFAULT 0, latest TEXT NOT NULL DEFAULT '', slot INTEGER,
 state TEXT CHECK(state IN ('prepared','dispatched','succeeded','definite_unsent','uncertain','held')),
 attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0);
CREATE TABLE backup_cycle(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 high_water TEXT NOT NULL DEFAULT '0', cycle TEXT, native TEXT, state TEXT,
 attempt TEXT NOT NULL DEFAULT '', manifest_hash TEXT NOT NULL DEFAULT '');
INSERT INTO backup_cycle(singleton) VALUES(1);
"""


@dataclass(frozen=True)
class Health:
    state: Literal["ready", "unready", "error"]
    observed_at: float
    revision: int
    jobs: tuple[tuple[str, str], ...] = ()
    backup_state: str = "unavailable"
    held: int = 0
    uncertain: int = 0
    oldest_age: float | None = None


@dataclass(frozen=True)
class ProducerResult:
    status: Result
    proven_unsent: bool = False


Producer = Callable[[int, float], ProducerResult]


class EffectStore:
    def __init__(self, path: Path, installation: str, generation: str) -> None:
        self.lock = threading.RLock()
        self.path = path
        self.db: sqlite3.Connection | None = None
        self.clock = Clock()
        self.cache = Health("unready", 0, 0)
        self.owner = threading.get_ident()
        try:
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError("effect store is not private")
            db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=0.1)
            self.db = db
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("effect integrity")
            identity = db.execute("SELECT version,installation,generation,utc_water FROM identity").fetchall()
            if len(identity) != 1 or identity[0][:3] != (1, installation, generation):
                raise ValueError("effect identity")
            expected = sqlite3.connect(":memory:")
            try:
                expected.executescript(SCHEMA)
                query = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                if set(db.execute(query)) != set(expected.execute(query)) or db.execute(
                    "PRAGMA user_version"
                ).fetchone() != (1,):
                    raise ValueError("effect schema")
            finally:
                expected.close()
            if db.execute("SELECT count(*) FROM jobs").fetchone()[0] > 8:
                raise ValueError("effect registry capacity")
            high, cycle = db.execute("SELECT high_water,cycle FROM backup_cycle").fetchone()
            if not 0 <= int(high) < (1 << 64) or (cycle is not None and not 1 <= int(cycle) < (1 << 64)):
                raise ValueError("backup cycle bounds")
            self.clock = Clock(float(identity[0][3]))
            # A committed dispatch mark without an atomic external receipt is never blindly replayed.
            with db:
                db.execute("UPDATE jobs SET state='uncertain' WHERE state='dispatched'")
                db.execute("UPDATE backup_cycle SET state='uncertain' WHERE cycle IS NOT NULL")
            self._refresh(time.time())
        except (OSError, ValueError, sqlite3.Error):
            if self.db is not None:
                self.db.close()
            self.db = None
            self.cache = Health("error", 0, 1)

    @staticmethod
    def initialize(path: Path, installation: str, generation: str) -> None:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(path)
        try:
            db.executescript(SCHEMA)
            with db:
                db.execute("INSERT INTO identity VALUES(1,?,?,0)", (installation, generation))
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()

    def _owned(self) -> sqlite3.Connection:
        if threading.get_ident() != self.owner or self.db is None:
            raise RuntimeError("effect owner unavailable")
        return self.db

    def _refresh(self, now: float) -> None:
        db = self._owned()
        jobs = tuple(
            (str(r[0]), str(r[1] or "idle"))
            for r in db.execute("SELECT name,state FROM jobs ORDER BY name LIMIT 9")
        )
        self.cache = Health(
            "ready",
            now,
            self.cache.revision + 1,
            jobs[:8],
            str(db.execute("SELECT state FROM backup_cycle").fetchone()[0] or "idle"),
            sum(state == "held" for _, state in jobs),
            sum(state == "uncertain" for _, state in jobs)
            + int(db.execute("SELECT state FROM backup_cycle").fetchone()[0] == "uncertain"),
            (max(0, now - float(row[0])) if (row := db.execute(
                "SELECT min(updated) FROM jobs WHERE state IN ('held','uncertain')"
            ).fetchone())[0] is not None else None),
        )

    def refresh(self) -> None:
        with self.lock:
            self._refresh(time.time())

    def observe(self, sample: Sample) -> bool:
        with self.lock:
            db = self._owned()
            ready = self.clock.observe(sample)
            with db:
                db.execute("UPDATE identity SET utc_water=?", (self.clock.high_water,))
            self._refresh(sample.utc)
            return ready

    def prepare(self, name: str, slot: int, now: float) -> bool:
        """O(1) ledger per job; newer due work cannot overwrite an unresolved effect."""
        if not name or len(name) > 64 or not name.isascii() or type(slot) is not int or slot < 0:
            raise ValueError("effect key")
        with self.lock:
            db = self._owned()
            with db:
                count = int(db.execute("SELECT count(*) FROM jobs").fetchone()[0])
                row = db.execute("SELECT high_water,slot,state FROM jobs WHERE name=?", (name,)).fetchone()
                if row is None:
                    if count >= 8:
                        return False
                    db.execute("INSERT INTO jobs(name) VALUES(?)", (name,))
                    row = (-1, None, None)
                if (not self.clock.ready or not math.isfinite(now) or now < self.clock.high_water
                        or slot <= row[0] or row[1] is not None):
                    return False
                skipped = max(0, slot - row[0] - 1)
                db.execute(
                    "UPDATE jobs SET slot=?,state='prepared',attempts=0,updated=?,"
                    "skipped=min(9223372036854775807,skipped+?) WHERE name=?",
                    (slot, now, skipped, name),
                )
                db.execute("UPDATE identity SET utc_water=max(utc_water,?)", (now,))
            self.clock.high_water = max(self.clock.high_water, now)
            self._refresh(now)
            return True

    def dispatch(self, name: str, producer: Producer, now: float, deadline: float) -> bool:
        with self.lock:
            db = self._owned()
            row = db.execute("SELECT slot,state,attempts FROM jobs WHERE name=?", (name,)).fetchone()
            if not self.clock.ready or row is None or row[1] not in ("prepared", "definite_unsent"):
                return False
            slot = int(row[0])
            with db:
                db.execute(
                    "UPDATE jobs SET state='dispatched',attempts=attempts+1,updated=? WHERE name=?",
                    (now, name),
                )
            self._refresh(now)
        # Never hold a SQLite transaction or the journal lock across a producer.
        try:
            result = producer(slot, deadline)
            status = result.status
            if status == "definite_unsent" and not result.proven_unsent:
                status = "uncertain"
        except Exception:
            status = "uncertain"
        with self.lock:
            with db:
                if status == "succeeded":
                    db.execute(
                        "UPDATE jobs SET high_water=slot,latest=?,slot=NULL,state=NULL,updated=? "
                        "WHERE name=?",
                        (json.dumps([slot, status]), now, name),
                    )
                else:
                    db.execute("UPDATE jobs SET state=?,updated=? WHERE name=?", (status, now, name))
            self._refresh(now)
        return True

    def pass_once(self, producers: Mapping[str, Producer], monotonic: Callable[[], float], now: float) -> int:
        """At most eight calls / thirty seconds; rotate the start to avoid starving a job."""
        start = monotonic()
        db = self._owned()
        rows = [
            str(r[0])
            for r in db.execute(
                "SELECT name FROM jobs WHERE state IN ('prepared','definite_unsent') "
                "ORDER BY updated,name LIMIT 8"
            )
        ]
        calls = 0
        for name in rows:
            remaining = 30 - (monotonic() - start)
            if remaining <= 0:
                break
            producer = producers.get(name)
            if producer is not None and self.dispatch(name, producer, now, monotonic() + min(5, remaining)):
                calls += 1
        return calls

    def begin_backup(self, cycle: int, native: str) -> str:
        if (
            type(cycle) is not int
            or not 1 <= cycle <= (1 << 64) - 1
            or native not in ("succeeded", "failed", "unknown")
        ):
            raise ValueError("backup cycle")
        with self.lock:
            db = self._owned()
            high, pending = db.execute("SELECT high_water,cycle FROM backup_cycle").fetchone()
            if pending is not None and int(pending) == cycle:
                return "joined"
            if pending is not None:
                return "unresolved"
            if cycle <= int(high):
                return "completed"
            with db:
                db.execute("UPDATE backup_cycle SET cycle=?,native=?,state='copying'", (str(cycle), native))
            self._refresh(time.time())
            return "queued"

    def backup_attempt(self, cycle: int, attempt: str) -> None:
        with self.lock:
            db = self._owned()
            with db:
                changed = db.execute(
                    "UPDATE backup_cycle SET attempt=? WHERE cycle=? AND state='copying'",
                    (attempt, str(cycle)),
                ).rowcount
                if changed != 1:
                    raise ValueError("backup attempt ownership")

    def pending_backup(self) -> tuple[int, str, str] | None:
        db = self._owned()
        row = db.execute("SELECT cycle,native,attempt FROM backup_cycle").fetchone()
        return None if row[0] is None else (int(row[0]), str(row[1]), str(row[2]))

    def finish_backup(self, cycle: int, status: str, attempt: str = "", sha: str = "") -> None:
        with self.lock:
            db = self._owned()
            pending = db.execute("SELECT cycle FROM backup_cycle").fetchone()[0]
            if pending is None or int(pending) != cycle:
                raise ValueError("backup completion identity")
            with db:
                if status in ("succeeded", "partial", "failed"):
                    db.execute(
                        "UPDATE backup_cycle SET high_water=cycle,cycle=NULL,state=?,"
                        "attempt=?,manifest_hash=?",
                        (status, attempt, sha),
                    )
                else:
                    db.execute("UPDATE backup_cycle SET state='uncertain'")
            self._refresh(time.time())

    def close(self) -> None:
        if self.db is not None:
            self._owned().close()
            self.db = None
