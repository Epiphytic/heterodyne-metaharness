"""AU-3: the journal upgrade, adoption and its startup and release paths (design §3; tests 4, 5, 9-12)."""

import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from fakes.checkpoints import CrashAt, SimulatedCrash
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import ADAPTERS, PROFILES, WS, Rig, accounts_at, git_repo, login_home, make_rig

from heterodyne.wsd import cli, ids, upgrade
from heterodyne.wsd.accounts import ConfiguredAccounts
from heterodyne.wsd.beads import NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import V1_SCHEMA, V2_TABLES, Adoption, Journal, JournalCorrupt, check_v1
from heterodyne.wsd.launches import LAUNCHES_KEY, LaunchEntry, encode_entry
from heterodyne.wsd.park import NotReleasable
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.states import Reason
from heterodyne.wsd.workstream import WorkstreamSettings

V2 = [stmt.split()[2] for stmt in V2_TABLES]
V1_TABLES = ("meta", "inbox", "ops", "beads", "holds", "workstreams", "events")


@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


class Configured(ConfiguredAccounts):
    """Accounts with a named account configured for codex (its login is never read here)."""

    def configured(self, adapter: str) -> tuple[str, ...]:
        return ("work",) if adapter == "codex" else ()


def db_path(rig: Rig) -> Path:
    return rig.root / "state" / "wsd.db"


def to_v1(rig: Rig) -> None:
    """Make the rig's journal plan 3's: the version 2 tables dropped and version 1, and every bead without
    `wsd_launches`. What the runtime and the beads say of each session is kept."""
    rig.journal.close()
    db = sqlite3.connect(db_path(rig), isolation_level=None)
    try:
        for table in ["receipts", *(t for t in V2 if t != "receipts")]:
            db.execute(f"DROP TABLE {table}")
        db.execute("DELETE FROM meta WHERE key = 'usage_receipt_seq'")
        db.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    finally:
        db.close()
    for bead in rig.world.beads.values():
        bead.metadata.pop(LAUNCHES_KEY, None)


def upgraded(rig: Rig, accounts: ConfiguredAccounts | None = None, cp: Checkpoint = nothing) -> None:
    upgrade.run(rig.beads, accounts or rig.ws.accounts, db_path(rig), cp)   # type: ignore[arg-type]
    rig.restart()


def legacy_running(tmp_path: Path, profile: str = "p-one") -> Rig:
    """btq-1 running under plan 3 on `profile`, its journal version 1."""
    rig = make_rig(tmp_path)
    rig.ws = replace(rig.ws, coder_profile=profile)
    rig.restart()
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    if profile == "p-two":
        meta = rig.world.beads["btq-1"].metadata
        meta[RECORD_KEY] = json.dumps({**json.loads(meta[RECORD_KEY]), "thread_id": "th-legacy"})
    to_v1(rig)
    return rig


def adoption(rig: Rig) -> Adoption:
    found = rig.journal.adoption(WS, "btq-1")
    assert found is not None
    return found


def bead_entries(rig: Rig) -> tuple[LaunchEntry, ...]:
    return rig.beads.show(WS, "btq-1").launches().entries


def ops(rig: Rig, kind: str) -> list[str]:
    rows = rig.journal.db.execute("SELECT status FROM ops WHERE bead = 'btq-1' AND kind = ?", (kind,))
    return [str(r[0]) for r in rows]


def reason(rig: Rig) -> Reason | None:
    row = rig.journal.state(WS, "btq-1")
    return None if row is None else row.reason


# --- tests 4 and 5: the upgrade itself ---

