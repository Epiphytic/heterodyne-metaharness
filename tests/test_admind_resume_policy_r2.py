"""Second full-review regressions; disposable stores and recording fake tmux only."""

import subprocess
from pathlib import Path

import pytest
from test_admind_agent import ACCOUNT_B, make_agent
from test_admind_r1 import Unit, run

from heterodyne.admind.hook import HookEvent
from heterodyne.tmux import TmuxError


@pytest.mark.parametrize("error", [TmuxError("probe unavailable"), subprocess.TimeoutExpired("fake", 1)])
def test_adoption_probe_uncertainty_never_persists_launch_hold(tmp_path: Path, error: Exception) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    sid, nonce = agent.session_id, agent.launch_nonce
    absent = tmux.session_absent

    def unavailable(name: str) -> bool:
        raise error

    tmux.session_absent = unavailable
    with pytest.raises(type(error)):
        agent.ensure_running()
    assert store.get("launch_hold") is None
    assert (agent.session_id, agent.launch_nonce) == (sid, nonce)
    tmux.session_absent = absent
    assert agent.ensure_running() == "adopted"
    assert store.get("launches_without_start") == "1"


@pytest.mark.parametrize(
    "error", [TmuxError("probe unavailable"), subprocess.TimeoutExpired("fake", 1), OSError("temporary")]
)
def test_poll_probe_uncertainty_is_not_pane_death(tmp_path: Path, error: Exception) -> None:
    u = Unit(tmp_path)

    def unavailable() -> bool:
        raise error

    u.agent.alive = unavailable
    run(u.daemon.check_agent())
    assert u.store.get("launch_hold") is None
    assert u.daemon.stuck is None
    assert u.daemon.ready.is_set()
    assert not u.outbox()


def test_startup_adoption_probe_error_can_recover_without_new_conversation(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    sid, nonce = u.agent.session_id, u.agent.launch_nonce
    alive = u.agent.alive

    def unavailable() -> bool:
        raise TmuxError("probe unavailable")

    u.agent.alive = unavailable
    run(u.daemon.start_agent(startup=True))
    assert u.store.get("launch_hold") is None
    u.agent.alive = alive
    u.build()
    run(u.daemon.start_agent(startup=True))
    assert (u.agent.session_id, u.agent.launch_nonce) == (sid, nonce)
    assert u.daemon.stuck is None
    assert u.daemon.is_ready()


def test_refused_session_start_notifies_and_holds_immediately(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.daemon.on_session_start(HookEvent("SessionStart", "S1", source="clear", launch=u.agent.launch_nonce))
    assert u.store.get("launch_hold") == "session-start-source-unqualified"
    assert u.daemon.stuck is not None
    assert not u.daemon.ready.is_set()
    assert len(u.outbox()) == 1
    assert "recovery is paused" in u.texts()[0]


def test_deconfigured_account_cannot_classify_any_current_hook(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.store.set("agent_account", ACCOUNT_B.key)
    for name in ["SessionStart", "UserPromptSubmit", "Stop"]:
        assert u.daemon.classify(HookEvent(name, "S1", launch=u.agent.launch_nonce)) == "stale"
    run(u.daemon.start_agent(startup=True))
    assert any("login changed" in text and "not been replaced" in text for text in u.texts())
    assert u.agent.session_id == "S1"
    assert u.tmux.sessions == {"admin"}


def test_sticky_hold_notice_deduplicates_across_daemon_restarts(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.agent.hold_launch("launch-unacknowledged")
    run(u.daemon.start_agent(startup=True))
    first = list(u.outbox())
    u.build()
    run(u.daemon.start_agent(startup=True))
    assert u.outbox() == first
    assert len(first) == 1
    assert "not running" not in first[0][1]


def test_busy_startup_ack_holds_without_releasing_original_obligation(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.store.set("busy", "original-busy")
    u.store.set("in_flight", "original-message")
    u.daemon.on_session_start(HookEvent("SessionStart", "S1", source="startup", launch=u.agent.launch_nonce))
    assert u.store.get("launch_hold") == "startup-during-turn"
    assert u.daemon.stuck is not None
    assert u.store.get("busy") == "original-busy"
    assert u.store.get("in_flight") == "original-message"
    assert not u.daemon.ready.is_set()


def test_failed_new_notice_reports_request_not_successful_replacement(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        u.agent.hold_launch("launch-unacknowledged")
        await u.daemon.start_agent(startup=True)
        mid = await u.say("old work")

        def fail(name: str) -> None:
            raise TmuxError("injected replacement failure")

        u.tmux.kill = fail
        await u.say("!new")
        text = "\n".join(t for _, t, target in u.outbox() if target == mid)
        assert "!new was requested" in text
        assert "original conversation was replaced" not in text
        assert u.daemon.stuck is not None
        assert u.store.inbound_status(mid) == "done"
        assert not u.tmux.pasted

    run(scenario())


def test_new_denies_revoked_held_sender_without_success_notice(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        u.agent.hold_launch("launch-unacknowledged")
        await u.daemon.start_agent(startup=True)
        mid = await u.say("old work")
        original = u.daemon.sender_current
        held_mid = mid
        u.daemon.sender_current = lambda mid: mid != held_mid and original(mid)
        before = [(key, text, target) for key, text, target in u.outbox() if target == mid]
        await u.say("!new")
        assert u.store.inbound_status(mid) == "dropped"
        after = [(key, text, target) for key, text, target in u.outbox() if target == mid]
        assert after[: len(before)] == before
        assert len(after) == len(before) + 1
        assert "could not verify the group membership" in after[-1][1]
        assert "!new was requested" not in after[-1][1]
        assert u.daemon.held == []

    run(scenario())
