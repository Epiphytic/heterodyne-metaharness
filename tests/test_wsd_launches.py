"""AU-3: launch entries, launch receipts and the accounts view (design §2, §4.1; tests 1 and 2)."""

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import msgspec
import pytest
from fakes.checkpoints import CrashAt, SimulatedCrash
from fakes.fake_btq import World, factory
from wsd_env import WS, Rig, make_rig

from heterodyne.config.accounts import credential_key
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import AccountChanged, Chosen, DefaultOnly
from heterodyne.wsd.beads import RECORD_KEY, BeadsAdapter, BeadsUnavailable, LaunchConflict
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.journal import EntryConflict, Journal, OpKind
from heterodyne.wsd.launches import (
    LAUNCHES_KEY,
    LaunchEntry,
    LaunchesUnreadable,
    Receipt,
    decode_launches,
    encode_entry,
)
from heterodyne.wsd.park import UNRECEIPTED
from heterodyne.wsd.recovery import recover
from heterodyne.wsd.runtime import LaunchSpec, RuntimeUnavailable, Started
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import Reason

KEY = ids.role_session("btq-1", "coder", "p-one")


def entry(generation: int = 1, key: str = KEY, **kw: object) -> LaunchEntry:
    fields: dict[str, object] = {
        "session_key": key, "generation": generation, "ws": WS, "bead": "btq-1", "role": "coder",
        "profile": "p-one", "account": "default", "credential_key": "ck1-" + "a" * 32, "model_passed": "",
        "adopted": False, "journaled_at": "2026-10-08T00:00:00+00:00", **kw}
    return LaunchEntry(**fields)  # type: ignore[arg-type]


def login_home(tmp_path: Path, name: str = "home") -> Path:
    home = tmp_path / name
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    (home / ".codex").mkdir()
    (home / ".codex" / "auth.json").write_text("{}")
    return home


PROFILES = {"p-one": "claude-code", "p-two": "codex"}


# --- the bead copy ---

def test_bead_copy_omits_empty_set_once_fields_and_sorts_keys() -> None:
    encoded = json.loads(encode_entry(entry()))
    assert "outcome" not in encoded and "dispatched_at" not in encoded
    assert list(encoded) == sorted(encoded)


@pytest.mark.parametrize("value", [1, "{", '{"a": 1}', "[1]", '[{"session_key": "k"}]'])
def test_an_unreadable_launch_array_is_never_guessed_at(value: object) -> None:
    with pytest.raises(LaunchesUnreadable):
        decode_launches(value)


def test_two_entries_for_one_generation_are_unreadable() -> None:
    one = msgspec.json.encode(entry()).decode()
    with pytest.raises(LaunchesUnreadable):
        decode_launches(f"[{one},{one}]")
    assert decode_launches(None).entries == ()


# --- the journal ---

@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "wsd.db")


def test_an_entry_is_never_changed_except_set_once_fields(journal: Journal) -> None:
    e = entry()
    journal.launch_insert(e)
    journal.launch_insert(e)                      # the same entry again: replay
    with pytest.raises(EntryConflict):
        journal.launch_insert(entry(account="other"))
    assert journal.launch_set(KEY, 1, "dispatched_at", "t1").dispatched_at == "t1"
    assert journal.launch_set(KEY, 1, "dispatched_at", "t1").dispatched_at == "t1"
    with pytest.raises(EntryConflict):
        journal.launch_set(KEY, 1, "dispatched_at", "t2")
    with pytest.raises(ValueError, match="set-once"):
        journal.launch_set(KEY, 1, "account", "other")
    assert journal.launch(KEY, 1) == entry(dispatched_at="t1")
    assert journal.rebuilt_at(KEY, 1) is None
    journal.launch_insert(entry(2), rebuilt=True)
    assert journal.rebuilt_at(KEY, 2) is not None
    assert [e.generation for e in journal.launches(KEY)] == [1, 2]
    assert [e.generation for e in journal.launches_of(WS, "btq-1")] == [1, 2]


def test_a_receipt_is_written_once_and_needs_its_entry(journal: Journal) -> None:
    with pytest.raises(EntryConflict):           # the foreign key: no receipt without its entry
        journal.receipt_put(Receipt(KEY, 1, "started"))
    journal.launch_insert(entry())
    started = Receipt(KEY, 1, "started", tmux_session="s", tmux_pane="%1", pane_pid=7, native_id="n")
    journal.receipt_put(started)
    assert journal.receipt(KEY, 1) == started
    with pytest.raises(EntryConflict):
        journal.receipt_put(Receipt(KEY, 1, "refused", refusal="failed"))
    assert journal.receipts() == 1


