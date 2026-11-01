"""Root global one-use admission ledger; no installer or consent-file importer.

Only the private live source-qualified parent composition may call admission.
This module grants no public capability and issues no effect. A replay, crash or
lost child response cannot erase a native consumption or permit another attempt.
"""

# pyright: reportPrivateUsage=false

import contextlib
import hashlib
import os
import sqlite3
import stat
import time
from collections.abc import Generator
from pathlib import Path

from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment_admission_binding import _correlate
from heterodyne.actions.deployment_admission_peer import _ParentChannel
from heterodyne.actions.deployment_host_store import _private_root
from heterodyne.actions.deployment_outage import decode_packet, packet_digest
from heterodyne.actions.deployment_producer_process import _profile_bytes
from heterodyne.actions.deployment_root_helper import _read

_ROOT = Path('/var/lib/heterodyne/deployment-owner')


_SCHEMA = '''
                CREATE TABLE IF NOT EXISTS admissions(
                  packet TEXT PRIMARY KEY, execution TEXT NOT NULL UNIQUE,
                  approval TEXT NOT NULL UNIQUE, nonce TEXT NOT NULL UNIQUE,
                  use_key TEXT NOT NULL UNIQUE, target TEXT NOT NULL,
                  original BLOB NOT NULL, consumed BLOB NOT NULL,
                  phase TEXT NOT NULL CHECK(phase IN
                    ('ready','issued','complete','cancelled','failed-no-effect')));
                CREATE UNIQUE INDEX IF NOT EXISTS admission_active_target
                  ON admissions(target) WHERE phase NOT IN ('complete','failed-no-effect');
                CREATE TABLE IF NOT EXISTS admission_events(
                  packet TEXT NOT NULL, ordinal INTEGER NOT NULL,
                  phase TEXT NOT NULL CHECK(phase IN ('issued','completed')),
                  observed TEXT NOT NULL, PRIMARY KEY(packet,ordinal,phase));
            '''

