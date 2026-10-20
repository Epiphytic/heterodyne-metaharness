"""Crash recovery preserves uncertain launches and held inputs without automatic replacement.

The daemon is rebuilt over the same disposable SQLite file and fake tmux. Explicit
!new is the only fresh conversation operation. Audit containment tests remain unchanged.
"""

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from admind_waits import wait_until
from test_admind_r1 import Unit, run, write_alert
from test_admind_t9r1 import supervised_unit

from heterodyne.admind import alerts
from heterodyne.admind.daemon import ALERT_FAILED, supervised


class Crash(BaseException):
    """The process dies here (not an Exception: nothing may catch and carry on)."""


def instrument(u: Unit) -> tuple[list[str], list[str]]:
    kills: list[str] = []
    launches: list[str] = []
    kill, new_session = u.tmux.kill, u.tmux.new_session

    def k(name: str) -> None:
        kills.append(name)
        kill(name)

    def n(name: str, cwd: Path, argv: list[str], **env: Any) -> None:
        launches.append(name)
        new_session(name, cwd, argv, **env)

    u.tmux.kill = k  # type: ignore[method-assign]
    u.tmux.new_session = n  # type: ignore[method-assign]
    return kills, launches


async def restart_and_check(
    u: Unit, kills: list[str], launches: list[str], old_nonce: str, new_session: bool
) -> None:
    """An uncertain launch never turns an old request into automatic replacement."""
    before = list(u.tmux.pasted)
    effects = (list(kills), list(launches))
    sid, nonce = u.store.get("agent_session"), u.store.get("launch_nonce")
    u.build()
    u.daemon.recover()
    await u.daemon.start_agent()
    assert (kills, launches) == effects
    assert u.store.get("agent_session") == sid and u.store.get("launch_nonce") == nonce
    assert u.daemon.stuck is not None and not u.daemon.is_ready()
    b = await u.say("job B")
    assert u.tmux.pasted == before and u.daemon.held == [(b, "job B")]
    assert u.store.inbound_status(b) == "received"
    assert u.store.get("launch_hold") is not None


def test_a_ready_timeout_crash_between_abandonment_and_replacement_never_adopts_the_old_pane(
    tmp_path: Path,
) -> None:
    u, clock, _ = supervised_unit(tmp_path)
    kills, launches = instrument(u)
    old_nonce = u.store.get("launch_nonce")
    assert old_nonce is not None

    async def scenario() -> None:
        first = await u.say("job A")
        u.daemon.ready.clear()
        clock.now += u.daemon.ready_timeout + 1
        u.tmux.pane_dead = lambda name: False  # type: ignore[method-assign]
        original = u.agent.hold_launch

        def crash(reason: str) -> None:
            original(reason)
            raise Crash

        u.agent.hold_launch = crash  # type: ignore[method-assign]
        with pytest.raises(Crash):
            await u.daemon.check_agent()
        assert u.store.get("busy") is not None and u.store.get("in_flight") == first
        assert u.store.get("launch_hold") is not None
        assert not kills and not launches
        u.tmux.pane_dead = lambda name: False  # type: ignore[method-assign]
        await restart_and_check(u, kills, launches, old_nonce, new_session=False)

    run(scenario())


def test_a_dead_pane_crash_between_abandonment_and_replacement_never_adopts_a_revived_pane(
    tmp_path: Path,
) -> None:
    u, clock, _ = supervised_unit(tmp_path)
    kills, launches = instrument(u)
    old_nonce = u.store.get("launch_nonce")
    assert old_nonce is not None

    async def scenario() -> None:
        first = await u.say("job A")
        u.daemon.ready.clear()
        clock.now += u.daemon.ready_timeout + 1
        u.tmux.pane_dead = lambda name: True  # type: ignore[method-assign]
        original = u.agent.hold_launch

        def crash(reason: str) -> None:
            original(reason)
            raise Crash

        u.agent.hold_launch = crash  # type: ignore[method-assign]
        with pytest.raises(Crash):
            await u.daemon.check_agent()
        assert u.store.get("busy") is not None and u.store.get("in_flight") == first
        assert u.store.get("launch_hold") is not None
        assert not kills and not launches
        u.tmux.pane_dead = lambda name: False  # type: ignore[method-assign]
        await restart_and_check(u, kills, launches, old_nonce, new_session=False)

    run(scenario())


