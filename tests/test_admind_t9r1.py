"""Task 9 review round 1, code fixes: rearm restores service, agent supervision, filesystem checks.
Fakes, tmp_path and explicit barriers only; no sleeps, no real wn-agent, claude, systemctl, network or
~/.claude.
"""

import asyncio
import contextlib
import os
import stat
from pathlib import Path

import pytest
from test_admind_r1 import Unit, run

from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HookEvent
from heterodyne.admind.store import Store, private_dir
from heterodyne.services import UnitStatus


def test_rearm_after_a_latched_count_check_restores_observing_without_a_restart(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.daemon.group_ok = False
    u.daemon.observing = False
    u.client.member_count = 3

    async def scenario() -> None:
        task = asyncio.create_task(u.daemon.inbound_loop())
        try:
            await u.client.acked.wait()                 # acknowledged, found three members, latched
            assert u.daemon.latched() and not u.daemon.observing
            u.client.member_count = 2
            u.store.delete("latched")                   # what `admind rearm` does
            assert await u.daemon.check_group()         # the periodic check passes
            assert u.daemon.observing and u.daemon.may_post()
            await u.say("hello")
            assert u.tmux.pasted == ["hello"]           # dispatch resumed
            u.daemon.post("k", "posted", None)
            await u.daemon.outbox_pass()
            assert "posted" in [s.text for s in u.client.sent]
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    run(scenario())


def test_a_passing_check_before_the_subscription_is_acknowledged_does_not_observe(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.daemon.observing = False

    async def scenario() -> None:
        assert await u.daemon.check_group()
        assert not u.daemon.observing
    run(scenario())


# --- agent supervision (decision D8) ---

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def supervised_unit(tmp_path: Path) -> tuple[Unit, Clock, list[str]]:
    u = Unit(tmp_path)
    clock = Clock()
    u.daemon.clock = clock
    u.daemon.launched_at = clock()
    launches: list[str] = []
    original = u.tmux.new_session

    def new_session(name: str, cwd: Path, argv: list[str]) -> None:
        launches.append(name)
        original(name, cwd, argv)
    u.tmux.new_session = new_session                    # type: ignore[method-assign]
    return u, clock, launches


def test_a_claude_that_never_sends_session_start_is_relaunched_then_reported(tmp_path: Path) -> None:
    u, clock, launches = supervised_unit(tmp_path)
    u.daemon.ready.clear()                              # launched, no SessionStart yet
    limit = u.daemon.ready_timeout

    async def scenario() -> None:
        await u.daemon.check_agent()
        assert launches == []                           # still within the readiness window
        for n in range(1, 4):
            clock.now += limit + 1
            await u.daemon.check_agent()
            assert len(launches) == n and u.daemon.stuck is None
            assert not u.daemon.ready.is_set()
        clock.now += limit + 1
        await u.daemon.check_agent()                    # the fourth failure meets the crash-loop limit
        assert len(launches) == 3 and u.daemon.stuck is not None
        notices = [t for t in u.texts() if "not running" in t]
        assert len(notices) == 1 and "!new" in notices[0]
        clock.now += 10 * limit
        await u.daemon.check_agent()                    # no tight loop: it waits for !new
        assert len(launches) == 3 and len([t for t in u.texts() if "not running" in t]) == 1
        await u.say("anyone there?")                    # a message now gets the stuck reply, not a hang
        assert any("Not delivered: the admin agent is not running" in t for t in u.texts())
    run(scenario())


def test_a_claude_that_exits_at_once_is_relaunched_without_waiting_for_the_timeout(tmp_path: Path) -> None:
    u, _clock, launches = supervised_unit(tmp_path)
    u.daemon.ready.clear()
    u.tmux.pane_dead = lambda name: True                # type: ignore[method-assign]

    async def scenario() -> None:
        for n in range(1, 4):
            await u.daemon.check_agent()
            assert len(launches) == n
        await u.daemon.check_agent()
        assert len(launches) == 3 and u.daemon.stuck is not None
        assert any("not running" in t for t in u.texts())
    run(scenario())


def test_an_agent_that_dies_mid_turn_abandons_the_turn_and_releases_the_queue(tmp_path: Path) -> None:
    u, _clock, launches = supervised_unit(tmp_path)
    dead: set[str] = set()
    u.tmux.pane_dead = lambda name: name in dead        # type: ignore[method-assign]
    original = u.tmux.new_session

    def relaunch(name: str, cwd: Path, argv: list[str]) -> None:
        dead.discard(name)
        original(name, cwd, argv)
    u.tmux.new_session = relaunch                       # type: ignore[method-assign]

    async def scenario() -> None:
        first = await u.say("long job")
        assert u.store.get("in_flight") == first and u.store.get("busy") is not None
        second = await u.say("next job")                # held behind the busy turn
        assert u.daemon.held == [(second, "next job")]
        dead.add("admin")
        await u.daemon.check_agent()
        assert any(t.startswith("No reply to this message") and "restarted" in t for t in u.texts())
        assert [r for k, _t, r in u.outbox() if k.startswith("abandoned:")] == [first]
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
        assert launches == ["admin"] and u.daemon.stuck is None
        assert u.tmux.pasted == ["long job"]            # not yet: the relaunched agent is not ready
        await u.daemon.hooks.put(HookEvent("SessionStart", "S1", source="startup"))
        ev = u.daemon.hooks.get_nowait()
        await u.daemon.on_hook(ev)
        await u.daemon.flush()
        assert u.tmux.pasted == ["long job", "next job"]    # the queue is released
    run(scenario())


def test_agent_loop_polls_at_the_injected_interval_and_relaunches(tmp_path: Path) -> None:
    u, _clock, launches = supervised_unit(tmp_path)
    u.daemon.agent_poll = 0.25
    sleeps: asyncio.Queue[float] = asyncio.Queue()
    go: asyncio.Queue[None] = asyncio.Queue()

    async def fake_sleep(delay: float) -> None:
        sleeps.put_nowait(delay)
        await go.get()
    u.daemon._sleep = fake_sleep                        # type: ignore[method-assign]

    async def scenario() -> None:
        task = asyncio.create_task(u.daemon.agent_loop())
        try:
            assert await sleeps.get() == 0.25
            u.tmux.sessions.discard("admin")            # the pane is gone
            go.put_nowait(None)
            assert await sleeps.get() == 0.25           # a full pass has run
            assert launches == ["admin"]
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    run(scenario())


def test_run_supervises_the_agent_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    names: list[str] = []

    async def record(name: str, factory: object, audit: object) -> None:
        names.append(name)
    monkeypatch.setattr("heterodyne.admind.daemon.supervised", record)
    run(u.daemon.run())
    assert "agent" in names


# --- filesystem checks and output boundary ---

def test_a_symlinked_state_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "state"
    link.symlink_to(real)
    for build in (lambda: Audit(link / "audit.jsonl"), lambda: Store(link / "admind.db"),
                  lambda: private_dir(link)):
        with pytest.raises(PermissionError):
            build()
    assert list(real.iterdir()) == []


def test_private_dir_makes_an_owned_directory_0700(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b"
    d.parent.mkdir()
    d.mkdir(mode=0o755)
    private_dir(d)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_an_existing_loose_audit_file_becomes_0600(tmp_path: Path) -> None:
    path = tmp_path / "state" / "audit.jsonl"
    path.parent.mkdir()
    path.write_text('{"kind": "old"}\n')
    path.chmod(0o644)
    Audit(path).write("test", n=1)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_text().splitlines()) == 2


def test_an_audit_path_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "state" / "audit.jsonl"
    path.parent.mkdir()
    os.mkfifo(path)
    with pytest.raises(PermissionError):
        Audit(path).write("test")


def test_a_symlinked_database_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    target.write_bytes(b"")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "admind.db").symlink_to(target)
    with pytest.raises(OSError):
        Store(tmp_path / "state" / "admind.db")
    assert target.read_bytes() == b""


def test_unit_status_line_passes_every_field_through_the_sanitizer() -> None:
    token = "sk" + "-" + "Q" * 24
    line = UnitStatus("wsd.service", f"active {token}", f"running {token}", f"since {token}").line()
    assert token not in line
    assert line.startswith("wsd.service: ") and "since" in line


def test_ready_notice_does_not_promise_that_every_reply_is_threaded() -> None:
    from heterodyne.admind.daemon import READY_NOTICE
    assert "replies normally come back in thread" in READY_NOTICE
