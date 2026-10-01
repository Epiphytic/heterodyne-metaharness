"""The bounded-wait helpers themselves: `stays` must check its predicate even when the process is
descheduled so long that its observation window has passed before the first loop turn."""

import asyncio

import pytest
from admind_waits import stays


def test_stays_fails_an_always_false_predicate_with_zero_loop_iterations(
        monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        real = loop.time
        ticks = iter([0.0])     # the first read computes `end`; every later read is far past it
        with monkeypatch.context() as m:
            m.setattr(loop, "time", lambda: next(ticks, 1_000_000.0))
            with pytest.raises(AssertionError, match="does not hold"):
                await stays(lambda: False, 0.05)
        assert loop.time == real
    asyncio.run(scenario())


def test_stays_passes_a_true_predicate_and_catches_a_late_change() -> None:
    async def scenario() -> None:
        await stays(lambda: True, 0.01)
        flag = [True]
        asyncio.get_running_loop().call_later(0.01, flag.clear)
        with pytest.raises(AssertionError, match="stopped holding"):
            await stays(lambda: bool(flag), 0.2)
    asyncio.run(scenario())
