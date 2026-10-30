"""Private durable finite ledger, not a host runner or an authority constructor.

Only a future protected factory may supply admitted capsules and independently
authenticated recovery values. Methods record data; they never execute effects,
authenticate a caller, or release a cancelled/uncertain target by assertion.
"""

# pyright: reportPrivateUsage=false
# One private deployment implementation; no configurable store or authority reader.

import contextlib
import os
import sqlite3
import stat
from collections.abc import Generator
from typing import Literal, cast

from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment import Target
from heterodyne.actions.deployment_host_store import _ProtectedStore
from heterodyne.actions.deployment_outage import (
    OUTAGE_STEPS,
    DeploymentPacket,
    RecoveryCommand,
    decode_recovery,
    packet_digest,
    validate_packet,
)

_ACTIVE = (
    "CREATE UNIQUE INDEX active_target ON jobs(target) "
    "WHERE phase NOT IN ('succeeded','failed-no-effect')"
)
_LEGACY_ACTIVE = "CREATE UNIQUE INDEX active_target ON jobs(target) WHERE phase != 'succeeded'"


class NeverIssuedFailure(Record, frozen=True):
    """Journal-owned causal receipt, not proof for any already-issued operation."""

    schema: Literal["heterodyne-never-issued-failure.v1"]
    packet_sha256: str
    execution_id: str
    outcome: Literal["failed-no-effect"]