def test_a_new_crash_between_the_idle_commit_and_the_kill_never_adopts_the_old_pane(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    kills, launches = instrument(u)
    old_nonce = u.store.get("launch_nonce")
    assert old_nonce is not None

    async def scenario() -> None:
        await u.say("job A")

        async def crash(cmd: object) -> tuple[str, bool]:
            raise Crash

        u.daemon.execute = crash  # type: ignore[method-assign]
        with pytest.raises(Crash):
            await u.say("!new")
        assert u.store.get("replace_pending") == "new" and u.store.get("busy") is None
        assert u.store.get("launch_nonce") is None  # the departing nonce is already invalid
        assert u.tmux.has_session("admin") and kills == []
        await restart_and_check(u, kills, launches, old_nonce, new_session=True)

    run(scenario())


def test_a_crash_after_the_kill_and_before_the_launch_holds_the_replacement(tmp_path: Path) -> None:
    u, clock, _ = supervised_unit(tmp_path)
    kills, launches = instrument(u)
    old_nonce = u.store.get("launch_nonce")
    assert old_nonce is not None
    real = u.tmux.new_session

    def crash(name: str, cwd: Path, argv: list[str], **env: Any) -> None:
        raise Crash

    u.tmux.new_session = crash  # type: ignore[method-assign]

    async def scenario() -> None:
        with pytest.raises(Crash):
            await u.say("!new")
        assert u.store.get("replace_pending") is not None  # not cleared: nothing was started
        u.tmux.new_session = real  # type: ignore[method-assign]
        u.tmux.sessions.add("admin")  # (a pane the dying process left behind)
        await restart_and_check(u, kills, launches, old_nonce, new_session=False)

    run(scenario())


def test_a_live_pane_with_no_current_launch_nonce_is_held_not_adopted(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    kills, launches = instrument(u)
    u.store.delete("launch_nonce")
    u.build()

    async def scenario() -> None:
        await u.daemon.start_agent()
        assert not kills and not launches and u.agent.launch_nonce is None
        assert u.daemon.stuck is not None and u.store.get("launch_hold") is not None
        assert not u.daemon.is_ready()

    run(scenario())


# --- supervisor audit containment (R9 #2) ---


def broken_audit(u: Unit) -> None:
    def write(kind: str, **fields: object) -> None:
        raise OSError("audit filesystem unavailable")

    u.audit.write = write  # type: ignore[method-assign]


def test_alert_processing_that_raises_with_a_broken_audit_does_not_end_the_daemon_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    u = Unit(tmp_path)
    broken_audit(u)
    runs = [0]

    def scan(path: Path) -> list[tuple[str, alerts.Alert | None]]:
        runs[0] += 1
        raise OSError("alert directory unreadable")

    monkeypatch.setattr(alerts, "scan", scan)

    async def no_wait(_delay: float) -> None:
        await asyncio.sleep(0)

    u.daemon._sleep = no_wait  # type: ignore[method-assign]
    delays: list[float] = []

    async def backoff(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0)

    sibling_alive = asyncio.Event()

    async def sibling() -> None:
        sibling_alive.set()
        await asyncio.Event().wait()

    async def scenario() -> None:
        async def group() -> None:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(supervised("alerts", u.daemon.alerts_loop, u.audit, sleep=backoff))
                tg.create_task(sibling())

        tg_task = asyncio.create_task(group())
        await wait_until(lambda: runs[0] >= 3 and len(delays) >= 3)
        assert not tg_task.done() and sibling_alive.is_set()  # the group survived
        assert delays[:3] == [1.0, 2.0, 4.0]  # restarted with growing backoff
        tg_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tg_task

    run(scenario())


def test_alert_failed_records_its_fallback_before_an_audit_that_raises(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"one.json")
    broken_audit(u)

    def boom(name: str, raw: bytes, alert: object) -> None:
        raise RuntimeError("secret detail")

    u.daemon.relay_alert = boom  # type: ignore[method-assign]

    async def scenario() -> None:
        await u.daemon.relay_alerts()  # must not raise
        assert any(t.startswith(ALERT_FAILED.split("{")[0]) for t in u.texts())

    run(scenario())


def test_alert_failed_marks_the_skip_before_an_audit_that_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"one.json")
    broken_audit(u)

    def boom(*_a: object) -> bool:
        raise OSError("db unavailable")

    monkeypatch.setattr(u.store, "relay_alert", boom)

    async def scenario() -> None:
        await u.daemon.relay_alerts()  # must not raise
        assert u.daemon.bad_alerts == {b"one"}

    run(scenario())
