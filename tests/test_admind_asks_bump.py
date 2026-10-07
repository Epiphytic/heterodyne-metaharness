"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

from pathlib import Path

import pytest
from test_admind_settings import BASE_CONFIG, write

from heterodyne.admind import commands
from heterodyne.admind.settings import resolve
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
