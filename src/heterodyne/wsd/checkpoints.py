"""Named crash windows. Every journaled flow calls its checkpoint between steps; production passes
`nothing`, and tests pass a callable that raises or blocks at a chosen name (crash-window and
interleaving tests). Each flow lists its names in a `POINTS` tuple, and a test checks that a clean run
passes exactly those, so a new window can't be added without a test that crashes in it."""

from collections.abc import Callable

Checkpoint = Callable[[str], None]


def nothing(_name: str) -> None:
    return None
