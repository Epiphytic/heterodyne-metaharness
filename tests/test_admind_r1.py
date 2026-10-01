"""Review round 1 for the admind daemon: races, crash windows and hostile input, tested without tmux.

The daemon runs over a recording fake tmux and a stub control client; races are forced with explicit
barriers (events, patched awaitables), never sleeps. Nothing here starts a real wn-agent, claude,
systemctl or tmux server, or touches the network or ~/.claude.
"""

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent
from fakes.settings import OPERATOR_HEX, make_settings

from heterodyne.admind.agent import SESSION, AdminAgent
from heterodyne.admind.audit import Audit
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import Admind
from heterodyne.admind.store import Store, now
from heterodyne.marmot.control import ControlError, InboundMessage, decode_event
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.services import UnitStatus

GROUP = "b2" * 32
STRANGER = "e5" * 32
_EVENTS = FakeWnAgent(Path("/nonexistent"))     # only used to build event frames, never started


class Services:
    def __init__(self) -> None:
        self.restart_started = threading.Event()
        self.restart_release = threading.Event()
        self.block_restart = False

    def restart(self, unit: str) -> tuple[bool, str]:
        if self.block_restart:
            self.restart_started.set()
            assert self.restart_release.wait(10), "test never released the restart"
        return True, ""

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "active", "running", "")


class FakeTmux:
    """Records what the daemon does to the admin agent's pane. `paste_error` is raised by paste()."""

    def __init__(self) -> None:
        self.sessions = {SESSION}
        self.pasted: list[str] = []
        self.keys: list[str] = []
        self.paste_error: BaseException | None = None

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self.sessions.add(name)

    def pane_dead(self, name: str) -> bool:
        return False

    def paste(self, name: str, text: str) -> None:
        if self.paste_error is not None:
            raise self.paste_error
        self.pasted.append(text)

    def send_key(self, name: str, key: str) -> None:
        self.keys.append(key)

    def capture(self, name: str, lines: int) -> str:
        return ""

    def kill(self, name: str) -> None:
        self.sessions.discard(name)


class StubClient:
    """The ControlClient surface Admind uses. Events come from `events`; `sent` records send_final."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.member_count = 2
        self.group_info_error: ControlError | None = None
        self.sent: list[SimpleNamespace] = []
        self.subscribe_calls = 0
        self.subscribe_failures = 0           # the next N subscribe() calls raise a retryable error
        self.on_subscribe: Callable[[], None] | None = None

    async def group_info(self, account: str, group: str) -> Any:
        if self.group_info_error is not None:
            raise self.group_info_error
        return SimpleNamespace(group_id_hex=group, member_count=self.member_count)

    async def send_final(self, account: str, group: str, text: str, reply_to: str | None,
                         key: str | None = None) -> Any:
        self.sent.append(SimpleNamespace(text=text, reply_to=reply_to, key=key))
        return SimpleNamespace(message_ids_hex=[hashlib.sha256(f"{len(self.sent)}".encode()).hexdigest()])

    async def subscribe(self, account: str, group: str) -> AsyncIterator[Any]:
        self.subscribe_calls += 1
        if self.on_subscribe is not None:
            self.on_subscribe()
        if self.subscribe_failures > 0:
            self.subscribe_failures -= 1
            raise ControlError("wn-agent closed the connection", "socket_closed", True)
        while True:
            yield await self.events.get()


def inbound(text: str, mid: str, sender: str = OPERATOR_HEX) -> InboundMessage:
    frame = {"marmot_agent_control": "marmot.agent-control.v2", "id": "r",
             **_EVENTS.message_event(text, sender, mid)}
    event = decode_event(json.dumps(frame).encode(), "r")
    assert isinstance(event, InboundMessage)
    return event


def membership_change() -> Any:
    frame = {"marmot_agent_control": "marmot.agent-control.v2", "id": "r", "type": "group_state_changed",
             "account_id_hex": ACCOUNT, "group_id_hex": GROUP, "event_id_hex": "f6" * 32,
             "change": "member_added"}
    return decode_event(json.dumps(frame).encode(), "r")


class Unit:
    """An Admind over fakes, with a running, idle admin agent and an operator already seen."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.settings = make_settings(tmp_path)
        self.store = Store(self.settings.state_dir / "admind.db")
        self.audit = Audit(self.settings.state_dir / "audit.jsonl")
        self.tmux = FakeTmux()
        self.services = Services()
        self.client = StubClient()
        self.build()
        self.store.set("agent_session", "S1")
        self.agent.started("S1")
        self.store.set("operator_seen_at", now())
        self.daemon.ready.set()
        self.daemon.group_ok = True
        self.seq = 0

    def build(self) -> None:
        """(Re)create the daemon over the same store, as after a restart."""
        self.agent = AdminAgent(self.tmux, self.store, self.settings, self.settings.state_dir / "hook.sock")
        runner = CommandRunner(self.agent, self.services, ("fake.service",), lambda: True)
        self.daemon = Admind(self.settings, self.client, self.store, self.audit, self.agent, runner,  # type: ignore[arg-type]
                             ACCOUNT, GROUP)

    def mid(self) -> str:
        self.seq += 1
        return f"{self.seq:064x}"

    async def say(self, text: str) -> str:
        mid = self.mid()
        await self.daemon.on_message(inbound(text, mid))
        return mid

    def audit_text(self) -> str:
        return (self.settings.state_dir / "audit.jsonl").read_text()

    def outbox(self) -> list[tuple[str, str, str | None]]:
        return [(r.key, r.text, r.reply_to) for r in self.store.pending()]

    def texts(self) -> list[str]:
        return [t for _, t, _ in self.outbox()]


