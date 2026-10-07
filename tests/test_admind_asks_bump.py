"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

import sqlite3
from pathlib import Path

import pytest
from test_admind_asks import outbox, row
from test_admind_reactions import OLD_TABLES, columns
from test_admind_settings import BASE_CONFIG, write

from heterodyne.admind import commands
from heterodyne.admind import store as store_mod
from heterodyne.admind.settings import resolve
from heterodyne.admind.store import Store
from heterodyne.config import ConfigError, load


# --- B1, B10, B11: parsing and help ----------------------------------------------------------------
def test_asks_bump_and_repeat_parse() -> None:
    assert commands.parse("!asks") == commands.Command("asks")
    assert commands.parse("!asks bump") == commands.Command("asks", arg="bump")
    assert commands.parse("!asks  repeat ") == commands.Command("asks", arg="repeat")


@pytest.mark.parametrize("text", ["!asks foo", "!asks bump extra", "!asks repeat now", "!asks Bump",
                                  "!asks bump repeat"])
def test_other_asks_arguments_are_refused_with_the_usage(text: str) -> None:
    with pytest.raises(commands.CommandError) as err:
        commands.parse(text)
    assert str(err.value) == "Usage: !asks [bump|repeat]."


def test_help_names_bump_and_repeat() -> None:
    assert "!asks [bump|repeat]" in commands.HELP


# --- B14: the setting --------------------------------------------------------------------------------
def with_hours(value: object) -> str:
    return BASE_CONFIG.replace('profile = "admin"', f'profile = "admin"\nask_bump_hours = {value}')


def test_ask_bump_hours_defaults_to_12(tmp_path: Path) -> None:
    env = write(tmp_path)
    assert resolve(load(None, env), env).ask_bump_hours == 12


@pytest.mark.parametrize("hours", [0, 1, 720])
def test_ask_bump_hours_in_range(tmp_path: Path, hours: int) -> None:
    env = write(tmp_path, with_hours(hours))
    assert resolve(load(None, env), env).ask_bump_hours == hours


@pytest.mark.parametrize("value", ["-1", "721", "1.5", "true", '"12"'])
def test_ask_bump_hours_out_of_range_is_refused(tmp_path: Path, value: str) -> None:
    env = write(tmp_path, with_hours(value))
    with pytest.raises(ConfigError, match=r"\[admind\] ask_bump_hours must be an integer from 0 to 720"):
        resolve(load(None, env), env)


# --- B14: the activity clock in the store ----------------------------------------------------------
T0 = "2026-10-07T00:00:00+00:00"
T1 = "2026-10-07T01:00:00+00:00"
T2 = "2026-10-07T02:00:00+00:00"


def activity(store: Store, ask_id: str = "k7m2") -> str | None:
    r = store.ask(ask_id)
    assert r is not None
    return r.last_activity_at


def test_a_posted_ask_starts_its_clock_at_its_creation(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(created_at=T1, updated_at=T1), None)
    assert activity(store) == T1
    store.close()


def test_the_clock_only_moves_forward(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(created_at=T1, updated_at=T1), None)
    store.touch_ask("k7m2", T0)
    assert activity(store) == T1
    store.touch_ask("k7m2", T2)
    assert activity(store) == T2
    store.touch_ask("k7m2", T1)
    assert activity(store) == T2
    store.close()


