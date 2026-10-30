"""Closed release values and a private durable fake-effect harness.

No host executor or public opener exists here. Config values are not custody proof.
The fake harness exercises ordering/restart rules, never authorizes a real deployment.
"""

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Literal, cast

from heterodyne.actions.codec import (
    _ALIAS,  # pyright: ignore[reportPrivateUsage] - exact public deploy alias contract
    _ID,  # pyright: ignore[reportPrivateUsage] - shared bounded identities
    InvalidAction,
    _decode,  # pyright: ignore[reportPrivateUsage] - reuse bounded closed codec
    encode_record,
)
from heterodyne.actions.contracts import Record

STEPS = (
    "preflight",
    "online_guard",
    "owner_exclusion",
    "disable_stop",
    "stopped_guard",
    "install",
    "held_acceptance",
)
FLAGS = ("held-acceptance", "zero-new-quarantine")
_HEX = re.compile(r"[0-9a-f]{64}")
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_LINK = re.compile(r"https://github\.com/Epiphytic/[A-Za-z0-9_-]+/blob/[0-9a-f]{40}/[A-Za-z0-9_./-]+\.md")


class Target(Record, frozen=True):
    alias: str
    target_id: str
    definition_revision: str
    service_user: str
    release_id: str
    unit_id: str
    state_id: str


class Artifacts(Record, frozen=True):
    source_commit: str
    source_tree: str
    bootstrap_sha256: str
    release_sha256: str
    installer_sha256: str
    guard_sha256: str
    units_sha256: str
    dependency_lock_sha256: str


class AuthorityPins(Record, frozen=True):
    adapter_sha256: str
    contract_sha256: str
    codec_sha256: str
    records_sha256: str
    core_sha256: str
    admind_sha256: str
    interpreter_sha256: str
    runtime_adapter_sha256: str
    admind_interpreter_sha256: str
    admind_package_sha256: str
    admind_source_sha256: str
    harness_package_sha256: str


class Recovery(Record, frozen=True):
    retained_release_sha256: str
    deadline_ns: int
    scope: Literal["safe-stop", "held-restore"]
    independent_route_id: str


class Release(Record, frozen=True):
    schema: Literal["heterodyne-release.v1"]
    recipe: Literal["heterodyne_release.v1"]
    approval_id: str
    execution_id: str
    nonce: str
    target: Target
    artifacts: Artifacts
    authority_sources: AuthorityPins
    expected_initial_sha256: str
    expected_manager_sha256: str
    design_links: tuple[str, ...]
    deviations: tuple[str, ...]
    flags: tuple[Literal["held-acceptance", "zero-new-quarantine"], ...]
    not_before_ns: int
    deadline_ns: int
    recovery: Recovery
    receipt_id: str
    packet_commit: str
    packet_path: str
    outage: Literal["refuse"] = "refuse"


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InvalidAction(reason)