class _AdmissionJournal:  # pyright: ignore[reportUnusedClass] - private owner composition only
    """The sole root global admission domain, separate from UID898 diagnostics.

    No path argument: callers cannot choose a fresh DB to reset one-use state.
    Existing root-equivalent administrators remain trusted recovery principals.
    SQLite DELETE journaling + FULL sync retain issue uncertainty across restart.
    """

    def __init__(self):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise InvalidAction('admission-journal-root-required')
        self.fd = _private_root(_ROOT)
        self.db: sqlite3.Connection
        anchor = -1
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            anchor = os.open('admission.db', flags, 0o600, dir_fd=self.fd)
            before = os.fstat(anchor)
            if (
                not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
            ):
                raise InvalidAction('admission-journal-custody')
            self.db = sqlite3.connect(f'/proc/self/fd/{self.fd}/admission.db',
                                      isolation_level=None, timeout=5)
            after = os.stat('admission.db', dir_fd=self.fd, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise InvalidAction('admission-journal-replaced')
            # Refuse foreign schema before any schema or journal-mode write.
            if not self.db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone():
                self.db.executescript(_SCHEMA)
            # Compare the complete source-owned schema, including UNIQUE/CHECK
            # constraints and partial target index; root path custody alone is
            # not a schema version or migration receipt.
            expected = sqlite3.connect(':memory:')
            try:
                expected.executescript(_SCHEMA)
                query = ("SELECT type,name,sql FROM sqlite_master "
                         "WHERE substr(name,1,7) != 'sqlite_' ORDER BY type,name")
                def normalized(db: sqlite3.Connection) -> list[tuple[str, str, str]]:
                    return [(kind, name, ' '.join(sql.split()))
                            for kind, name, sql in db.execute(query)]
                if normalized(self.db) != normalized(expected):
                    raise InvalidAction('admission-journal-schema')
            finally:
                expected.close()
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            os.fsync(anchor)
            os.fsync(self.fd)
        except BaseException:
            if hasattr(self, 'db'):
                self.db.close()
            os.close(self.fd)
            raise
        finally:
            if anchor >= 0:
                os.close(anchor)

    def close(self) -> None:
        self.db.close()
        os.close(self.fd)

    @contextlib.contextmanager
    def _transaction(self) -> Generator[None]:
        try:
            self.db.execute('BEGIN IMMEDIATE')
            yield
            self.db.commit()
        except sqlite3.IntegrityError as exc:
            self.db.rollback()
            raise InvalidAction('admission-one-use-or-target-conflict') from exc
        except sqlite3.Error as exc:
            self.db.rollback()
            raise InvalidAction('admission-journal-unavailable') from exc
        except BaseException:
            self.db.rollback()
            raise

    def admit(self, channel: _ParentChannel) -> str:
        """Require actual owned transport; no receipt/data parameter exists.

        Durable insertion is intentionally never idempotent: native consent was
        consumed before handoff. Duplicate/lost response is retained UNKNOWN,
        never a fresh authority result, even if exact original bytes match.
        """
        if type(channel) is not _ParentChannel:
            raise InvalidAction('admission-owned-parent-required')
        consumed = _correlate(channel)
        # Use the exact original already correlated to native consumption.
        # Retained producer input is independently rehashed before parsing;
        # never hash one descriptor read and parse a second read.
        original = bytes.fromhex(consumed.original.operation)
        producer_raw = _read(13, 32768)
        if hashlib.sha256(producer_raw).hexdigest() != channel.profile.producer_profile_sha256:
            raise InvalidAction('admission-retained-profile-binding')
        producer = _profile_bytes(producer_raw)
        packet = decode_packet(original, producer.targets)
        in_window = packet.outage.not_before_ns <= time.time_ns() < packet.outage.deadline_ns
        key = hashlib.sha256(encode_record(consumed.consumption_key)).hexdigest()
        digest = packet_digest(packet)
        channel.check()
        with self._transaction():
            self.db.execute(
                "INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,?)",
                (digest, packet.release.execution_id, packet.release.approval_id,
                 packet.release.nonce, key, packet.release.target.target_id,
                 original, channel.consumed_raw, 'ready' if in_window else 'failed-no-effect'),
            )
        # Native consent was consumed before handoff. Never roll its durable
        # reservation back merely because the parent ceases after insertion.
        try:
            channel.check()
            if not packet.outage.not_before_ns <= time.time_ns() < packet.outage.deadline_ns:
                raise InvalidAction('admission-forward-window')
        except (InvalidAction, OSError):
            if in_window:
                self.fail_unissued(channel, digest)
            raise
        return digest

    def fail_unissued(self, channel: _ParentChannel, packet: str) -> None:
        """Settle only a genuinely never-issued capsule, preserving all use keys.

        This cannot settle an issued/unknown effect or release its target lock.
        The no-effect probe uses it after actual admission, never a success claim.
        """
        if type(channel) is not _ParentChannel:
            raise InvalidAction('admission-owned-parent-required')
        # Negative settlement needs no live parent: the authoritative journal
        # itself proves no issued event exists. It never grants an effect.
        with self._transaction():
            changed = self.db.execute(
                "UPDATE admissions SET phase='failed-no-effect' WHERE packet=? AND phase='ready' "
                "AND NOT EXISTS(SELECT 1 FROM admission_events WHERE packet=?)",
                (packet, packet),
            ).rowcount
            if changed != 1:
                raise InvalidAction('admission-never-issued-proof-unavailable')

    def recover_never_issued(self) -> tuple[str, ...]:
        """Root operator recovery: release only SQL-proven never-issued holds.

        No channel, receipt-file, target selector or replacement authority. The
        root global journal is authoritative for its own absent issued events.
        A concurrent issuer must check the ready phase in its write transaction;
        any already-issued/ambiguous effect stays held. Never resets use keys.
        """
        with self._transaction():
            rows = self.db.execute(
                "SELECT packet FROM admissions WHERE phase='ready' AND NOT EXISTS "
                "(SELECT 1 FROM admission_events WHERE admission_events.packet=admissions.packet)",
            ).fetchall()
            self.db.executemany(
                "UPDATE admissions SET phase='failed-no-effect' WHERE packet=? AND phase='ready'", rows,
            )
        return tuple(row[0] for row in rows)
