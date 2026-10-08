"""The journal upgrade, version 1 to 2 (AU-3 design §3).

`cli.run` calls `run` when `Journal()` raises JournalNeedsUpgrade, under the instance lock and before
`assemble`. Three steps:

1. Gather, read-only: the legacy beads (every journal row not closed or dropped) with their open ops, each
   bead as bd shows it, and per adapter the accounts facts. A queue failure aborts with the file untouched.
2. Decide, a pure function: `adopt` gives each legacy bead `adopted` or `held`, or no row for a bead proven
   never to have launched.
3. Commit, one SQLite transaction on the v1 file: the v2 tables (one `execute` each, never `executescript`,
   which would commit first), the adoption rows, the adopted entries, the version. Any exception rolls
   back, so a crash leaves version 1 intact; the `.v1.bak` copy taken first is for the operator only.

The facts are fixed here: an account configured later never un-adopts a session (D2's "at the upgrade").
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.config.capabilities import LOGIN_FILES
from heterodyne.config.errors import ConfigError
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import DEFAULT, Accounts
from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable, ClaimView, RecordUnreadable, SessionRecord
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import (
    BUSY_TIMEOUT,
    LAUNCH_N,
    LAUNCH_SQL,
    SCHEMA_VERSION,
    V2_META,
    V2_TABLES,
    Adoption,
    JournalCorrupt,
    backup_to,
    check_v1,
    launch_row,
    now,
)
from heterodyne.wsd.launches import LAUNCHED, LaunchEntry, LaunchesUnreadable
from heterodyne.wsd.states import BeadState

ABORTED = "beads unreachable during the journal upgrade; retrying is safe"
# Plan 3 runs the launch guard only after a pickup's `worktree` step: before it, nothing launched.
PRE_LAUNCH_STATES = (BeadState.CLAIMING.value, BeadState.STARTING.value)
PRE_LAUNCH_STEPS = ("intent", "claimed", "placed")
CODEX_THREAD_FIELD = "thread_id"     # plan 4's field on the record; absent until plan 4 lands


class UpgradeAborted(Exception):
    """The beads could not be read: the file is untouched and still version 1."""


@dataclass(frozen=True)
class AdapterFacts:
    configured: tuple[str, ...]
    default_resolves: bool
    default_key: str | None          # None: the default login can't be resolved at all


@dataclass(frozen=True)
class Legacy:
    """One legacy bead as gathered: its journal row, its open op, and the bead as bd showed it."""
    ws: str
    bead: str
    state: str
    op: tuple[str, str] | None                   # (kind, step) of its open op
    record: SessionRecord | Literal["missing", "unreadable"]
    adapter: str | None                           # the record's profile's adapter; None if it's gone
    thread_id: str | None
    launches: tuple[LaunchEntry, ...] | None      # None: present but unreadable
    claim: ClaimView


@dataclass(frozen=True)
class Verdict:
    ws: str
    bead: str
    session_key: str | None
    verdict: Literal["adopted", "held"]
    detail: str
    facts: str
    entry: LaunchEntry | None


# --- 1. gather ---

type Gathered = tuple[list[Legacy], dict[str, AdapterFacts]]


def gather(path: Path, beads: BeadsAdapter, accounts: Accounts) -> Gathered:
    try:
        rows = _legacy_rows(path)
        adapters = {a: _adapter_facts(accounts, a) for a in LOGIN_FILES}
        return [_legacy(beads, accounts, *row) for row in rows], adapters
    except (BeadsUnavailable, OSError):
        raise UpgradeAborted(ABORTED) from None


def _legacy_rows(path: Path) -> list[tuple[str, str, str, tuple[str, str] | None]]:
    db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, isolation_level=None,
                         timeout=BUSY_TIMEOUT)
    try:
        db.execute("BEGIN")
        if not check_v1(db):
            raise JournalCorrupt("the journal changed before its upgrade")
        ops = {(str(r[0]), str(r[1])): (str(r[2]), str(r[3])) for r in db.execute(
            "SELECT ws, bead, kind, step FROM ops WHERE status = 'open'")}
        rows = db.execute("SELECT ws, bead, state FROM beads WHERE state NOT IN ('closed', 'dropped') "
                          "ORDER BY ws, bead").fetchall()
        db.execute("COMMIT")
        return [(str(ws), str(bead), str(state), ops.get((str(ws), str(bead)))) for ws, bead, state in rows]
    finally:
        db.close()


def _adapter_facts(accounts: Accounts, adapter: str) -> AdapterFacts:
    try:
        key: str | None = accounts.current_key(adapter, DEFAULT)
    except ConfigError:
        key = None
    return AdapterFacts(tuple(accounts.configured(adapter)), accounts.login_resolves(adapter, DEFAULT), key)


def _legacy(beads: BeadsAdapter, accounts: Accounts, ws: str, bead: str, state: str,
            op: tuple[str, str] | None) -> Legacy:
    shown = beads.show(ws, bead)
    record: SessionRecord | Literal["missing", "unreadable"]
    try:
        record = shown.record() or "missing"
    except RecordUnreadable:
        record = "unreadable"
    try:
        launches: tuple[LaunchEntry, ...] | None = shown.launches().entries
    except LaunchesUnreadable:
        launches = None
    adapter = accounts.adapter(record.profile) if isinstance(record, SessionRecord) else None
    return Legacy(ws, bead, state, op, record, adapter, shown.record_field(CODEX_THREAD_FIELD), launches,
                  beads.read_claim(ws, bead))


# --- 2. decide ---

def adopt(legacy: Sequence[Legacy], adapters: Mapping[str, AdapterFacts], at: str) -> list[Verdict]:
    """A verdict per legacy bead, except one proven never to have launched (§3, step 2)."""
    verdicts: list[Verdict] = []
    for item in legacy:
        exempt = (item.state in PRE_LAUNCH_STATES and item.op is not None and item.op[0] == "pickup"
                  and item.op[1] in PRE_LAUNCH_STEPS)
        if exempt:
            continue
        entry, failed = _decide(item, adapters, at)
        rec = item.record if isinstance(item.record, SessionRecord) else None
        facts = json.dumps(_facts(item, adapters), sort_keys=True)
        if failed is None:
            verdicts.append(Verdict(item.ws, item.bead, entry.session_key if entry else None, "adopted",
                                    "adopted at the upgrade", facts, entry))
        else:
            verdicts.append(Verdict(item.ws, item.bead, rec.session_key if rec else None, "held", failed,
                                    facts, None))
    return verdicts


def adopted_entry(ws: str, bead: str, rec: SessionRecord, adapter: str, key: str, at: str,
                  thread_id: str | None) -> LaunchEntry:
    """Generation 1 of a legacy session: `default`, launched, adopted. Claude's native ID is the session
    key (plan 3 used it as one); Codex's is plan 4's thread ID if recorded, else empty, set once later."""
    native = rec.session_key if adapter == "claude-code" else thread_id
    return LaunchEntry(rec.session_key, 1, ws, bead, rec.role, rec.profile, DEFAULT, key, "", True, at,
                       dispatched_at=at, native_id=native, outcome=LAUNCHED)


def same_adoption(found: LaunchEntry, wanted: LaunchEntry) -> bool:
    """Whether a bead entry is this adopted entry, its timestamps aside (written by an earlier attempt)."""
    stamps = {"journaled_at": wanted.journaled_at, "dispatched_at": wanted.dispatched_at}
    return msgspec.structs.replace(found, **stamps) == wanted


type Decided = tuple[LaunchEntry | None, str | None]


def _decide(item: Legacy, adapters: Mapping[str, AdapterFacts], at: str) -> Decided:
    rec = item.record
    if rec == "missing":
        return None, "no session record; launch history unknown"
    if rec == "unreadable":
        return None, "the session record does not parse; launch history unknown"
    if rec.session_key != ids.role_session(item.bead, rec.role, rec.profile):
        return None, "the record's session key is not the bead's role session"
    if item.adapter is None or item.adapter not in adapters:
        return None, f"profile {rec.profile} no longer exists"
    facts = adapters[item.adapter]
    if facts.configured:
        return None, f"accounts were configured for {item.adapter} at the upgrade"
    if not facts.default_resolves or facts.default_key is None:
        return None, f"the {item.adapter} default login does not resolve"
    return check_agreement(item.claim, item.op, item.launches,
                           adopted_entry(item.ws, item.bead, rec, item.adapter, facts.default_key, at,
                                         item.thread_id))


def check_agreement(claim: ClaimView, op: tuple[str, str] | None, launches: tuple[LaunchEntry, ...] | None,
                    entry: LaunchEntry) -> Decided:
    """Condition (e): journal and bead agree. The entry to use (the bead's own, if it already has exactly
    this one), or the failed condition."""
    if claim is not ClaimView.OURS:
        return None, f"the claim reads {claim.value}, not ours"
    if op is not None:
        return None, f"a {op[0]} is open at step {op[1]}; its launch state is unknown"
    if launches is None:
        return None, "wsd_launches does not parse"
    if not launches:
        return entry, None
    if len(launches) == 1 and same_adoption(launches[0], entry):
        return launches[0], None
    return None, "the bead already has launch entries"


def _facts(item: Legacy, adapters: Mapping[str, AdapterFacts]) -> dict[str, object]:
    rec = item.record
    return {
        "adapters": {a: {"configured": list(f.configured), "default_resolves": f.default_resolves,
                         "default_key": f.default_key} for a, f in adapters.items()},
        "adapter": item.adapter,
        "state": item.state,
        "claim": item.claim.value,
        "op": None if item.op is None else {"kind": item.op[0], "step": item.op[1]},
        "record": msgspec.to_builtins(rec) if isinstance(rec, SessionRecord) else rec,
        "launches": None if item.launches is None else len(item.launches),
    }


def adoption_adapter(adoption: Adoption) -> str | None:
    """The session's adapter as the upgrade saw it; None if it had no readable record then."""
    found = json.loads(adoption.facts).get("adapter")
    return found if isinstance(found, str) else None