def validate(release: Release, targets: tuple[Target, ...]) -> None:
    """Resolve deploy{target} through an exact finite protected mapping projection."""
    # Round-trip also refuses malformed direct host constructions, not only JSON input.
    _decode(encode_record(release), Release)
    _require(len(targets) <= 32 and len({t.alias for t in targets}) == len(targets), "target-map")
    _require(bool(_ALIAS.fullmatch(release.target.alias)), "deploy-alias")
    matches = [t for t in targets if t.alias == release.target.alias]
    _require(matches == [release.target], "target-mapping-drift")
    for value in release.target.__struct_fields__:
        _require(bool(_ID.fullmatch(getattr(release.target, value))), "target-identifier")
    for value in (
        release.approval_id,
        release.execution_id,
        release.nonce,
        release.receipt_id,
        release.recovery.independent_route_id,
    ):
        _require(bool(_ID.fullmatch(value)), "release-identifier")
    a = release.artifacts
    _require(bool(_OID.fullmatch(a.source_commit)) and bool(_OID.fullmatch(a.source_tree)), "source-object")
    hashes = (
        a.bootstrap_sha256,
        a.release_sha256,
        a.installer_sha256,
        a.guard_sha256,
        a.units_sha256,
        a.dependency_lock_sha256,
        release.expected_initial_sha256,
        release.expected_manager_sha256,
        release.recovery.retained_release_sha256,
        *(getattr(release.authority_sources, f) for f in release.authority_sources.__struct_fields__),
    )
    _require(all(_HEX.fullmatch(v) for v in hashes), "release-digest")
    _require(
        1 <= len(release.design_links) <= 8
        and all(_LINK.fullmatch(v) and ".." not in v.split("/") for v in release.design_links),
        "design-links",
    )
    _require(
        1 <= len(release.deviations) <= 16
        and len(set(release.deviations)) == len(release.deviations)
        and all(v.strip() and len(v) <= 1024 and v.isprintable() for v in release.deviations),
        "explicit-deviations",
    )
    _require(bool(re.fullmatch(r"[0-9a-f]{40}", release.packet_commit)), "packet-commit")
    _require(
        bool(re.fullmatch(r"docs/[A-Za-z0-9_./-]+\.json", release.packet_path))
        and ".." not in release.packet_path.split("/")
        and str(Path(release.packet_path)) == release.packet_path,
        "packet-path",
    )
    _require(release.flags == FLAGS, "fixed-recipe-flags")
    _require(
        0 <= release.not_before_ns < release.deadline_ns <= release.recovery.deadline_ns
        and release.recovery.deadline_ns - release.not_before_ns <= 24 * 3600 * 10**9,
        "finite-window",
    )


def decode_release(raw: bytes, targets: tuple[Target, ...]) -> Release:
    release = _decode(raw, Release)
    validate(release, targets)
    return release


def digest(release: Release) -> str:
    return hashlib.sha256(encode_record(release)).hexdigest()


class FakeEffects:
    """Explicit disposable target; cannot spawn, install, signal or contact a service."""

    def __init__(self, initial: str):
        self.state = initial
        self.calls: list[str] = []

    def apply(self, step: str) -> str:
        self.calls.append(step)
        self.state = hashlib.sha256((self.state + ":" + step).encode()).hexdigest()
        return self.state


