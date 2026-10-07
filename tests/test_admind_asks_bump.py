"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

import sqlite3
from pathlib import Path

import pytest
from test_admind_asks import row
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