def configured_at_upgrade(facts: str, adapter: str | None) -> bool:
    """Whether the upgrade-time facts show accounts configured for `adapter`. An unknown adapter (no
    record at the upgrade) counts if any adapter had them: the session's login can't be told."""
    found = json.loads(facts).get("adapters", {})
    if adapter is not None and adapter in found:
        return bool(found[adapter]["configured"])
    return any(f["configured"] for f in found.values())


# --- 3. commit ---

def commit(path: Path, verdicts: Sequence[Verdict], at: str, cp: Checkpoint = nothing) -> None:
    db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=rw", uri=True, isolation_level=None,
                         timeout=BUSY_TIMEOUT)
    try:
        backup_to(db, path.with_name(f"{path.name}.v1.bak"))
        db.execute("BEGIN IMMEDIATE")
        try:
            _write(db, verdicts, at, cp)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def _write(db: sqlite3.Connection, verdicts: Sequence[Verdict], at: str, cp: Checkpoint) -> None:
    if not check_v1(db):
        raise JournalCorrupt("the journal changed during its upgrade")
    for stmt in V2_TABLES:
        db.execute(stmt)
        cp(f"upgrade.created.{stmt.split()[2]}")
    db.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", V2_META)
    for v in verdicts:
        db.execute("INSERT INTO adoptions (ws, bead, session_key, verdict, detail, facts) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (v.ws, v.bead, v.session_key, v.verdict, v.detail, v.facts))
        if v.entry is not None:
            db.execute(f"INSERT INTO launches ({LAUNCH_SQL}) VALUES ({', '.join('?' * LAUNCH_N)})",  # noqa: S608
                       launch_row(v.entry))
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION,))
    db.execute("INSERT INTO meta (key, value) VALUES ('upgraded_at', ?)", (at,))
    adopted = sum(v.verdict == "adopted" for v in verdicts)
    db.execute("INSERT INTO events (at, ws, bead, kind, detail) VALUES (?, '-', NULL, 'journal_upgraded', ?)",
               (at, f"version 1 to 2: {adopted} adopted, {len(verdicts) - adopted} held"))
    cp("upgrade.inserted")


def run(beads: BeadsAdapter, accounts: Accounts, path: Path, cp: Checkpoint = nothing) -> None:
    """Upgrade the version 1 journal at `path` in place. UpgradeAborted if the beads can't be read; the
    file is then untouched. Reopen with `Journal(path)` after."""
    legacy, adapters = gather(path, beads, accounts)
    at = now()
    commit(path, adopt(legacy, adapters, at), at, cp)
