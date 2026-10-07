"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

import pytest

from heterodyne.admind import commands


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
