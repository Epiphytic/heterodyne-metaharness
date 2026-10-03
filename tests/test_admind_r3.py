"""Review round 3 for the admind daemon: the anchor of a message whose send failed definitively.
Same rules as rounds 1 and 2: fakes, tmp_path and explicit barriers only; no sleeps, no real wn-agent,
claude, systemctl, network or ~/.claude.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from test_admind_r1 import Unit, run

from heterodyne.admind.hook import HookEvent
from heterodyne.tmux import TmuxError


def test_a_prompt_hook_during_a_failing_send_leaves_no_orphan_anchor_for_the_retry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    started = threading.Event()
    release = threading.Event()
    attempts: list[str] = []

    def paste(name: str, text: str) -> None:
        attempts.append(text)
        if len(attempts) == 1:
            started.set()
            assert release.wait(10), "test never released the paste"
            raise TmuxError("load-buffer failed")      # definite: nothing reached the pane
    u.tmux.paste = paste                                # type: ignore[method-assign]
    monkeypatch.setattr("heterodyne.admind.daemon.reply_text", lambda ev, *_: "the answer to A")

    async def scenario() -> None:
        sending = asyncio.create_task(u.say("prompt A"))
        assert await asyncio.to_thread(started.wait, 10)         # A is reserved; its paste is pending
        # A terminal prompt with text identical to A's arrives meanwhile; it waits for the dispatch.
        hook = asyncio.create_task(u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A")))
        a = u.store.get("in_flight")
        assert a is not None and not hook.done() and u.store.get("anchor") is None
        release.set()
        assert await sending == a
        await hook
        assert u.daemon.held == [(a, "prompt A")] and u.store.get("in_flight") is None
        assert u.store.get("anchor") is None                     # not delivered: nothing to anchor to
        assert u.store.get("busy") is not None                   # the newer turn's busy state is kept
        await u.say("!interrupt")
        # The command's own flush retries A into the now idle agent, with no stale anchor in the way.
        assert u.store.get("anchor") is None
        assert attempts == ["prompt A", "prompt A"] and u.store.get("in_flight") == a
        await u.daemon.on_hook(HookEvent("UserPromptSubmit", "S1", prompt="prompt A"))
        assert u.store.get("anchor") == a                        # the genuine prompt hook anchors it
        await u.daemon.on_hook(HookEvent("Stop", "S1"))
        replies = [(t, r) for k, t, r in u.outbox() if k.startswith("reply:")]
        assert replies == [("the answer to A", a)]
        assert not any("another prompt started a turn" in t for t in u.texts())
    run(scenario())


def test_interrupt_clears_an_anchor_left_without_a_reservation(tmp_path: Path) -> None:
    u = Unit(tmp_path)

    async def scenario() -> None:
        u.store.set("anchor", "aa" * 32)
        u.store.set("busy", "7")
        await u.say("!interrupt")
        assert u.store.get("anchor") is None and u.store.get("busy") is None
    run(scenario())