def v1_journal(path: Path) -> None:
    """Plan 3's frozen schema with a row in every table; the only bead row is closed (no legacy beads)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.executescript(V1_SCHEMA)
        at = "2026-10-01T00:00:00+00:00"
        db.execute("INSERT INTO meta VALUES ('schema_version', '1'), ('created_at', ?)", (at,))
        db.execute("INSERT INTO inbox (surface, event_id, kind, ws, bead, payload, status, received_at, "
                   "updated_at) VALUES ('s', 'e1', 'k', ?, 'btq-9', '{}', 'committed', ?, ?)", (WS, at, at))
        db.execute("INSERT INTO ops VALUES ('op-1', 'pickup', ?, 'btq-9', 'done', '{}', 0, 'done', ?, ?)",
                   (WS, at, at))
        db.execute("INSERT INTO beads VALUES (?, 'btq-9', 'closed', NULL, '', ?)", (WS, at))
        db.execute("INSERT INTO holds VALUES (?, 'paused', '', ?)", (WS, at))
        db.execute("INSERT INTO workstreams VALUES (?, 'idle', ?)", (WS, at))
        db.execute("INSERT INTO events (at, ws, bead, kind, detail) VALUES (?, ?, 'btq-9', 'k', 'd')",
                   (at, WS))
    finally:
        db.close()


def rows(path: Path) -> dict[str, set[tuple[object, ...]]]:
    db = sqlite3.connect(path)
    try:
        return {t: set(db.execute(f"SELECT * FROM {t}").fetchall()) for t in V1_TABLES}  # noqa: S608
    finally:
        db.close()


@pytest.fixture
def v1(tmp_path: Path) -> Path:
    path = tmp_path / "state" / "wsd.db"
    v1_journal(path)
    return path


def no_beads(tmp_path: Path) -> tuple[BeadsAdapter, ConfiguredAccounts]:
    """A queue and accounts for a journal with no legacy beads: neither is ever asked anything."""
    return BeadsAdapter(factory(World(tmp_path / "btq-state"))), accounts_at(login_home(tmp_path / "home"))


def test_the_upgrade_keeps_every_v1_row(tmp_path: Path, v1: Path) -> None:
    before = rows(v1)
    upgrade.run(*no_beads(tmp_path), v1)
    after = rows(v1)
    for table in V1_TABLES:
        kept = before[table] - {("schema_version", "1")}
        assert kept <= after[table], table
    assert ("schema_version", "2") in after["meta"]
    assert [r[4] for r in after["events"] - before["events"]] == ["journal_upgraded"]
    Journal(v1).close()                                      # the v2 check passes
    assert (v1.parent / "wsd.db.v1.bak").exists()


@pytest.mark.parametrize("point", [*(f"upgrade.created.{t}" for t in V2), "upgrade.inserted"])
def test_a_crash_mid_upgrade_leaves_version_1(tmp_path: Path, v1: Path, point: str) -> None:
    before = rows(v1)
    with pytest.raises(SimulatedCrash):
        upgrade.run(*no_beads(tmp_path), v1, CrashAt(point))
    db = sqlite3.connect(v1)
    try:
        assert check_v1(db)
    finally:
        db.close()
    assert rows(v1) == before
    upgrade.run(*no_beads(tmp_path), v1)                     # and it is retried whole
    Journal(v1).close()


@pytest.mark.parametrize("shape", ["version 2 on v1", "extra table"])
def test_a_shape_that_is_neither_version_is_refused_unwritten(tmp_path: Path, v1: Path, shape: str) -> None:
    db = sqlite3.connect(v1, isolation_level=None)
    if shape == "extra table":
        db.execute("CREATE TABLE extra (x)")
    else:
        db.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    db.close()
    raw = v1.read_bytes()
    with pytest.raises(JournalCorrupt):
        Journal(v1)
    with pytest.raises(JournalCorrupt):
        upgrade.run(*no_beads(tmp_path), v1)
    assert v1.read_bytes() == raw


def test_beads_down_aborts_with_the_file_untouched(tmp_path: Path) -> None:
    rig = legacy_running(tmp_path)
    raw = db_path(rig).read_bytes()
    rig.world.down = True
    with pytest.raises(upgrade.UpgradeAborted):
        upgrade.run(rig.beads, rig.ws.accounts, db_path(rig))   # type: ignore[arg-type]
    assert db_path(rig).read_bytes() == raw
    rig.world.down = False
    upgraded(rig)
    assert adoption(rig).verdict == "adopted"


def test_the_daemon_exits_1_when_beads_are_down_at_the_upgrade(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repos" / "proj")
    accounts = accounts_at(login_home(tmp_path / "home"))
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, accounts=accounts)
    s = WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,), accounts)
    s.state_dir.mkdir(parents=True, mode=0o700)
    v1_journal(s.journal)
    db = sqlite3.connect(s.journal, isolation_level=None)
    db.execute("INSERT INTO beads VALUES (?, 'btq-1', 'running', NULL, '', 'x')", (WS,))
    db.close()
    raw = s.journal.read_bytes()
    world = World(tmp_path / "btq-state")
    world.down = True
    assert cli.run(s, factory(world), FakeRuntime()) == 1
    assert s.journal.read_bytes() == raw


# --- test 9: adoption ---

@pytest.mark.parametrize("profile", ["p-one", "p-two"])
def test_a_legacy_session_is_adopted_before_the_first_pickup(tmp_path: Path, profile: str) -> None:
    rig = legacy_running(tmp_path, profile)
    key = rig.key("btq-1")
    upgraded(rig)
    assert adoption(rig).verdict == "adopted" and not adoption(rig).settled
    assert recover(rig.sched).ok and adoption(rig).settled
    [entry] = bead_entries(rig)
    native = key if profile == "p-one" else "th-legacy"
    assert (entry.generation, entry.adopted, entry.outcome, entry.native_id) == (1, True, "launched", native)
    assert entry.credential_key == rig.ws.accounts.current_key(ADAPTERS[profile], "default")  # type: ignore[union-attr]
    rig.runtime.end(key)
    rig.pickup()
    spec = rig.runtime.launches[-1]
    assert (spec.resume, spec.generation, spec.native_id) == (True, 2, native)
    assert [e.generation for e in bead_entries(rig)] == [1, 2] and rig.state("btq-1") == "running"


def test_an_account_added_later_never_unadopts(tmp_path: Path) -> None:
    rig = legacy_running(tmp_path, "p-two")
    upgraded(rig)
    assert recover(rig.sched).ok
    before = adoption(rig)
    rig.ws = replace(rig.ws, accounts=Configured(ADAPTERS, {"HOME": str(tmp_path / "home")}))
    rig.restart()
    rig.runtime.end(rig.key("btq-1"))
    rig.pickup()
    assert adoption(rig) == before
    assert rig.runtime.launches[-1].generation == 2 and rig.state("btq-1") == "running"


def test_a_repointed_login_after_adoption_is_account_changed(tmp_path: Path) -> None:
    rig = legacy_running(tmp_path, "p-two")
    upgraded(rig)
    assert recover(rig.sched).ok
    other = login_home(tmp_path / "other")
    auth = tmp_path / "home" / ".codex" / "auth.json"
    auth.unlink()
    auth.symlink_to(other / ".codex" / "auth.json")
    rig.runtime.end(rig.key("btq-1"))
    calls = rig.runtime.calls
    rig.pickup()
    assert rig.runtime.calls == calls and reason(rig) is Reason.ACCOUNT_CHANGED
    assert adoption(rig).verdict == "adopted"


@pytest.mark.parametrize("point", ["adopt.appended!", "adopt.settled"])
def test_a_crash_settling_an_adoption_gives_one_entry(tmp_path: Path, point: str) -> None:
    rig = legacy_running(tmp_path)
    upgraded(rig)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    rig.restart()
    assert recover(rig.sched).ok and adoption(rig).settled
    assert len(bead_entries(rig)) == 1


@pytest.mark.parametrize("value", ["not json", "[]"])
def test_an_adopted_entry_that_cant_go_on_the_bead_fails_recovery(tmp_path: Path, value: str) -> None:
    """r1 finding 3 (§3.2): an unreadable or conflicting `wsd_launches` fails recovery, so pickup never
    runs, and the adoption stays unsettled for the next recovery."""
    rig = legacy_running(tmp_path)
    upgraded(rig)
    meta = rig.world.beads["btq-1"].metadata
    if value == "[]":     # a conflicting generation 1 under the adopted session key
        conflict = msgspec.structs.replace(rig.journal.launches_of(WS, "btq-1")[0], account="edited")
        value = "[" + encode_entry(conflict).decode() + "]"
    meta[LAUNCHES_KEY] = value
    calls = rig.runtime.calls
    result = recover(rig.sched)
    assert not result.ok and not adoption(rig).settled
    assert Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)    # the daemon starts no pickup on it
    rig.runtime.end(rig.key("btq-1"))
    rig.pickup()                                     # and if one ran, the guard waits for 4a
    assert rig.runtime.calls == calls and not adoption(rig).settled
    meta.pop(LAUNCHES_KEY)
    assert recover(rig.sched).ok and adoption(rig).settled and len(bead_entries(rig)) == 1


# --- test 10: held adoptions ---

type Change = Callable[[Rig], None]


def unresolvable(rig: Rig) -> None:
    (rig.root / "home" / ".claude" / ".credentials.json").unlink()


def mismatched(rig: Rig) -> None:
    meta = rig.world.beads["btq-1"].metadata
    other = ids.role_session("btq-1", "coder", "p-two")
    meta[RECORD_KEY] = json.dumps({**json.loads(meta[RECORD_KEY]), "session_key": other})


def claimed_elsewhere(rig: Rig) -> None:
    rig.world.beads["btq-1"].assignee = "codex:otherhost:x"


def unrecorded(rig: Rig) -> None:
    rig.world.beads["btq-1"].metadata.pop(RECORD_KEY)


@pytest.mark.parametrize("change", [unresolvable, mismatched, claimed_elsewhere, unrecorded])
def test_a_legacy_session_in_doubt_is_held_and_never_launched(tmp_path: Path, change: Change) -> None:
    rig = legacy_running(tmp_path)
    change(rig)
    calls = rig.runtime.calls
    upgraded(rig)
    assert adoption(rig).verdict == "held" and rig.journal.launches_of(WS, "btq-1") == []
    recover(rig.sched)
    rig.pickup()
    assert rig.runtime.calls == calls and rig.state("btq-1") == "stuck"


def test_accounts_configured_at_the_upgrade_hold_codex_only(tmp_path: Path) -> None:
    for profile, verdict in (("p-two", "held"), ("p-one", "adopted")):
        rig = legacy_running(tmp_path / profile, profile)
        upgraded(rig, Configured(ADAPTERS, {"HOME": str(tmp_path / profile / "home")}))
        assert adoption(rig).verdict == verdict, profile
        assert recover(rig.sched).ok
        assert (reason(rig) is Reason.UNEXPECTED_STATE) == (verdict == "held")


def interrupted(tmp_path: Path, point: str) -> Rig:
    """btq-1's pickup (or, for a resume point, its resume after a park) cut short at `point` under plan 3."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    if point.startswith("resume."):
        rig.world.add("btq-2")
        rig.world.beads["btq-2"].labels.remove("agent:wsd")
        assert rig.pickup() is Outcome.STARTED
        rig.parker.park("btq-1", ("btq-2",))
        rig.world.close("btq-2")
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    to_v1(rig)
    return rig


