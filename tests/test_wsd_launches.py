"""AU-3: launch entries, launch receipts and the accounts view (design §2, §4.1; tests 1 and 2)."""

import json
from pathlib import Path

import msgspec
import pytest
from fakes.fake_btq import World, factory
from wsd_env import WS

from heterodyne.config.accounts import credential_key
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import AccountChanged, Chosen, DefaultOnly
from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable, LaunchConflict
from heterodyne.wsd.journal import EntryConflict, Journal, OpKind
from heterodyne.wsd.launches import (
    LAUNCHES_KEY,
    LaunchEntry,
    LaunchesUnreadable,
    Receipt,
    decode_launches,
    encode_entry,
)

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
