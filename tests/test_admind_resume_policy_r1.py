"""Independent first-review regression checks; disposable stores and fake tmux only."""
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_admind_agent import make_agent
from test_admind_r1 import Unit, run

from heterodyne.admind.agent import AdminAgent, AgentStuck


def test_crash_after_first_new_commit_cannot_authorize_automatic_replacement(tmp_path: Path) -> None:
    agent, tmux, store = make_agent(tmp_path)
    agent.ensure_running()
    old_sid = agent.session_id
    transaction = store.transaction
    effects: list[str] = []
    def record_kill(name: str) -> None:
        effects.append(name)
    tmux.kill = record_kill

    class Crash(BaseException):
        pass

    @contextmanager
    def crash_after_commit(*, read_only: bool = False, notify_fulfillment: bool = True):
        with transaction(read_only=read_only, notify_fulfillment=notify_fulfillment):
            yield
        if store.get("launch_new_intent") is not None:
            store.transaction = transaction
            raise Crash

    store.transaction = crash_after_commit
    with pytest.raises(Crash):
        agent.new()
    replacement = AdminAgent(tmux, store, agent.settings, agent.hook_socket)
    with pytest.raises(AgentStuck):
        replacement.ensure_running()
    assert effects == []
    assert old_sid is not None
    assert store.get("launch_attempt_intent") == store.get("launch_new_intent")
    assert store.get("launch_issued") is not None


def test_startup_hold_posts_notice_and_later_prompt_gets_held_reply_once(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    u.agent.hold_launch("resume-eligibility-unqualified")

    async def scenario() -> None:
        await u.daemon.start_agent(startup=True)
        assert u.daemon.stuck is not None
        assert any("recovery is paused" in t for t in u.texts())
        mid = await u.say("please report progress")
        assert u.daemon.held == [(mid, "please report progress")]
        assert any("Not delivered:" in t for t in u.texts())
        count = len(u.outbox())
        await u.daemon.flush()
        assert len(u.outbox()) == count
        assert u.store.inbound_status(mid) == "received"
        assert u.tmux.pasted == []
    run(scenario())


def test_new_disposes_old_queued_prompts_without_replaying_into_fresh_context(tmp_path: Path) -> None:
    from heterodyne.admind.hook import HookEvent

    u = Unit(tmp_path)

    async def scenario() -> None:
        u.agent.hold_launch("resume-eligibility-unqualified")
        await u.daemon.start_agent(startup=True)
        mid = await u.say("perform the previous plan")
        await u.say("!new")
        assert u.daemon.held == []
        assert u.store.inbound_status(mid) == "done"
        assert any("Resend with the context" in t for t in u.texts())
        sid = u.agent.session_id
        assert sid is not None
        await u.daemon.on_hook(HookEvent("SessionStart", sid, source="startup", launch=u.agent.launch_nonce))
        await u.daemon.flush()
        assert u.tmux.pasted == []
        assert "explicitly replaced" in u.agent.recovery_status()
    run(scenario())