def test_the_migration_starts_existing_clocks_at_the_migration_time(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """An ask created days ago, with a note an hour ago: not due at deploy; its clock starts then."""
    db = sqlite3.connect(tmp_path / "admind.db")
    db.executescript(OLD_TABLES)
    db.close()
    monkeypatch.setattr(store_mod, "now", lambda: T2)
    store = Store(tmp_path / "admind.db")
    assert "last_activity_at" in columns(store, "asks") and activity(store, "p4xw") == T2
    store.close()
    monkeypatch.setattr(store_mod, "now", lambda: "2026-10-09T00:00:00+00:00")
    again = Store(tmp_path / "admind.db")          # once: a later start never re-initialises it
    assert activity(again, "p4xw") == T2
    again.close()


# --- B6, B12, B13: the recognizers, repeat delivery and the bump target -----------------------------
CMD = "a1" * 32        # a command's message ID
CMD2 = "a2" * 32


def mid(n: int) -> str:
    """The fake message ID wn-agent gave the n-th sent row."""
    return f"{n:064x}"


@pytest.mark.parametrize("key", [f"asknote:k7m2:bump:{CMD}:0", f"asknote:k7m2:bump:{CMD}:12",
                                 "asknote:k7m2:autobump:7:0", "asknote:k7m2:autobump:123:3"])
def test_bump_for_message_matches_both_reminder_forms(tmp_path: Path, key: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.bump_for_message(mid(1)) == "k7m2"
    assert store.ask_for_message(mid(1)) is None            # a bump is never a card (B6)
    store.close()


@pytest.mark.parametrize("key", [
    "asknote:k7m2:cancelled:0:0", "asknote:k7m2:superseded:p4xw:0", f"asknote:k7m2:reconcile:{CMD}:0",
    "asknote:k7m2:cancelled:autobump:0", "asknote:k7m2:autobump:7:0:0", "asknote:k7m2:autobump::0",
    "asknote:k7m2:autobump:x7:0", "asknote:k7m2:autobump:7:", f"asknote:k7m2:bump:{CMD[:-1]}:0",
    f"asknote:k7m2:bump:{CMD.upper()}:0", f"asknote:k7m2:bump:{CMD}0:0", f"asknote:k7m2:bump:{CMD}:x",
    f"asknote:k7m2:bump:{CMD}:0\n", "asknote:K7M2:autobump:7:0", "asknote:k7m2:bump:7:0",
    f"asknote:k7m2:autobump:{CMD}:0", "ask:k7m2:0", f"askd:k7m2:{CMD}:0", f"askr:k7m2:{CMD}:0",
    f"xasknote:k7m2:bump:{CMD}:0", f"ask:{CMD}:0"])
def test_bump_for_message_matches_nothing_else(tmp_path: Path, key: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.bump_for_message(mid(1)) is None
    store.close()


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_bump_for_message_needs_a_sent_row(tmp_path: Path, status: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, f"asknote:k7m2:bump:{CMD}:0", status)
    outbox(store, "asknote:k7m2:autobump:1:0", status)
    store.db.execute("UPDATE outbox SET message_id = ?", (mid(1),))
    assert store.bump_for_message(mid(1)) is None
    assert store.bump_for_message(None) is None
    store.close()


def test_bump_for_message_needs_the_ask(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    outbox(store, f"asknote:k7m2:bump:{CMD}:0", "sent", mid(1))
    assert store.bump_for_message(mid(1)) is None
    store.close()


@pytest.mark.parametrize(("key", "card"), [
    (f"askr:k7m2:{CMD}:0", True), (f"askr:k7m2:{CMD}:7", True), (f"askr:k7m2:{CMD[:-1]}:0", False),
    (f"askr:k7m2:{CMD.upper()}:0", False), ("askr:k7m2:0", False), (f"askr:k7m2:{CMD}:", False),
    (f"askr:k7m2:{CMD}:0:0", False), (f"askr:k7m:{CMD}:0", False), (f"askrx:k7m2:{CMD}:0", False)])
def test_a_repeat_chunk_is_a_card(tmp_path: Path, key: str, card: bool) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.ask_for_message(mid(1)) == ("k7m2" if card else None)
    assert store.bump_for_message(mid(1)) is None
    store.close()


def test_a_pending_or_failed_repeat_chunk_is_not_a_card(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, f"askr:k7m2:{CMD}:0", "pending")
    outbox(store, f"askr:k7m2:{CMD}:1", "failed")
    store.db.execute("UPDATE outbox SET message_id = ?", (mid(1),))
    assert store.ask_for_message(mid(1)) is None
    store.close()


def repeated(store: Store, request: str, statuses: list[str], at: str = T1) -> None:
    store.add_repeat("k7m2", request, len(statuses), at)
    for i, status in enumerate(statuses):
        outbox(store, f"askr:k7m2:{request}:{i}", status, mid(int(request[:2], 16) * 100 + i))


def test_an_original_of_2_and_a_repeat_of_3(tmp_path: Path) -> None:
    """R8 delivery from one complete repeat, counted by its own parts (B12)."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "failed")
    assert not store.card_delivered("k7m2")
    repeated(store, CMD, ["sent", "sent", "pending"])
    assert not store.card_delivered("k7m2")             # two of three: the original's count is not enough
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = ?",
                                     (f"askr:k7m2:{CMD}:2",)).fetchone()[0], mid(3))
    assert store.card_delivered("k7m2")
    store.close()


def test_two_partial_repeats_never_combine(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=1), None)
    outbox(store, "ask:k7m2:0", "failed")
    repeated(store, CMD, ["sent", "failed"])
    repeated(store, CMD2, ["failed", "sent"])
    assert not store.card_delivered("k7m2")
    assert store.bump_target("k7m2") is None
    store.close()


def test_the_original_card_still_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "sent", mid(2))
    assert store.card_delivered("k7m2")
    store.close()


def test_a_repeat_with_no_parts_never_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=1), None)
    store.add_repeat("k7m2", CMD, 0, T1)
    assert not store.card_delivered("k7m2")
    store.close()


def test_a_repeat_is_recorded_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    store.add_repeat("k7m2", CMD, 2, T1)
    store.add_repeat("k7m2", CMD, 5, T2)        # a replay of the same command changes nothing
    assert store.db.execute("SELECT parts, created_at FROM ask_repeats").fetchall() == [(2, T1)]
    store.close()


def test_the_bump_target_prefers_the_original_then_the_earliest_full_repeat(tmp_path: Path) -> None:
    """B3/B13: the original's chunk 0 whenever it is sent; otherwise the first chunk of the earliest fully
    sent repeat; otherwise none. A late original becomes the target."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "pending")
    outbox(store, "ask:k7m2:1", "sent", mid(2))
    assert store.bump_target("k7m2") is None
    repeated(store, CMD2, ["sent", "pending"], T1)         # the earliest, but not fully sent
    repeated(store, CMD, ["sent", "sent"], T2)
    repeated(store, "a3" * 32, ["sent"], "2026-10-07T03:00:00+00:00")
    assert store.bump_target("k7m2") == mid(0xa1 * 100)
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = ?",
                                     (f"askr:k7m2:{CMD2}:1",)).fetchone()[0], mid(9))
    assert store.bump_target("k7m2") == mid(0xa2 * 100)      # now the earliest fully sent
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = 'ask:k7m2:0'").fetchone()[0], mid(1))
    assert store.bump_target("k7m2") == mid(1)               # the late original
    store.close()


def test_the_bump_target_is_the_original_chunk_0_even_if_the_card_is_partial(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "failed")
    outbox(store, f"askd:k7m2:{CMD}:0", "sent", mid(5))
    assert store.bump_target("k7m2") == mid(1)
    store.close()


@pytest.mark.parametrize(("key", "blocks"), [
    (f"asknote:k7m2:bump:{CMD}:0", True), ("asknote:k7m2:autobump:3:1", True), (f"askr:k7m2:{CMD}:0", True),
    ("asknote:k7m2:cancelled:0:0", False), ("ask:k7m2:0", False), (f"askd:k7m2:{CMD}:0", False),
    ("asknote:p4xw:autobump:3:0", False), (f"askr:p4xw:{CMD}:0", False)])
def test_pending_reminders(tmp_path: Path, key: str, blocks: bool) -> None:
    """B14: a pending reminder or repeat chunk of the ask blocks an automatic bump; failed or sent ones do
    not, and neither does anything else."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "pending")
    assert store.reminder_pending("k7m2") is blocks
    seq = store.db.execute("SELECT seq FROM outbox WHERE key = ?", (key,)).fetchone()[0]
    store.mark_failed(seq)
    assert not store.reminder_pending("k7m2")
    store.close()
