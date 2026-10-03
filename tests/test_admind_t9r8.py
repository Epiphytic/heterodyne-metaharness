"""Task 9 review round 8: the abandonment's safety change commits on its own. A failing notice or audit
cannot keep an obsolete anchor, and cannot stop a dead agent being relaunched. Fakes and tmp_path only; no
real wn-agent, claude, systemctl, network or ~/.claude.
"""

from pathlib import Path

import pytest
from test_admind_r1 import Unit, run
from test_admind_t9r1 import supervised_unit
from test_admind_t9r5 import arm

from heterodyne.admind.hook import HookEvent
from heterodyne.admind.store import Store


def test_a_failed_supersession_notice_still_clears_the_old_anchor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    arm(u)
    real = Store.enqueue
    hits: list[str] = []

    def enqueue(self: Store, key: str, text: str, reply_to: str | None, lane: int = 1) -> bool:
        if key.startswith("abandoned:") and not hits:
            hits.append(key)
            raise OSError("outbox write failed")
        return real(self, key, text, reply_to, lane)

    async def scenario() -> None:
        a = await u.say("job A")
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="job A"))     # A anchored
        assert u.store.get("anchor") == a
        monkeypatch.setattr(Store, "enqueue", enqueue)
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="typed at the terminal"))   # B
        assert hits
        assert u.store.get("anchor") is None and u.store.get("in_flight") is None
        assert u.store.get("busy") is not None
        await u.daemon.on_hook(HookEvent("Stop", "S1", last_assistant_message="reply to B"))
        replies = [(t, r) for k, t, r in u.outbox() if k.startswith("reply:")]
        assert replies == [("reply to B", None)]            # never threaded to A
    run(scenario())


def test_a_persistent_abandonment_audit_failure_does_not_stop_a_dead_agent_relaunching(
        tmp_path: Path) -> None:
    u, clock, launches = supervised_unit(tmp_path)
    real = u.audit.write

    def write(kind: str, **fields: object) -> None:
        if fields.get("action") == "abandon":
            raise OSError("audit filesystem unavailable")
        real(kind, **fields)
    u.audit.write = write       # type: ignore[method-assign]
    u.daemon.ready.set()

    async def scenario() -> None:
        a = await u.say("job A")
        assert u.store.get("in_flight") == a
        u.tmux.pane_dead = lambda name: True            # type: ignore[method-assign]
        await u.daemon.check_agent()
        assert len(launches) == 1                       # relaunched despite the failing audit
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
        assert any("No reply to this message" in t for t in u.texts())
        u.daemon.ready.set()
        clock.now += 1
        await u.daemon.check_agent()
        assert len(launches) == 2
    run(scenario())


def test_a_persistent_abandonment_notice_failure_does_not_stop_a_dead_agent_relaunching(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u, _clock, launches = supervised_unit(tmp_path)
    u.daemon.ready.set()
    real = Store.enqueue

    def enqueue(self: Store, key: str, text: str, reply_to: str | None, lane: int = 1) -> bool:
        if key.startswith("abandoned:"):
            raise OSError("outbox write failed")
        return real(self, key, text, reply_to, lane)

    async def scenario() -> None:
        await u.say("job A")
        monkeypatch.setattr(Store, "enqueue", enqueue)
        u.tmux.pane_dead = lambda name: True            # type: ignore[method-assign]
        await u.daemon.check_agent()
        assert len(launches) == 1
        assert u.store.get("in_flight") is None and u.store.get("busy") is None
    run(scenario())


def test_a_failing_audit_cannot_skip_the_restart_after_a_definite_send_failure(tmp_path: Path) -> None:
    from heterodyne.tmux import TmuxError
    u = Unit(tmp_path)
    arm(u)
    real = u.audit.write
    starts: list[int] = []

    def write(kind: str, **fields: object) -> None:
        if fields.get("action") == "send-failed":
            raise OSError("audit filesystem unavailable")
        real(kind, **fields)
    u.audit.write = write       # type: ignore[method-assign]

    def paste(name: str, text: str) -> None:
        raise TmuxError("tmux paste failed")
    u.tmux.paste = paste       # type: ignore[method-assign]

    async def scenario() -> None:
        async def start_agent(relaunch: bool = False) -> None:
            starts.append(1)
        u.daemon.start_agent = start_agent      # type: ignore[method-assign]
        await u.say("job A")
        assert starts == [1]
        assert u.store.get("in_flight") is None
    run(scenario())