class _OutageJournal:  # pyright: ignore[reportUnusedClass] - dormant protected factory ledger
    """Executor-owned SQLite ledger anchored to an already checked directory.

    SQLite uses the anchored /proc descriptor path, never a caller-supplied DB
    filename. The fixed database and all sidecars stay inside the private store.
    This is source-only: possession of this class is not a deployment capability.
    """

    def __init__(self, store: _ProtectedStore):
        if type(store) is not _ProtectedStore:
            raise InvalidAction("protected-store-required")
        name = "outage-journal.db"
        self.root_fd = os.dup(store.fd)
        self.db: sqlite3.Connection
        fd = -1
        try:
            fd = os.open(
                name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                0o600, dir_fd=self.root_fd,
            )
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            ):
                raise InvalidAction("protected-journal-file")
            self.db = sqlite3.connect(f"/proc/self/fd/{self.root_fd}/{name}", isolation_level=None, timeout=0)
            after = os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            if (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino):
                self.db.close()
                raise InvalidAction("protected-journal-changed")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs(
                  execution TEXT PRIMARY KEY, approval TEXT UNIQUE, nonce TEXT UNIQUE,
                  target TEXT NOT NULL, packet TEXT NOT NULL, payload BLOB NOT NULL,
                  ordinal INTEGER NOT NULL, phase TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  execution TEXT NOT NULL, ordinal INTEGER NOT NULL, phase TEXT NOT NULL,
                  observed TEXT NOT NULL, PRIMARY KEY(execution,ordinal,phase));
                CREATE TABLE IF NOT EXISTS recovery(
                  nonce TEXT PRIMARY KEY, execution TEXT NOT NULL, payload BLOB NOT NULL);
            """)
            current = self._index_sql()
            if current != " ".join(_ACTIVE.split()):
                # Ordinary/status opens retain read concurrency. A migration
                # rechecks under the same transaction before changing schema.
                with self._transaction():
                    current = self._index_sql()
                    if current == " ".join(_LEGACY_ACTIVE.split()):
                        self.db.execute("DROP INDEX active_target")
                        self.db.execute(_ACTIVE)
                    elif current is None:
                        self.db.execute(_ACTIVE)
                    elif current != " ".join(_ACTIVE.split()):
                        raise InvalidAction("outage-journal-index-schema")
            os.fsync(fd)
            os.fsync(self.root_fd)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            os.close(self.root_fd)
            raise
        finally:
            if fd >= 0:
                os.close(fd)

    def _index_sql(self) -> str | None:
        row = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='active_target'",
        ).fetchone()
        return " ".join(row[0].split()) if row else None

    def close(self) -> None:
        self.db.close()
        os.close(self.root_fd)

    @contextlib.contextmanager
    def _transaction(self) -> Generator[None]:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise InvalidAction("outage-one-use-or-target-conflict") from exc
        except sqlite3.Error as exc:
            self.db.rollback()
            raise InvalidAction("outage-journal-unavailable") from exc
        except BaseException:
            self.db.rollback()
            raise

    def _row(self, packet: DeploymentPacket) -> tuple[int, str]:
        row = self.db.execute(
            "SELECT packet,ordinal,phase FROM jobs WHERE execution=?", (packet.release.execution_id,),
        ).fetchone()
        if not row or row[0] != packet_digest(packet):
            raise InvalidAction("outage-unknown-or-payload-drift")
        return cast(tuple[int, str], (row[1], row[2]))

    def record_capsule(self, packet: DeploymentPacket, targets: tuple[Target, ...], now_ns: int) -> None:
        validate_packet(packet, targets)
        if not packet.outage.not_before_ns <= now_ns < packet.outage.deadline_ns:
            raise InvalidAction("outage-forward-window")
        with self._transaction():
            old = self.db.execute(
                "SELECT packet FROM jobs WHERE execution=?", (packet.release.execution_id,),
            ).fetchone()
            if old:
                self._row(packet)
                return
            r = packet.release
            self.db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,0,'ready')",
                (r.execution_id, r.approval_id, r.nonce, r.target.target_id, packet_digest(packet),
                 encode_record(packet)),
            )

    def issue(self, packet: DeploymentPacket, targets: tuple[Target, ...], now_ns: int, observed: str) -> str:
        validate_packet(packet, targets)
        with self._transaction():
            ordinal, phase = self._row(packet)
            if phase != "ready" or ordinal >= len(OUTAGE_STEPS):
                raise InvalidAction("outage-forward-hold")
            if not packet.outage.not_before_ns <= now_ns < packet.outage.deadline_ns:
                raise InvalidAction("outage-forward-window")
            step = packet.outage.transitions[ordinal]
            if observed != step.before_sha256:
                raise InvalidAction("outage-unexpected-before")
            self.db.execute(
                "INSERT INTO events VALUES(?,?,'issued',?)", (packet.release.execution_id, ordinal, observed),
            )
            self.db.execute(
                "UPDATE jobs SET phase='uncertain' WHERE execution=?", (packet.release.execution_id,),
            )
        # Intent is durable before an actual protected runner could receive the
        # fixed step. Calling again, even after restart, never reissues that step.
        return step.step

    def complete(self, packet: DeploymentPacket, targets: tuple[Target, ...], observed: str) -> None:
        validate_packet(packet, targets)
        with self._transaction():
            ordinal, phase = self._row(packet)
            issued = self.db.execute(
                "SELECT 1 FROM events WHERE execution=? AND ordinal=? AND phase='issued'",
                (packet.release.execution_id, ordinal),
            ).fetchone()
            if (
                phase not in {"uncertain", "recovery-hold"} or not issued
                or ordinal >= len(OUTAGE_STEPS)
                or observed != packet.outage.transitions[ordinal].after_sha256
            ):
                raise InvalidAction("outage-reconciliation-hold")
            self.db.execute(
                "INSERT INTO events VALUES(?,?,'completed',?)",
                (packet.release.execution_id, ordinal, observed),
            )
            self.db.execute(
                "UPDATE jobs SET ordinal=?,phase=? WHERE execution=?",
                (ordinal + 1, "recovery-hold" if phase == "recovery-hold" else (
                    "succeeded" if ordinal + 1 == len(OUTAGE_STEPS) else "ready"),
                 packet.release.execution_id),
            )

    def request_recovery(
        self, packet: DeploymentPacket, targets: tuple[Target, ...], command: RecoveryCommand,
        now_ns: int, observed: str,
    ) -> None:
        decode_recovery(encode_record(command), packet, targets)
        if not command.not_before_ns <= now_ns < command.deadline_ns:
            raise InvalidAction("outage-recovery-window")
        if command.expected_observation_sha256 != observed:
            raise InvalidAction("outage-recovery-observation")
        with self._transaction():
            _, phase = self._row(packet)
            if phase in {"succeeded", "failed-no-effect"}:
                raise InvalidAction("outage-recovery-terminal")
            self.db.execute(
                "INSERT INTO recovery VALUES(?,?,?)",
                (command.command_nonce, packet.release.execution_id, encode_record(command)),
            )
            # No 'cancelled' terminal phase: retain target exclusion until the
            # actual protected cessation/newest-state producer is integrated.
            self.db.execute(
                "UPDATE jobs SET phase='recovery-hold' WHERE execution=?", (packet.release.execution_id,),
            )

    def fail_never_issued(self, packet: DeploymentPacket, targets: tuple[Target, ...]) -> NeverIssuedFailure:
        """Record real absence of issued effects; no caller proof/exit code input.

        This only settles a capsule from which this journal has never returned a
        step. Once any intent was issued, even an installer exit before an
        apparent change requires a separately qualified no-effect producer.
        """
        validate_packet(packet, targets)
        with self._transaction():
            ordinal, phase = self._row(packet)
            issued = self.db.execute(
                "SELECT 1 FROM events WHERE execution=? AND phase='issued' LIMIT 1",
                (packet.release.execution_id,),
            ).fetchone()
            if ordinal != 0 or phase not in {"ready", "recovery-hold"} or issued:
                raise InvalidAction("outage-never-issued-proof-unavailable")
            receipt = NeverIssuedFailure(
                "heterodyne-never-issued-failure.v1", packet_digest(packet),
                packet.release.execution_id, "failed-no-effect",
            )
            self.db.execute(
                "INSERT INTO events VALUES(?,0,'failed-no-effect',?)",
                (packet.release.execution_id, encode_record(receipt).decode("ascii")),
            )
            self.db.execute(
                "UPDATE jobs SET phase='failed-no-effect' WHERE execution=?", (packet.release.execution_id,),
            )
        return receipt