class _FixtureJournal:  # pyright: ignore[reportUnusedClass] - private fixture only
    """Private fixture runner. SQLite FULL commits intent before each fake effect.

    A restart after an issued step is uncertain and never reissues it. Reconciliation
    requires a separately observed exact post-state; time alone never frees a target.
    Host filesystem/UID trust and outage execution are deliberately unavailable.
    """

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
                execution TEXT PRIMARY KEY, approval TEXT UNIQUE NOT NULL, nonce TEXT UNIQUE NOT NULL,
                target TEXT NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL,
                phase TEXT NOT NULL, ordinal INTEGER NOT NULL, observed TEXT NOT NULL,
                anticipated TEXT, cancelled INTEGER NOT NULL DEFAULT 0);
            CREATE UNIQUE INDEX IF NOT EXISTS active_target ON jobs(target)
                WHERE phase IN ('executing','uncertain');
            CREATE TABLE IF NOT EXISTS events(
                execution TEXT NOT NULL, ordinal INTEGER NOT NULL, phase TEXT NOT NULL,
                observed TEXT NOT NULL, PRIMARY KEY(execution,ordinal,phase));
        """)

    def close(self) -> None:
        self.db.close()

    def _begin(self) -> None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise InvalidAction("journal-unavailable") from exc

    def enroll(self, release: Release, targets: tuple[Target, ...], now_ns: int) -> None:
        validate(release, targets)
        _require(release.not_before_ns <= now_ns < release.deadline_ns, "forward-window")
        self._begin()
        try:
            old = self.db.execute(
                "SELECT digest FROM jobs WHERE execution=?", (release.execution_id,)
            ).fetchone()
            if old:
                _require(old[0] == digest(release), "one-use-payload-drift")
            else:
                self.db.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,'executing',0,?,NULL,0)",
                    (
                        release.execution_id,
                        release.approval_id,
                        release.nonce,
                        release.target.target_id,
                        digest(release),
                        encode_record(release),
                        release.expected_initial_sha256,
                    ),
                )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            raise InvalidAction("one-use-or-target-conflict") from None
        except sqlite3.Error as exc:
            self.db.rollback()
            raise InvalidAction("journal-unavailable") from exc
        except BaseException:
            self.db.rollback()
            raise

    def advance(
        self,
        release: Release,
        targets: tuple[Target, ...],
        effects: FakeEffects,
        now_ns: int,
        *,
        crash_after_effect: bool = False,
    ) -> str:
        _require(type(effects) is FakeEffects, "fixture-effects-only")
        validate(release, targets)
        self._begin()
        try:
            row = self.db.execute(
                "SELECT digest,phase,ordinal,observed,cancelled FROM jobs WHERE execution=?",
                (release.execution_id,),
            ).fetchone()
            if row is None or row[0] != digest(release):
                raise InvalidAction("unknown-execution")
            _, phase, ordinal, observed, cancelled = cast(tuple[str, str, int, str, int], row)
            _require(phase == "executing" and not cancelled, "forward-hold")
            _require(release.not_before_ns <= now_ns < release.deadline_ns, "forward-window")
            _require(effects.state == observed, "unexpected-transition")
            step = STEPS[ordinal]
            anticipated = hashlib.sha256((observed + ":" + step).encode()).hexdigest()
            self.db.execute(
                "INSERT INTO events VALUES(?,?,'issued',?)", (release.execution_id, ordinal, observed)
            )
            self.db.execute(
                "UPDATE jobs SET phase='uncertain',anticipated=? WHERE execution=?",
                (anticipated, release.execution_id),
            )
            self.db.commit()
        except sqlite3.Error as exc:
            self.db.rollback()
            raise InvalidAction("journal-unavailable") from exc
        except BaseException:
            self.db.rollback()
            raise
        result = effects.apply(step)
        if crash_after_effect:
            return "uncertain"
        self.reconcile(release, result)
        return "succeeded" if ordinal + 1 == len(STEPS) else "executing"

    def reconcile(self, release: Release, observed: str) -> None:
        self._begin()
        try:
            row = self.db.execute(
                "SELECT digest,phase,ordinal,anticipated FROM jobs WHERE execution=?", (release.execution_id,)
            ).fetchone()
            if row is None or row[0] != digest(release) or row[1] != "uncertain":
                raise InvalidAction("reconcile-state")
            _, _, ordinal, anticipated = cast(tuple[str, str, int, str], row)
            _require(observed == anticipated, "uncertain-hold")
            self.db.execute(
                "INSERT INTO events VALUES(?,?,'completed',?)", (release.execution_id, ordinal, observed)
            )
            phase = "succeeded" if ordinal + 1 == len(STEPS) else "executing"
            self.db.execute(
                "UPDATE jobs SET phase=?,ordinal=?,observed=?,anticipated=NULL WHERE execution=?",
                (phase, ordinal + 1, observed, release.execution_id),
            )
            self.db.commit()
        except sqlite3.Error as exc:
            self.db.rollback()
            raise InvalidAction("journal-unavailable") from exc
        except BaseException:
            self.db.rollback()
            raise

    def cancel(self, execution_id: str) -> None:
        # Fixture models a pre-authorized independent stop; no native operator route is installed.
        try:
            changed = self.db.execute("UPDATE jobs SET cancelled=1 WHERE execution=?", (execution_id,))
        except sqlite3.Error as exc:
            raise InvalidAction("journal-unavailable") from exc
        _require(changed.rowcount == 1, "unknown-execution")


def open_host_executor() -> None:
    raise InvalidAction("HOLD: protected host custody and independent recovery qualification required")