def test_op_forget_drops_only_the_named_keys(journal: Journal) -> None:
    op = journal.op_open(OpKind.PICKUP, WS, "btq-1", {"generation": "1", "other": "x"})
    assert journal.op_forget(op.op_id, "generation").data == {"other": "x"}


# --- the bead copy, written (§4.1) ---

@pytest.fixture
def world(tmp_path: Path) -> World:
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    return world


def test_ensure_launch_appends_sets_once_and_keeps_others_byte_for_byte(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    other = '{"session_key":"other","generation":1,"weird":  "spacing kept"}'
    meta = world.beads["btq-1"].metadata
    adapter.ensure_launch(WS, "btq-1", entry())
    meta[LAUNCHES_KEY] = meta[LAUNCHES_KEY][:-1] + "," + other + "]"
    with pytest.raises(LaunchesUnreadable):        # a foreign shape is never guessed at, nor rewritten
        adapter.ensure_launch(WS, "btq-1", entry(2))
    meta[LAUNCHES_KEY] = "[" + encode_entry(entry()).decode() + "]"
    first = meta[LAUNCHES_KEY]
    for _ in range(2):
        adapter.ensure_launch(WS, "btq-1", entry())
        assert meta[LAUNCHES_KEY] == first
    adapter.ensure_launch(WS, "btq-1", entry(2))
    adapter.ensure_launch(WS, "btq-1", entry(2, dispatched_at="t"))
    assert meta[LAUNCHES_KEY].startswith(first[:-1] + ",")
    assert adapter.show(WS, "btq-1").launches().entries == (entry(), entry(2, dispatched_at="t"))


@pytest.mark.parametrize("wanted", [entry(account="other"), entry(), entry(dispatched_at="t2")])
def test_a_differing_bead_entry_is_a_conflict_and_kept(world: World, wanted: LaunchEntry) -> None:
    """A field set on the bead but empty or different in the journal's entry is never overwritten."""
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    adapter.ensure_launch(WS, "btq-1", entry(dispatched_at="t1"))
    before = world.beads["btq-1"].metadata[LAUNCHES_KEY]
    with pytest.raises(LaunchConflict):
        adapter.ensure_launch(WS, "btq-1", wanted)
    assert world.beads["btq-1"].metadata[LAUNCHES_KEY] == before


def test_ensure_launch_needs_read_back(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    monkeypatch.setattr(adapter.bead_queue(WS, "btq-1"), "bd", lambda *args: [])
    with pytest.raises(BeadsUnavailable, match="read back"):
        adapter.ensure_launch(WS, "btq-1", entry())


# --- the accounts view (§2.1; test 1) ---

def test_keys_are_au2s_resolved_freshly_from_the_supplied_home(tmp_path: Path) -> None:
    home = login_home(tmp_path)
    accounts = DefaultOnly(PROFILES, {"HOME": str(home)})
    assert accounts.current_key("claude-code", "default") == credential_key(
        "claude-code", ((home / ".claude" / ".credentials.json").resolve(),))
    assert accounts.choose("p-one", None) == Chosen("default", accounts.current_key("claude-code", "default"))
    other = login_home(tmp_path, "other")
    (home / ".codex" / "auth.json").unlink()
    (home / ".codex" / "auth.json").symlink_to(other / ".codex" / "auth.json")
    before = credential_key("codex", ((home / ".codex" / "auth.json"),))
    assert accounts.current_key("codex", "default") != before   # the repointed file is seen, no reload
    assert accounts.current_key("codex", "default").startswith("ck1-")


def test_a_changed_key_is_account_changed_without_switch_capability(tmp_path: Path) -> None:
    home = login_home(tmp_path)
    accounts = DefaultOnly(PROFILES, {"HOME": str(home)})
    assert isinstance(accounts.choose("p-two", "ck1-" + "0" * 32), AccountChanged)
    key = accounts.current_key("codex", "default")
    assert accounts.choose("p-two", key) == Chosen("default", key)


def test_login_resolves_is_strict(tmp_path: Path) -> None:
    home = login_home(tmp_path)
    accounts = DefaultOnly(PROFILES, {"HOME": str(home)})
    assert accounts.login_resolves("codex", "default")
    (home / ".codex" / "auth.json").unlink()
    (home / ".codex" / "auth.json").symlink_to(home / "nowhere")
    assert not accounts.login_resolves("codex", "default")
    (home / ".claude" / ".credentials.json").unlink()
    (home / ".claude" / ".credentials.json").mkdir()
    assert not accounts.login_resolves("claude-code", "default")
    assert accounts.eligible("p-one", "default") and not accounts.eligible("p-one", "named")
    assert accounts.configured("codex") == ()


def test_the_session_key_never_depends_on_the_account(tmp_path: Path, world: World) -> None:
    """Test 1: two accounts views with different keys give one session key and one `wsd_session`."""
    a = DefaultOnly(PROFILES, {"HOME": str(login_home(tmp_path, "a"))})
    b = DefaultOnly(PROFILES, {"HOME": str(login_home(tmp_path, "b"))})
    assert a.current_key("claude-code", "default") != b.current_key("claude-code", "default")
    assert ids.role_session("btq-1", "coder", "p-one") == KEY


# --- the guard, end to end (tests 1, 2, 3, 6, 7, 8) ---

@pytest.fixture(autouse=True)
def _no_queue_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith(("BTQ_", "BEADS_", "BD_")):
            monkeypatch.delenv(name)


def started(tmp_path: Path, cp: Checkpoint | None = None, profile: str = "p-one") -> Rig:
    """btq-1 picked up on `profile` (generation 1), with btq-2 to park it on."""
    rig = make_rig(tmp_path)
    rig.ws = replace(rig.ws, coder_profile=profile, models={profile: "model-x"})
    rig.restart(cp)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    return rig


def resumable(tmp_path: Path, profile: str = "p-one") -> Rig:
    """btq-1 launched once, parked on btq-2, and btq-2 since closed."""
    rig = started(tmp_path, profile=profile)
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    return rig


def crash(rig: Rig, point: str) -> None:
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()


def raw_entries(rig: Rig) -> tuple[bytes, ...]:
    return rig.beads.show(WS, "btq-1").launches().raw


def row_reason(rig: Rig) -> Reason | None:
    row = rig.journal.state(WS, "btq-1")
    return None if row is None else row.reason


def test_wsd_session_never_records_the_account(tmp_path: Path) -> None:
    """Test 1: the session record is the same whichever login launched it, and a later launch on another
    login (here refused as account_changed) leaves it byte-identical."""
    rig = resumable(tmp_path)
    before = rig.world.beads["btq-1"].metadata[RECORD_KEY]
    assert "ck1-" not in before and "default" not in before
    rig.ws = replace(rig.ws, accounts=DefaultOnly(PROFILES, {"HOME": str(login_home(tmp_path, "b"))}))
    rig.restart()
    rig.pickup()
    assert row_reason(rig) is Reason.ACCOUNT_CHANGED
    assert rig.world.beads["btq-1"].metadata[RECORD_KEY] == before


@pytest.mark.parametrize("step", ["entry", "entry!", "dispatched", "dispatched!", "outcome", "outcome!"])
def test_a_crash_appending_an_entry_replays_to_one(tmp_path: Path, step: str) -> None:
    """Test 2: one entry per generation, and generation 1 byte-identical, whatever the crash point."""
    rig = resumable(tmp_path)
    [first] = raw_entries(rig)
    crash(rig, f"resume.{step}")
    rig.pickup()
    entries = rig.beads.show(WS, "btq-1").launches()
    assert [e.generation for e in entries.entries] == [1, 2] and entries.raw[0] == first
    assert rig.runtime.calls == 2 or (step in UNRECEIPTED and row_reason(rig) is Reason.UNEXPECTED_STATE)


def test_a_hand_edited_entry_is_unexpected_state(tmp_path: Path) -> None:
    rig = resumable(tmp_path)
    meta = rig.world.beads["btq-1"].metadata
    meta[LAUNCHES_KEY] = meta[LAUNCHES_KEY].replace('"account":"default"', '"account":"edited"')
    rig.pickup()
    assert row_reason(rig) is Reason.UNEXPECTED_STATE and rig.runtime.calls == 1


def codex_dir_login(tmp_path: Path) -> None:
    """`~/.codex` as a directory symlink into a scratch login dir."""
    home = tmp_path / "home"
    shutil.rmtree(home / ".codex")
    (home / ".codex").symlink_to(login_home(tmp_path, "login-a") / ".codex")


def repoint_dir(tmp_path: Path) -> None:
    link = tmp_path / "home" / ".codex"
    link.unlink()
    link.symlink_to(login_home(tmp_path, "login-b") / ".codex")


def repoint_file(tmp_path: Path) -> None:
    link = tmp_path / "home" / ".codex" / "auth.json"
    link.unlink()
    link.symlink_to(login_home(tmp_path, "login-c") / ".codex" / "auth.json")


@pytest.mark.parametrize("repoint", [repoint_dir, repoint_file])
def test_a_first_launch_never_dispatches_a_repointed_pin(tmp_path: Path,
                                                        repoint: Callable[[Path], None]) -> None:
    """Test 3: the login changed between the entry and the dispatch. The pinned generation is abandoned
    and a first launch pins generation 2 on the new key."""
    rig = started(tmp_path, profile="p-two")
    if repoint is repoint_dir:
        codex_dir_login(tmp_path)
    crash(rig, "pickup.entry!")
    pinned = rig.journal.launches_of(WS, "btq-1")[0].credential_key
    repoint(tmp_path)
    rig.pickup()
    one, two = rig.journal.launches_of(WS, "btq-1")
    assert (one.outcome, one.dispatched_at) == ("abandoned", None)
    assert two.outcome == "launched" and two.credential_key not in (pinned, "")
    assert [s.generation for s in rig.runtime.launches] == [2]
    assert rig.beads.show(WS, "btq-1").launches().entries == (one, two)


def test_a_resume_never_dispatches_a_repointed_pin(tmp_path: Path) -> None:
    rig = resumable(tmp_path, "p-two")
    crash(rig, "resume.entry!")
    repoint_file(tmp_path)
    rig.pickup()
    assert row_reason(rig) is Reason.ACCOUNT_CHANGED and rig.runtime.calls == 1
    assert [e.outcome for e in rig.journal.launches_of(WS, "btq-1")] == ["launched", "abandoned"]


class Ineligible(DefaultOnly):
    def eligible(self, profile: str, account: str) -> bool:
        return False


def test_an_ineligible_pin_is_abandoned_and_waits(tmp_path: Path) -> None:
    rig = started(tmp_path)
    crash(rig, "pickup.entry!")
    rig.ws = replace(rig.ws, accounts=Ineligible(PROFILES, {"HOME": str(tmp_path / "home")}))
    rig.restart()
    rig.pickup()
    [op] = rig.journal.ops_open()
    assert rig.runtime.calls == 0 and "generation" not in op.data      # the next pickup pins again
    assert {e.outcome for e in rig.journal.launches_of(WS, "btq-1")} == {"abandoned"}


@pytest.mark.parametrize("point", ["pickup.abandoned", "pickup.abandoned!"])
def test_a_crash_abandoning_a_pin_replays_to_one_launch(tmp_path: Path, point: str) -> None:
    rig = started(tmp_path, profile="p-two")
    crash(rig, "pickup.entry!")
    repoint_file(tmp_path)
    crash(rig, point)
    rig.pickup()
    assert [e.outcome for e in rig.journal.launches_of(WS, "btq-1")] == ["abandoned", "launched"]
    assert rig.runtime.calls == 1 and len(raw_entries(rig)) == 2


def test_an_unchanged_pin_launches_exactly_what_it_pinned(tmp_path: Path) -> None:
    rig = started(tmp_path)
    crash(rig, "pickup.entry!")
    rig.pickup()
    [entry] = rig.journal.launches_of(WS, "btq-1")
    [spec] = rig.runtime.launches
    assert (spec.generation, spec.account, spec.model) == (1, "default", "model-x")
    assert entry.model_passed == "model-x" and entry.outcome == "launched"


@pytest.mark.parametrize("live", [True, False])
@pytest.mark.parametrize("step", ["receipt", "outcome", "outcome!", "done"])
def test_a_crash_after_the_launch_finishes_from_the_receipt(tmp_path: Path, step: str, live: bool) -> None:
    """Test 7: one dispatch, finished from the receipt, whether the session is still live or ended."""
    rig = resumable(tmp_path)
    crash(rig, f"resume.{step}")
    if not live:
        rig.runtime.end(rig.key("btq-1"))
    rig.replay_open()
    _, two = rig.journal.launches_of(WS, "btq-1")
    receipt = rig.journal.receipt(*two.ident)
    assert receipt is not None and two.outcome == "launched" and two.native_id == receipt.native_id
    assert rig.beads.show(WS, "btq-1").launches().entries[1] == two
    assert rig.runtime.calls == 2 and rig.journal.ops_open() == []


def test_after_a_receipt_a_repointed_login_is_account_changed(tmp_path: Path) -> None:
    rig = resumable(tmp_path, "p-two")
    crash(rig, "resume.receipt")
    rig.replay_open()
    rig.world.add("btq-3")
    rig.world.beads["btq-3"].labels.remove("agent:wsd")
    rig.parker.park("btq-1", ("btq-3",))
    repoint_file(tmp_path)
    rig.world.close("btq-3")
    rig.pickup()
    assert row_reason(rig) is Reason.ACCOUNT_CHANGED and rig.runtime.calls == 2


def test_a_failed_receipt_replays_to_one_spend(tmp_path: Path) -> None:
    rig = resumable(tmp_path)
    rig.runtime.launch_failures = 1
    crash(rig, "resume.receipt")
    rig.replay_open()
    [op] = rig.journal.ops_open()
    assert op.attempts == 1 and "generation" not in op.data
    assert rig.journal.receipt(rig.key("btq-1"), 2) is not None
    rig.pickup()
    assert [s.generation for s in rig.runtime.launches] == [1, 3] and rig.state("btq-1") == "running"


def test_an_unavailable_receipt_replays_to_wait(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = resumable(tmp_path)

    def down(spec: LaunchSpec) -> Started:
        raise RuntimeUnavailable("the backend went away mid-launch")

    with monkeypatch.context() as m:
        m.setattr(rig.runtime, "launch", down)
        crash(rig, "resume.receipt")
    [op] = rig.journal.ops_open()
    with rig.parker.entry():
        assert rig.parker.replay_resume(op).value == "wait"
    [op] = rig.journal.ops_open()
    assert op.attempts == 0 and Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)


def lose_journal(rig: Rig) -> None:
    rig.journal.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{rig.root / 'state' / 'wsd.db'}{suffix}").unlink(missing_ok=True)
    rig.restart()


def p_q_p(tmp_path: Path) -> tuple[Rig, str]:
    """Generations P1, Q1, P2 on btq-1: P is the coder's session key, Q another role's (written as the
    guard would, journal then bead). P2 ended; returns Q's key."""
    rig = resumable(tmp_path)
    q = ids.role_session("btq-1", "reviewer", "p-two")
    q1 = entry(1, q, role="reviewer", profile="p-two", dispatched_at="t", outcome="launched")
    rig.journal.launch_insert(q1)
    rig.beads.ensure_launch(WS, "btq-1", q1)
    rig.pickup()
    rig.runtime.end(rig.key("btq-1"))
    return rig, q


def test_entries_rebuild_from_the_bead_after_the_journal_is_lost(tmp_path: Path) -> None:
    """Test 6: P1, Q1, P2 rebuild from `wsd_launches` with `rebuilt_at` and no receipts; P3 is pinned."""
    rig, q = p_q_p(tmp_path)
    before = rig.beads.show(WS, "btq-1").launches().entries
    p = rig.key("btq-1")
    assert [(e.session_key, e.generation) for e in before] == [(p, 1), (q, 1), (p, 2)]
    lose_journal(rig)
    assert recover(rig.sched).ok and row_reason(rig) is Reason.JOURNAL_LOST
    rig.parker.release("btq-1")
    rig.pickup()
    assert rig.journal.receipts() == 1                               # P3's own, nothing rebuilt
    assert rig.journal.launches(q) == [before[1]] and rig.journal.rebuilt_at(q, 1) is not None
    assert [e.generation for e in rig.journal.launches(p)] == [1, 2, 3]
    assert rig.journal.rebuilt_at(p, 2) is not None and rig.journal.rebuilt_at(p, 3) is None
    assert rig.runtime.launches[-1].generation == 3 and rig.state("btq-1") == "running"


def test_a_rebuilt_dispatched_entry_with_no_outcome_holds(tmp_path: Path) -> None:
    rig, _ = p_q_p(tmp_path)
    meta = rig.world.beads["btq-1"].metadata
    p2 = rig.beads.show(WS, "btq-1").launches().raw[2].decode()
    meta[LAUNCHES_KEY] = meta[LAUNCHES_KEY].replace(p2, p2.replace(',"outcome":"launched"', ""))
    assert rig.beads.show(WS, "btq-1").launches().entries[2].outcome is None
    lose_journal(rig)
    assert recover(rig.sched).ok
    calls = rig.runtime.calls
    rig.parker.release("btq-1")
    rig.pickup()
    assert rig.runtime.calls == calls and row_reason(rig) is Reason.UNEXPECTED_STATE
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and "generation 2 was dispatched with no launch receipt" in row.detail