@pytest.mark.parametrize("point", ["pickup.worktree", "resume.unlabelled"])
def test_an_open_launch_op_is_held_and_handed_to_one_escalation(tmp_path: Path, point: str) -> None:
    """Test 11: step 4a ends the open op STUCK and opens one escalation, `settled` in the same transaction."""
    rig = interrupted(tmp_path, point)
    calls = rig.runtime.calls
    upgraded(rig)
    assert adoption(rig).verdict == "held"
    assert recover(rig.sched).ok and adoption(rig).settled
    assert ops(rig, point.split(".")[0])[-1] == "stuck" and ops(rig, "escalate") == ["done"]
    assert reason(rig) is Reason.UNEXPECTED_STATE and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    rig.pickup()
    assert rig.runtime.calls == calls and rig.journal.ops_open() == []


def test_a_crash_at_the_handoff_opens_no_second_escalation(tmp_path: Path) -> None:
    rig = interrupted(tmp_path, "resume.unlabelled")
    upgraded(rig)
    rig.restart(CrashAt("adopt.settled"))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    rig.restart()
    assert recover(rig.sched).ok
    assert ops(rig, "escalate") == ["done"] and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_an_open_escalation_is_kept_not_doubled(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.restart(CrashAt("escalate.intent"))
    with pytest.raises(SimulatedCrash):
        rig.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
    to_v1(rig)
    upgraded(rig)
    assert adoption(rig).verdict == "held"
    assert recover(rig.sched).ok and adoption(rig).settled
    assert ops(rig, "escalate") == ["done"] and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_a_pickup_before_its_launch_is_exempt(tmp_path: Path) -> None:
    rig = interrupted(tmp_path, "pickup.placed")
    upgraded(rig)
    assert rig.journal.adoption(WS, "btq-1") is None
    assert recover(rig.sched).ok
    rig.pickup()
    [entry] = bead_entries(rig)
    assert (entry.generation, entry.adopted) == (1, False) and rig.state("btq-1") == "running"


# --- test 12: release of an adoption hold ---

def test_release_is_refused_when_accounts_were_configured_at_the_upgrade(tmp_path: Path) -> None:
    rig = legacy_running(tmp_path, "p-two")
    upgraded(rig, Configured(ADAPTERS, {"HOME": str(tmp_path / "home")}))
    assert recover(rig.sched).ok
    before, calls = adoption(rig), rig.runtime.calls
    for accounts in (Configured(ADAPTERS, {"HOME": str(tmp_path / "home")}), rig.ws.accounts):
        rig.ws = replace(rig.ws, accounts=accounts)
        rig.restart()
        with pytest.raises(NotReleasable, match="accounts were configured for codex"):
            rig.parker.release("btq-1")
        assert adoption(rig) == before and rig.state("btq-1") == "stuck"
    assert rig.runtime.calls == calls and rig.journal.ops_open() == []


def released(tmp_path: Path, change: Change = unresolvable, point: str | None = None) -> Rig:
    """btq-1 legacy, held by `change`, its session ended, the login restored, then released."""
    rig = legacy_running(tmp_path)
    change(rig)
    upgraded(rig)
    assert recover(rig.sched).ok and adoption(rig).verdict == "held"
    rig.runtime.end(ids.role_session("btq-1", "coder", "p-one"))
    login_home(rig.root / "home")
    if point is None:
        rig.parker.release("btq-1")
    else:
        rig.restart(CrashAt(point))
        with pytest.raises(SimulatedCrash):
            rig.parker.release("btq-1")
        rig.restart()
        rig.replay_open()
    return rig


@pytest.mark.parametrize("change", [unresolvable, unrecorded])
def test_release_adopts_once_reverified(tmp_path: Path, change: Change) -> None:
    rig = released(tmp_path, change)
    found = adoption(rig)
    assert found.resolution == "adopted" and found.resolved_by is not None
    assert [e.adopted for e in bead_entries(rig)] == [True]
    rig.pickup()
    assert rig.runtime.launches[-1].generation == 2 and rig.state("btq-1") == "running"


@pytest.mark.parametrize("point", ["release.adopted", "release.adopted!"])
def test_a_crash_resolving_an_adoption_replays_to_one_entry(tmp_path: Path, point: str) -> None:
    rig = released(tmp_path, point=point)
    assert adoption(rig).resolution == "adopted" and len(bead_entries(rig)) == 1
    rig.pickup()
    assert [s.generation for s in rig.runtime.launches[1:]] == [2]


def test_release_reescalates_while_the_login_still_does_not_resolve(tmp_path: Path) -> None:
    rig = legacy_running(tmp_path)
    unresolvable(rig)
    upgraded(rig)
    assert recover(rig.sched).ok
    before = adoption(rig)
    rig.parker.release("btq-1")
    after = adoption(rig)
    assert (after.verdict, after.detail, after.facts, after.resolution) == (
        before.verdict, before.detail, before.facts, None)
    assert reason(rig) is Reason.UNEXPECTED_STATE and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


@pytest.mark.parametrize("point", [None, "release.adopted", "release.adopted!"])
def test_a_parked_adoption_hold_is_released_back_to_parked(tmp_path: Path, point: str | None) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    to_v1(rig)
    unresolvable(rig)
    upgraded(rig)
    assert recover(rig.sched).ok and adoption(rig).verdict == "held"
    login_home(rig.root / "home")
    calls = rig.runtime.calls
    if point is None:
        rig.parker.release("btq-1")
    else:
        rig.restart(CrashAt(point))
        with pytest.raises(SimulatedCrash):
            rig.parker.release("btq-1")
        rig.restart()
        rig.replay_open()
    assert adoption(rig).resolution == "adopted" and len(bead_entries(rig)) == 1
    assert rig.state("btq-1") == "parked" and PARKED in rig.world.beads["btq-1"].labels
    assert rig.runtime.calls == calls
    rig.world.close("btq-2")
    rig.pickup()
    assert rig.runtime.launches[-1].generation == 2 and rig.state("btq-1") == "running"
