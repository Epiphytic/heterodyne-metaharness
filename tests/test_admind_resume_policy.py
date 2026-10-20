"""Offline default-deny owner policy; no CLI capability qualification or processes."""
from pathlib import Path

import pytest
from test_admind_agent import ACCOUNT_B, agent_on, make_agent

from heterodyne.admind.agent import AdminAgent, AgentStuck


def acknowledge(agent: AdminAgent) -> None:
    assert agent.session_id is not None
    assert agent.started(agent.session_id, "startup", agent.launch_nonce)


def test_initial_start_ack_is_current_readiness_not_resume_authority(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    assert agent.ensure_running() == "startup-launch-requested"
    assert not agent.startup_acknowledged()
    acknowledge(agent)
    assert agent.startup_acknowledged()
    assert store.get("launch_resume_eligibility") == "unqualified"
    assert store.get("launches_without_start") == "1"
    assert agent.ensure_running() == "adopted"
    tmux.dead.add("admin")
    old_sid, old_nonce = agent.session_id, agent.launch_nonce
    with pytest.raises(AgentStuck, match="resume-eligibility-unqualified"):
        agent.ensure_running()
    assert agent.session_id == old_sid and agent.launch_nonce == old_nonce
    assert store.get("launches_without_start") == "1"
    assert tmux.has_session("admin") and tmux.pane_dead("admin")


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize("relaunch", [False, True])
def test_any_automatic_retry_holds_before_kill_or_attempt(
        tmp_path: Path, started: bool, relaunch: bool) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    if started:
        acknowledge(agent)
    tmux.dead.add("admin")
    kills: list[str] = []
    tmux.kill = kills.append  # type: ignore[method-assign]
    with pytest.raises(AgentStuck):
        agent.ensure_running(relaunch)
    assert kills == [] and store.get("launches_without_start") == "1"
    assert store.get("launch_hold") == "resume-eligibility-unqualified"
    # A new owner/store view cannot clear or retry a sticky hold.
    replacement = AdminAgent(tmux, store, agent.settings, agent.hook_socket)
    with pytest.raises(AgentStuck):
        replacement.ensure_running()
    assert kills == [] and store.get("launches_without_start") == "1"


def test_new_clears_hold_and_requests_one_new_startup(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    old_sid = agent.session_id
    tmux.dead.add("admin")
    with pytest.raises(AgentStuck):
        agent.ensure_running()
    assert agent.new() == "new-launch-requested"
    assert agent.session_id != old_sid and store.get("launches_without_start") == "1"
    assert store.get("launch_hold") is None
    acknowledge(agent)
    tmux.dead.add("admin")
    with pytest.raises(AgentStuck):
        agent.ensure_running()
    assert store.get("launches_without_start") == "1"


@pytest.mark.parametrize("field,value", [("session_started", "S1"), ("agent_session", "S1"),
                                           ("launches_without_start", "0")])
def test_legacy_unknown_records_cannot_fall_back_to_fresh(
        tmp_path: Path, field: str, value: str) -> None:
    agent, tmux, store = make_agent(tmp_path)
    store.set(field, value)
    with pytest.raises(AgentStuck):
        agent.ensure_running()
    assert not tmux.has_session("admin")
    assert store.get("launch_hold") == "resume-eligibility-unqualified"


def test_caller_written_qualification_and_wrong_account_cannot_authorize(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    acknowledge(agent)
    store.set("launch_resume_eligibility", "qualified")
    changed = agent_on(tmp_path, store, tmux, ACCOUNT_B)
    with pytest.raises(AgentStuck):
        changed.ensure_running()
    assert not changed.login_matches()
    assert store.get("launches_without_start") == "1"


@pytest.mark.parametrize("source", ["resume", "compact", "clear", "other", ""])
def test_wrong_source_cannot_acknowledge(tmp_path: Path, source: str) -> None:
    agent, _, store = make_agent(tmp_path)
    agent.ensure_running()
    assert agent.session_id is not None
    assert not agent.started(agent.session_id, source, agent.launch_nonce)
    assert not agent.startup_acknowledged() and store.get("launch_ack_nonce") is None


def test_wrong_session_nonce_account_or_hold_cannot_acknowledge(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    assert agent.session_id is not None
    assert not agent.started("wrong", "startup", agent.launch_nonce)
    assert not agent.started(agent.session_id, "startup", "wrong")
    assert not agent.started(agent.session_id)
    changed = agent_on(tmp_path, store, tmux, ACCOUNT_B)
    assert not changed.started(agent.session_id, "startup", agent.launch_nonce)
    agent.hold_launch("ready-timeout")
    assert not agent.started(agent.session_id, "startup", agent.launch_nonce)
    assert store.get("launch_ack_nonce") is None


@pytest.mark.parametrize("after", [False, True])
def test_issued_new_crash_cannot_replay_pending_intent(tmp_path: Path, after: bool) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    spawn = tmux.new_session
    calls: list[str] = []

    class Crash(BaseException):
        pass

    def crash(*args, **kwargs) -> None:
        calls.append("spawn")
        if after:
            spawn(*args, **kwargs)
        raise Crash

    tmux.new_session = crash  # type: ignore[method-assign]
    with pytest.raises(Crash):
        agent.new()
    replacement = AdminAgent(tmux, store, agent.settings, agent.hook_socket)
    with pytest.raises(AgentStuck):
        replacement.ensure_running()
    assert calls == ["spawn"] and store.get("launches_without_start") == "1"
    assert store.get("launch_hold") == "launch-unacknowledged"


def test_daemon_adoption_requires_persisted_current_ack(tmp_path: Path) -> None:
    from test_admind_r1 import Unit, run

    u = Unit(tmp_path)
    u.store.delete("launch_ack_nonce")
    run(u.daemon.start_agent(startup=True))
    assert not u.daemon.is_ready()
    assert u.store.get("session_started") == "S1"  # legacy readiness is insufficient
    assert u.store.get("launches_without_start") is None


@pytest.mark.parametrize("source", ["resume", "compact", "clear", None, "bogus"])
def test_fresh_launch_wrong_source_cannot_become_ready(tmp_path: Path, source: str | None) -> None:
    from test_admind_r1 import Unit, run

    from heterodyne.admind.hook import HookEvent

    u = Unit(tmp_path)
    u.agent.new()
    assert u.agent.session_id is not None
    u.daemon.ready.clear()
    run(u.daemon.on_hook(HookEvent("SessionStart", u.agent.session_id,
                                  source=source, launch=u.agent.launch_nonce)))
    assert not u.daemon.is_ready() and not u.agent.startup_acknowledged()
    assert u.store.get("launch_hold") == "session-start-source-unqualified"


@pytest.mark.parametrize("fault", ["nonce", "session", "account", "retired"])
def test_daemon_wrong_binding_never_acknowledges(tmp_path: Path, fault: str) -> None:
    from test_admind_r1 import Unit, run

    from heterodyne.admind.hook import HookEvent

    u = Unit(tmp_path)
    u.agent.new()
    sid, nonce = u.agent.session_id, u.agent.launch_nonce
    assert sid is not None
    if fault == "nonce":
        nonce = "cd" * 16
    elif fault == "session":
        sid = "wrong"
    elif fault == "account":
        u.store.set("agent_account", "wrong")
    else:
        u.daemon.retired.add(sid)
    u.daemon.ready.clear()
    run(u.daemon.on_hook(HookEvent("SessionStart", sid, source="startup", launch=nonce)))
    assert not u.daemon.is_ready() and u.store.get("launch_ack_nonce") is None


@pytest.mark.parametrize("death", [False, True])
def test_daemon_supervision_holds_preserving_inputs_and_budget(tmp_path: Path, death: bool) -> None:
    from test_admind_r1 import Unit, run

    from heterodyne.admind.hook import HookEvent

    u = Unit(tmp_path)

    async def scenario() -> None:
        first = await u.say("first")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="first"))
        second = await u.say("second")
        count, nonce = u.store.get("launches_without_start"), u.agent.launch_nonce
        if death:
            u.tmux.sessions.clear()
        else:
            u.daemon.ready.clear()
            u.daemon.launched_at = u.daemon.clock() - u.daemon.ready_timeout - 1
        effects: list[str] = []
        u.tmux.kill = effects.append  # type: ignore[method-assign]
        await u.daemon.check_agent()
        await u.daemon.check_agent()
        await u.daemon.flush()
        assert effects == [] and u.agent.launch_nonce == nonce
        assert u.store.get("launches_without_start") == count
        assert u.store.get("in_flight") == first and u.store.get("anchor") == first
        assert u.daemon.held == [(second, "second")]
        assert u.daemon.stuck is not None and not u.daemon.is_ready()
        assert u.store.get("busy") is not None
    run(scenario())


def test_matching_new_startup_only_acknowledges_current_readiness(tmp_path: Path) -> None:
    from test_admind_r1 import Unit, run

    from heterodyne.admind.hook import HookEvent

    u = Unit(tmp_path)
    u.agent.new()
    assert u.agent.session_id is not None
    run(u.daemon.on_hook(HookEvent("SessionStart", u.agent.session_id,
                                  source="startup", launch=u.agent.launch_nonce)))
    assert u.daemon.is_ready() and u.agent.startup_acknowledged()
    assert "startup-ready" in u.audit_text() and '"action": "resumed"' not in u.audit_text()
    assert u.store.get("launch_resume_eligibility") == "unqualified"