def run(coro: Awaitable[Any]) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coro, 20)
    return asyncio.run(bounded())


# --- 1. alert outbox keys never carry the alert's name ----------------------------------------------------

def secret_token() -> str:
    return "sk" + "-" + "Q" * 24


def write_alert(u: Unit, raw_name: bytes, text: str = "disk is full") -> None:
    u.settings.alerts_dir.mkdir(parents=True, exist_ok=True)
    stem = raw_name.decode("utf-8", "surrogateescape")
    ident = stem.removesuffix(".json")
    (u.settings.alerts_dir / stem).write_text(json.dumps({"id": ident, "created_at": "t", "text": text}))


def test_alert_keys_and_audit_never_hold_a_name_that_is_an_identifier_or_a_secret(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    npub = hex_to_npub(STRANGER)
    token = secret_token()
    names = [f"{npub}.json", f"{token}.json", f"{'ab' * 32}.json"]
    for n in names:
        write_alert(u, n.encode())

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
        await u.daemon.relay_alerts()           # a second sweep relays nothing new
        await u.daemon.outbox_pass()
    run(scenario())
    assert len(u.client.sent) == 3
    for sent in u.client.sent:
        key = sent.key
        assert key.startswith("alert:") and len(key) == len("alert:") + 32
        assert all(c in "0123456789abcdef" for c in key[len("alert:"):])
    expected = {"alert:" + hashlib.sha256(os.fsencode(n.removesuffix(".json"))).hexdigest()[:32]
                for n in names}
    assert {s.key for s in u.client.sent} == expected
    everything = u.audit_text() + "\n".join(s.text + s.key for s in u.client.sent)
    for banned in (npub, STRANGER, token, "ab" * 32):
        assert banned not in everything, banned[:8]
    assert any(k in u.audit_text() for k in expected)           # the audit correlates on the opaque key


# --- 2. an alert file whose name is not UTF-8 -------------------------------------------------------------

def test_a_non_utf8_alert_name_is_reported_safely_and_never_blocks_later_alerts(tmp_path: Path) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"bad\xff\xfe.json")
    write_alert(u, b"later.json", "still relayed")

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(scenario())
    texts = [s.text for s in u.client.sent]
    assert any("still relayed" in t for t in texts)
    assert any("\\xff" in t for t in texts)                      # the bad one is reported, escaped
    for sent in u.client.sent:
        sent.text.encode("utf-8")                                # no surrogate escapes anywhere
        sent.key.encode("utf-8")
    u.audit_text().encode("utf-8")

    async def again() -> None:                                    # relayed once only
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(again())
    assert len(u.client.sent) == 2


def test_an_alert_that_fails_to_relay_is_audited_by_type_and_never_blocks_the_rest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    u = Unit(tmp_path)
    write_alert(u, b"first.json")
    write_alert(u, b"second.json", "second goes out")
    real = u.store.relay_alert
    calls = [0]

    def flaky(name: bytes, key: str, text: str) -> bool:
        calls[0] += 1
        if calls[0] == 1:
            raise ValueError(f"boom {hex_to_npub(STRANGER)}")
        return real(name, key, text)
    monkeypatch.setattr(u.store, "relay_alert", flaky)

    async def scenario() -> None:
        await u.daemon.relay_alerts()
        await u.daemon.outbox_pass()
    run(scenario())
    assert any("second goes out" in s.text for s in u.client.sent)
    audit = u.audit_text()
    assert "ValueError" in audit and hex_to_npub(STRANGER) not in audit and "boom" not in audit
