"""The admind daemon (ADR 0001 §8). See plan 2, Task 8, "Behaviour", for the contract the tests pin.

Output safety. Everything that leaves this module is one of:
- fixed wording, or a number;
- a message ID (an event hash, not an identity), audited for correlation;
- an operator's own text, audited only after `show` (secrets and identifiers redacted, controls escaped);
- the admin agent's reply or the `!tail` screen, relayed to the operator in chat because the brief says
  so, and never written to the audit log.
Peer- and child-supplied text (a stranger's message, an agent reply, a terminal prompt, a peer's error
`detail`, a hook's session ID or event name) is never logged. Exceptions are reported by type only.
"""

import asyncio
import contextlib
import os
import re
import time
from collections.abc import Awaitable, Callable

from heterodyne.admind import alerts, chunk, commands, guard
from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HOOK_EVENTS, HookEvent, HookServer, reply_text, same_prompt
from heterodyne.admind.settings import AdmindSettings
from heterodyne.admind.store import Store, now
from heterodyne.config.secret_scan import show
from heterodyne.marmot.control import (
    ControlClient,
    ControlError,
    GroupStateChanged,
    InboundMessage,
    ReactionAdded,
)
from heterodyne.tmux import TmuxError, TmuxPasteUncertain

READY_NOTICE = ("admind is listening. Your messages go to the admin agent verbatim; replies come back "
                "in thread. " + commands.HELP)
RESTARTED_NOTICE = ("⚠️ admind restarted before this message reached the admin agent. "
                    "Resend it if it is still needed.")
NO_REPLY = "(the admin agent's turn ended without a text reply)"
NOT_READY = "The admin agent has not started yet; your message is queued. Use !tail to see its screen."
CONTROL_REFUSED = "Not delivered: the message contains terminal control characters."
UNVERIFIED = "Not delivered: admind could not verify the group membership. Try again shortly."
UNCONFIRMED = ("admind has not seen the admin agent take this message yet, so later messages are held. "
               "If the agent already answered, its answer was posted without a thread. "
               "Use !tail to look, or !interrupt to cancel this message and release the queue.")
LONG_TURN = ("The admin agent is still working, so later messages are held. "
             "Use !tail to look, or !interrupt to stop it and release the queue.")
ALERT_FAILED = ("🚨 A wsd alert file could not be relayed (ref {ref}). "
                "Check the alert directory on the host.")
UNCERTAIN = "delivery to the admin agent is uncertain; not retried — resend if needed"
MAX_SEND_ATTEMPTS = 10
MESSAGE_ID = re.compile(r"[0-9a-f]{64}")
AUDIT_TEXT_CHARS = 2000
KNOWN_HOOKS = frozenset(HOOK_EVENTS)
SESSION_SOURCES = frozenset({"startup", "resume", "clear", "compact"})   # Claude Code's SessionStart sources


def reason(exc: BaseException) -> str:
    """A reason fit for the chat and the audit log: never `str(exc)` of an OS, tmux or peer error."""
    if isinstance(exc, AgentStuck):
        return show(str(exc), False)    # admind's own wording
    if isinstance(exc, TmuxError):
        return "a tmux command failed"
    return "internal error"


def own_text(text: str) -> str:
    """The operator's own text for the audit log: secrets and identifiers redacted, controls escaped."""
    return show(text, False)[:AUDIT_TEXT_CHARS]


async def supervised(name: str, factory: Callable[[], Awaitable[None]], audit: Audit, *, base: float = 1.0,
                     cap: float = 30.0, healthy: float = 60.0, clock: Callable[[], float] = time.monotonic,
                     sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Run a background loop forever. If it raises, or returns, record its exception type (never its
    message) and restart it after a bounded, doubling delay that resets once a run lasted `healthy`
    seconds. Cancellation passes through. A loop must never die silently or spin."""
    delay = base
    while True:
        started = clock()
        try:
            await factory()
        except Exception as exc:  # noqa: BLE001 - the task boundary: contain, record the type, retry
            audit.write("task", name=name, action="crashed", error=type(exc).__name__)
        else:
            audit.write("task", name=name, action="returned")
        if clock() - started >= healthy:
            delay = base
        await sleep(delay)
        delay = min(delay * 2, cap)


class Admind:
    def __init__(self, settings: AdmindSettings, client: ControlClient, store: Store, audit: Audit,
                 agent: AdminAgent, runner: commands.CommandRunner, account: str, group: str) -> None:
        self.s = settings
        self.client = client
        self.store = store
        self.audit = audit
        self.agent = agent
        self.runner = runner
        self.account = account
        self.group = group
        self.hooks: asyncio.Queue[HookEvent] = asyncio.Queue()
        self.work: asyncio.Queue[InboundMessage] = asyncio.Queue()    # operator messages for worker_loop
        self.ready = asyncio.Event()
        self.held: list[tuple[str, str]] = []
        self.launched_at = time.monotonic()
        self.stuck: str | None = None
        self.not_ready_sent = False
        self.group_ok = False
        self.observing = False      # a membership subscription is confirmed active (acked, then verified)
        self.wake = asyncio.Event()
        self.dispatch_lock = asyncio.Lock()
        self.dispatched_at = 0.0
        self.generation = 0     # bumped by every dispatch and every turn-starting hook; see _flush
        self.busy_since = time.monotonic()      # meaningful only while the store's `busy` is set
        self.noticed: set[str] = set()          # held-queue notices already sent, see notify_held()
        self.dispatch_blocked = False           # the block has been audited; reset when it lifts
        self.bad_alerts: set[bytes] = set()     # alert files that could not even be marked; skipped
        self.retired: set[str] = set()          # sessions replaced by !new; their hooks are ignored
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep     # replaced in tests

    # --- state helpers -------------------------------------------------------------------------
    def latched(self) -> bool:
        return self.store.get("latched") is not None

    def latch(self, why: str) -> None:
        """`why` is built by admind from numbers and allowlisted change names, never from peer text."""
        if not self.latched():
            self.store.set("latched", why)
            self.audit.write("guard", action="latch", reason=why)

    def may_post(self) -> bool:
        """Outbound gate, checked immediately before every send: not latched, the operator has been
        seen in the group (D5), the last membership check succeeded and the membership subscription is
        confirmed active. A count check alone never counts: with nobody watching, a swap goes unseen."""
        return (not self.latched() and self.group_ok and self.observing
                and self.store.get("operator_seen_at") is not None)

    def authorised(self) -> bool:
        """May admind act for the operator right now: not latched, group verified, subscription live.
        Checked after every await that verifies and again immediately before a side effect."""
        return not self.latched() and self.group_ok and self.observing

    def deny(self, mid: str, what: str) -> None:
        """Refuse a message that was accepted before admind stopped being authorised. Fixed wording; no
        reply is queued (a latch is final, and an unverified group re-answers via UNVERIFIED)."""
        with self.store.transaction():
            self.store.set_inbound(mid, "dropped")
            if not self.latched():
                self.reply(mid, UNVERIFIED, "unverified")
        self.audit.write("drop", message_id=mid, reason="no longer authorised", what=what)

    def post(self, key: str, text: str, reply_to: str | None) -> None:
        if self.store.enqueue(key, text, reply_to):
            self.wake.set()

    def reply(self, mid: str, text: str, tag: str) -> None:
        for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
            self.post(f"{tag}:{mid}:{i}", part, mid)

    # --- lifecycle -----------------------------------------------------------------------------
    def recover(self) -> None:
        """Settle what a previous run left half done (D6). Idempotent."""
        # A turn in flight when admind stopped can't be tied to its Stop any more (it may have been
        # lost, or arrive later). Close it out; a late Stop then has no anchor and posts top-level.
        self.abandon_in_flight("admind restarted during this turn; a late reply may appear unthreaded")
        # `busy` is kept: an adopted session may still be mid-turn. Its Stop, a SessionStart (a launched
        # or resumed agent), or the operator's !interrupt clears it.
        # Received (held, never pasted) or executing (a command that may not have run to the end): each
        # is answered once, and the state change and its notice commit together.
        for status in ("received", "executing"):
            for mid in self.store.inbound_with_status(status):
                with self.store.transaction():
                    self.store.set_inbound(mid, "dropped")
                    self.post(f"restarted:{mid}", RESTARTED_NOTICE, mid)
                self.audit.write("recover", message_id=mid, action="answered-restarted")

    async def run(self) -> None:
        self.recover()
        await self.check_group()
        server = HookServer(self.s.state_dir / "hook.sock", self.hooks, self.audit)
        await server.start()
        try:
            await self.start_agent()
            async with asyncio.TaskGroup() as tg:
                for name, loop in (("inbound", self.inbound_loop), ("worker", self.worker_loop),
                                   ("hooks", self.hook_loop), ("outbox", self.outbox_loop),
                                   ("alerts", self.alerts_loop), ("group", self.group_loop)):
                    tg.create_task(supervised(name, loop, self.audit))
        finally:
            await server.close()

    async def start_agent(self) -> None:
        self.ready.clear()
        self.launched_at = time.monotonic()
        self.not_ready_sent = False
        try:
            mode = await asyncio.to_thread(self.agent.ensure_running)
        except Exception as exc:  # noqa: BLE001 - AgentStuck, tmux or filesystem failure; reported by type
            self.stuck = reason(exc)
            self.audit.write("agent", action="start-failed", error=type(exc).__name__)
            return
        self.stuck = None
        self.audit.write("agent", action=mode, session=self.agent.session_id)
        if mode == "adopted":
            self.ready.set()

    async def check_group(self) -> bool:
        try:
            info = await self.client.group_info(self.account, self.group)
        except ControlError as exc:
            self.group_ok = False
            self.audit.write("guard", action="group-check-failed", code=exc.code)   # never the peer's detail
            return False
        verdict = guard.judge_member_count(info.member_count)
        self.group_ok = verdict.action != "latch"
        if not self.group_ok:
            self.latch(verdict.reason)
            return False
        self.wake.set()
        return True

    # --- inbound -------------------------------------------------------------------------------
    async def inbound_loop(self) -> None:
        """Read the subscription and nothing else. Membership and admin events are acted on at once;
        operator messages are only queued for worker_loop, so a slow command can never keep the reader
        from seeing a membership change. A broken or ended stream means the group can no longer be
        watched: group_ok and observing drop, and nothing is sent until the subscription is back and the
        group re-verified (confirm_observing)."""
        delay = 1.0
        while True:
            self.observing = False
            try:
                async for event in self.client.subscribe(self.account, self.group,
                                                         on_ack=self.confirm_observing):
                    delay = 1.0
                    try:
                        self.on_event(event)
                    except Exception as exc:  # noqa: BLE001 - one bad event must not end the subscription
                        self.audit.write("handler", action="failed", error=type(exc).__name__)
                code = "stream-ended"
            except Exception as exc:  # noqa: BLE001 - ControlError, or anything the stream raised
                code = exc.code if isinstance(exc, ControlError) else type(exc).__name__
            self.observing = False
            self.group_ok = False
            self.audit.write("subscribe", action="reconnect", code=code)
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)

    async def confirm_observing(self) -> None:
        """Called once a subscription is acknowledged, before any event is read: resubscribe first, then
        re-verify the group, then (and only then) observe. `group_info` reports a count, not members, so
        a swap during an outage that leaves the count at two is not detected (ADR 0001 §3.4)."""
        if not await self.check_group():
            if self.latched():
                return
            raise ControlError("group could not be re-verified", "unverified", True)
        self.observing = True
        self.wake.set()

    def on_event(self, event: object) -> None:
        if isinstance(event, InboundMessage):
            self.work.put_nowait(event)
        elif isinstance(event, GroupStateChanged):
            verdict = guard.judge_group_change(event, group_id=self.group)
            if verdict.action == "latch":
                self.latch(verdict.reason)
            else:
                self.audit.write("event", action="ignored", what="group-change")
        elif isinstance(event, ReactionAdded):
            self.audit.write("event", action="ignored", what="reaction")
        else:
            self.audit.write("event", action="ignored", what="other")

    async def worker_loop(self) -> None:
        """Process operator messages one at a time, in arrival order, apart from the subscription reader."""
        while True:
            event = await self.work.get()
            try:
                await self.on_message(event)
            except Exception as exc:  # noqa: BLE001 - one bad message must not end the worker
                self.audit.write("handler", action="failed", error=type(exc).__name__)

    async def on_message(self, ev: InboundMessage) -> None:
        verdict = guard.judge_message(ev, group_id=self.group, operator_hex=self.s.operator_hex,
                                      latched=self.latched())
        mid = ev.message.message_id_hex.lower()
        if verdict.action == "ignore":
            return
        if verdict.action == "drop":
            # The sender is peer-supplied: a short prefix correlates without recording an identifier,
            # and the text, which a stranger controls, is not recorded at all.
            self.audit.write("drop", sender_prefix=ev.message.sender.account_id_hex[:8],
                             reason=verdict.reason, text_chars=len(ev.message.text))
            return
        if not MESSAGE_ID.fullmatch(mid):
            self.audit.write("drop", reason="malformed message id")
            return
        if not self.store.claim_inbound(mid):
            self.audit.write("drop", message_id=mid, reason="replayed message id")
            return
        text = ev.message.text
        self.audit.write("inbound", message_id=mid, text=own_text(text))
        if not await self.check_group() or not self.authorised():   # the latch may have come meanwhile
            self.deny(mid, "message")
            return
        if self.store.get("operator_seen_at") is None:
            self.store.set("operator_seen_at", now())
            self.post("ready", READY_NOTICE, None)
        await self.handle(mid, text)

    async def execute(self, cmd: commands.Command) -> tuple[str, bool]:
        """Run a command in a thread. A failure becomes fixed wording, never the exception's text."""
        try:
            return await asyncio.to_thread(self.runner.run, cmd), True
        except Exception as exc:  # noqa: BLE001 - AgentStuck, TmuxError or a bug; reported by type
            self.audit.write("command", command=cmd.name, action="failed", error=type(exc).__name__)
            return f"!{cmd.name} failed: {reason(exc)}", False

    def finish(self, mid: str, status: str, text: str, tag: str) -> None:
        """Settle an inbound message and queue its reply as one step: a crash leaves both or neither."""
        with self.store.transaction():
            self.store.set_inbound(mid, status)
            self.reply(mid, text, tag)

    async def handle(self, mid: str, text: str) -> None:
        # Control characters are refused before anything else: they can break out of bracketed paste,
        # and a command name or argument holding one must never be parsed or echoed (plan decision D3).
        if commands.has_control_chars(text):
            self.finish(mid, "dropped", CONTROL_REFUSED, "refused")
            return
        try:
            cmd = commands.parse(text)
        except commands.CommandError as exc:
            self.finish(mid, "done", show(str(exc), False), "cmd")   # may echo the operator's mistyped word
            return
        if cmd is not None:
            # Persisted before anything runs (or waits for the dispatch lock): a crash from here until
            # `done` is answered after the restart instead of being lost.
            self.store.set_inbound(mid, "executing")
            if cmd.name in ("interrupt", "new"):   # these recheck once they hold the dispatch lock
                await (self.interrupt(mid, cmd) if cmd.name == "interrupt" else self.new_session(mid, cmd))
                await self.flush()
                return
            if not self.authorised():
                self.deny(mid, "command")
                return
            result, _ = await self.execute(cmd)
            self.audit.write("command", message_id=mid, command=cmd.name,
                             arg=None if cmd.arg is None else own_text(cmd.arg), result_chars=len(result))
            self.finish(mid, "done", result, "cmd")
            await self.flush()
            return
        self.held.append((mid, text))
        await self.flush()

    async def interrupt(self, mid: str, cmd: commands.Command) -> None:
        """!interrupt holds the dispatch lock from before the Escape until the turn's state is settled, so
        a Stop arriving meanwhile can't dispatch a prompt that the Escape, or this cleanup, would hit.
        Only the turn in flight when the command arrived is abandoned, and only once the Escape was sent:
        a failed send is no evidence that the agent is idle, so the queue stays held."""
        async with self.dispatch_lock:
            if not self.authorised():       # the lock wait may have spanned a latch
                self.deny(mid, "command")
                return
            target = self.store.get("in_flight")
            period = self.store.get("busy")
            result, ok = await self.execute(cmd)
            if ok:
                # Claude Code does not run the Stop hook for a user interrupt, so the turn ends here.
                if target is not None and self.store.get("in_flight") == target:
                    self.abandon_in_flight("interrupted by !interrupt")
                # A prompt that started a turn while the Escape was pending opened a new busy period;
                # the Escape is no evidence that that turn ended.
                if self.store.get("busy") == period:
                    self.set_idle()
            else:
                result += ". Later messages are still held."
        self.audit.write("command", message_id=mid, command=cmd.name, result_chars=len(result))
        self.finish(mid, "done", result, "cmd")

    async def new_session(self, mid: str, cmd: commands.Command) -> None:
        """!new holds the dispatch lock while the old session is replaced, so nothing is pasted into it.
        The old session ID is retired first: its hooks (a late SessionStart included) are ignored from
        here on, whenever they arrive. The new session's SessionStart sets ready as usual."""
        async with self.dispatch_lock:
            if not self.authorised():
                self.deny(mid, "command")
                return
            old = self.agent.session_id
            if old is not None:
                self.retired.add(old)
            self.ready.clear()
            self.launched_at = time.monotonic()
            self.not_ready_sent = False
            self.stuck = None
            self.abandon_in_flight("the admin agent session was replaced by !new")
            self.set_idle()
            result, ok = await self.execute(cmd)
            if not ok:
                self.stuck = result.partition(": ")[2] or "internal error"
        self.audit.write("command", message_id=mid, command=cmd.name, result_chars=len(result))
        self.finish(mid, "done", result, "cmd")

    async def flush(self) -> None:
        async with self.dispatch_lock:      # one dispatcher at a time: flush() is called from several tasks
            await self._flush()

    async def _flush(self) -> None:
        # Same gate as the outbound side (D4): while latched, or while the group is not verified as
        # the operator and admind, nothing reaches the agent. Held prompts stay held, state untouched.
        if self.latched() or not self.group_ok or not self.observing:
            if self.held and not self.dispatch_blocked:
                self.dispatch_blocked = True
                self.audit.write("agent", action="dispatch-blocked", held=len(self.held),
                                 latched=self.latched())
            return
        self.dispatch_blocked = False
        if self.stuck is not None:
            for mid, _ in self.held:
                self.finish(mid, "dropped", f"Not delivered: the admin agent is not running ({self.stuck}). "
                                            "Use !tail, then !new.", "stuck")
            self.held.clear()
            return
        if not self.ready.is_set():
            waited = time.monotonic() - self.launched_at
            if self.held and waited > self.s.start_timeout_seconds and not self.not_ready_sent:
                self.not_ready_sent = True
                self.reply(self.held[-1][0], NOT_READY, "notready")
            return
        # One prompt at a time, and only into an idle agent (D9). The message is reserved as in_flight
        # before the paste, so nothing else can dispatch meanwhile; its reply anchor is set only by the
        # agent's UserPromptSubmit.
        if not self.held or self.store.get("in_flight") is not None or self.store.get("busy") is not None:
            return
        if not self.hooks.empty():
            return      # an acknowledged hook (maybe a new turn) is queued; hook_loop flushes after it
        mid, text = self.held.pop(0)
        with self.store.transaction():
            self.store.set("in_flight", mid)
            self.store.set("in_flight_text", text)
            self.store.set_inbound(mid, "dispatched")   # claimed before the paste: never replayed (D6)
            self.set_busy()
        self.dispatched_at = time.monotonic()
        self.generation += 1
        generation = self.generation        # a UserPromptSubmit or SessionStart meanwhile makes it stale
        try:
            await asyncio.to_thread(self.agent.send, text)
        except TmuxPasteUncertain as exc:
            self.paste_uncertain(mid, exc)
        except TmuxError as exc:
            # Failed before anything reached the pane: definitely not delivered, so the message is kept.
            # The busy state is cleared only if no newer turn started while the send was running.
            reserved = self.store.get("in_flight") == mid   # else a restart already answered it as abandoned
            with self.store.transaction():
                if reserved:
                    self.store.delete("in_flight")
                    self.store.delete("in_flight_text")
                    self.store.set_inbound(mid, "received")
                    if self.generation == generation:
                        self.set_idle()
            if reserved:
                self.held.insert(0, (mid, text))
            self.audit.write("agent", action="send-failed", error=type(exc).__name__, message_id=mid)
            await self.start_agent()
        except Exception as exc:  # noqa: BLE001 - anything else: delivery can't be ruled out
            self.paste_uncertain(mid, exc)
        else:
            self.audit.write("dispatch", message_id=mid, session=self.agent.session_id)

    def paste_uncertain(self, mid: str, exc: Exception) -> None:
        """The paste may have been submitted: never retry it (D6). The operator is told, in fixed words,
        to resend. The reservation and any anchor are cleared; the agent is still treated as busy,
        because nothing shows it idle (a Stop, a SessionStart, !interrupt or !new will release the queue)."""
        self.audit.write("agent", action="send-uncertain", error=type(exc).__name__, message_id=mid)
        self.abandon_in_flight(UNCERTAIN)

    def abandon_in_flight(self, why: str) -> None:
        with self.store.transaction():
            mid = self.store.get("in_flight")
            self.store.delete("anchor")
            self.store.delete("in_flight_text")
            if mid is not None:
                self.store.delete("in_flight")
                self.reply(mid, f"No reply to this message: {why}.", "abandoned")
        if mid is not None:
            self.audit.write("agent", action="abandon", message_id=mid, reason=why)

    def set_busy(self) -> None:
        """Start a busy period: at dispatch, and on every UserPromptSubmit (each prompt is a new turn, so
        LONG_TURN times and describes the current one, even after a lost Stop)."""
        turn = int(self.store.get("turn_seq") or "0") + 1
        self.store.set("turn_seq", str(turn))
        self.store.set("busy", str(turn))
        self.busy_since = time.monotonic()
        self.noticed.discard("long-turn")

    def set_idle(self) -> None:
        self.store.delete("busy")

    def notify_held(self) -> None:
        """Tell the operator, once each, that the queue is held (D9). Never dispatches: a lost hook
        looks exactly like a slow turn, so only a Stop, SessionStart, !interrupt or !new releases it."""
        mid = self.store.get("in_flight")
        if (mid is not None and self.store.get("anchor") is None and f"unconfirmed:{mid}" not in self.noticed
                and time.monotonic() - self.dispatched_at > self.s.start_timeout_seconds):
            self.noticed.add(f"unconfirmed:{mid}")
            self.reply(mid, UNCONFIRMED, "unconfirmed")
            self.audit.write("agent", action="unconfirmed", message_id=mid)
        if (self.store.get("busy") is not None and "long-turn" not in self.noticed
                and time.monotonic() - self.busy_since > self.s.turn_notice_seconds):
            self.noticed.add("long-turn")
            anchor = self.store.get("anchor")
            self.post(f"long-turn:{self.store.get('busy')}", LONG_TURN, anchor)
            self.audit.write("agent", action="long-turn", message_id=anchor)

    # --- hooks ---------------------------------------------------------------------------------
    async def hook_loop(self) -> None:
        while True:
            ev = await self.hooks.get()
            if ev.session_id != self.agent.session_id or ev.session_id in self.retired:
                # Neither the session ID nor the event name is recorded: any local process can send both.
                self.audit.write("hook", action="ignored-other-session")
            else:
                try:
                    await self.on_hook(ev)
                except Exception as exc:  # noqa: BLE001 - one bad event must not end the hook loop
                    self.audit.write("handler", action="hook-failed", error=type(exc).__name__)
            if self.hooks.empty():      # after every event, ignored ones too: _flush skipped while it queued
                await self.flush()

    async def on_hook(self, ev: HookEvent) -> None:
        """One event from the current session. hook_loop flushes after it once the queue is empty."""
        if ev.hook_event_name not in KNOWN_HOOKS:
            self.audit.write("hook", action="ignored-unknown-event")
        elif ev.hook_event_name == "SessionStart":
            self.generation += 1
            self.on_session_start(ev)
        elif ev.hook_event_name == "UserPromptSubmit":
            self.generation += 1
            self.set_busy()                     # a turn is running, whoever started it
            in_flight = self.store.get("in_flight")
            pasted = self.store.get("in_flight_text")
            if (in_flight is not None and pasted is not None and self.store.get("anchor") is None
                    and ev.prompt is not None and same_prompt(ev.prompt, pasted)):
                self.store.set("anchor", in_flight)
                self.audit.write("agent", action="prompt-submitted", message_id=in_flight)
            else:
                self.audit.write("agent", action="prompt-not-from-admind",
                                 prompt_chars=None if ev.prompt is None else len(ev.prompt))
                if self.store.get("anchor") is not None:
                    # The agent runs one turn at a time, so the anchored turn is over and its Stop
                    # was lost. Release the anchor before this turn's Stop can take it.
                    self.abandon_in_flight("admind did not see the admin agent finish it before "
                                           "another prompt started a turn")
        else:   # Stop
            await self.on_stop(ev)

    def on_session_start(self, ev: HookEvent) -> None:
        """Whether a SessionStart proves the agent idle depends on its `source` (D9). It is only trusted
        for the session admind has already seen start: `compact` is the same session shrinking its
        context, mid-turn or not, and says nothing about the turn; `startup` and `resume` of an idle
        session change nothing. Everything else (`clear`, an unknown or missing source, a busy session
        being started again, or any other session ID) is a restart: whatever was in flight is lost."""
        source = ev.source if ev.source in SESSION_SOURCES else "other"  # allowlisted: logged
        same = ev.session_id == self.store.get("session_started")
        if same and source == "compact":
            self.audit.write("agent", action="session-start", session=ev.session_id, source=source,
                             effect="none")
            return
        if same and source in ("startup", "resume") and self.store.get("busy") is None:
            self.agent.started(ev.session_id)
            self.audit.write("agent", action="session-start", session=ev.session_id, source=source,
                             effect="ready")
            self.ready.set()
            return
        with self.store.transaction():
            self.abandon_in_flight("the admin agent restarted before answering; resend if needed")
            self.set_idle()
        self.agent.started(ev.session_id)
        self.audit.write("agent", action="session-start", session=ev.session_id, source=source,
                         effect="restart")
        self.ready.set()

    def turn_identity(self, session_id: str) -> tuple[str, str | None, str | None, str | None]:
        """What a Stop must still find true when it applies its effects: the session it came from, the
        message in flight, its confirmed anchor, and the busy period."""
        return (session_id, self.store.get("in_flight"), self.store.get("anchor"), self.store.get("busy"))

    def session_current(self, session_id: str) -> bool:
        return session_id == self.agent.session_id and session_id not in self.retired

    async def on_stop(self, ev: HookEvent) -> None:
        # Captured before the first await: reading the reply yields, and !interrupt or !new can run
        # meanwhile (the worker is a separate task), replacing the turn this Stop belongs to.
        identity = self.turn_identity(ev.session_id)
        seq = int(self.store.get("reply_seq") or "0") + 1
        self.store.set("reply_seq", str(seq))
        raw = await asyncio.to_thread(reply_text, ev)    # may read the transcript file
        if not self.session_current(ev.session_id):
            self.audit.write("agent", action="stale-stop")   # a retired session: fixed wording only
            return
        text = raw if raw.strip() else NO_REPLY
        parts = chunk.split(text, self.s.chunk_chars)
        # Thread only to a prompt the agent confirmed receiving, and only if the turn this Stop was
        # captured for is still the current one. A Stop with no anchor (a turn that ended after
        # !interrupt, one begun at the terminal, or ours with a lost prompt hook) posts top-level, and
        # so does a late Stop whose turn a newer dispatch has replaced: its reply is real and goes out
        # unthreaded, and the newer turn's state is left exactly as it is.
        current = self.turn_identity(ev.session_id) == identity
        anchor = identity[2] if current else None
        with self.store.transaction():      # reply, cleared turn and idle state: all or nothing
            for i, part in enumerate(parts):
                self.post(f"reply:{ev.session_id}:{seq}:{i}", part, anchor)
            if current:
                if anchor is not None:
                    self.store.delete("anchor")
                    self.store.delete("in_flight")
                    self.store.delete("in_flight_text")
                self.set_idle()             # the turn ended; an unconfirmed in_flight still holds
        if not current:
            self.audit.write("agent", action="late-stop")   # fixed wording; nothing from the event
        # The agent's text goes to the operator's chat only; the audit log records its size.
        self.audit.write("reply", session=ev.session_id, reply_to=anchor, chars=len(text),
                         chunks=len(parts))

    # --- outbound ------------------------------------------------------------------------------
    async def outbox_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), 5)
            self.wake.clear()
            await self.outbox_pass()

    async def outbox_pass(self) -> None:
        """One sweep of the outbox (a loop iteration, callable on its own)."""
        if self.held:
            await self.flush()              # emits NOT_READY once the start timeout passes
        self.notify_held()
        for row in self.store.pending():
            if not self.may_post():         # rechecked per row: a latch mid-batch stops the rest
                break
            try:
                sent = await self.client.send_final(self.account, self.group, row.text, row.reply_to,
                                                    row.key)
            except ControlError as exc:
                attempts = self.store.mark_attempt(row.seq)
                if exc.retryable and attempts < MAX_SEND_ATTEMPTS:
                    self.audit.write("send", key=show(row.key, False), action="retry", attempts=attempts,
                                     code=exc.code)
                    await self._sleep(min(60, 2 ** attempts))
                    self.wake.set()
                    break
                self.store.mark_failed(row.seq)
                self.audit.write("send", key=show(row.key, False), action="failed", code=exc.code)
                continue
            self.store.mark_sent(row.seq, sent.message_ids_hex[0] if sent.message_ids_hex else None)
            self.audit.write("send", key=show(row.key, False), action="sent")

    async def alerts_loop(self) -> None:
        while True:
            await self._sleep(self.s.alert_poll_seconds)
            await self.relay_alerts()

    async def relay_alerts(self) -> None:
        """One sweep of the alert directory (a loop iteration, callable on its own). Each alert is
        handled on its own: a failure is audited by type and that alert is marked, never retried and
        never in the way of the alerts after it."""
        if not self.may_post():
            return
        for name, alert in await asyncio.to_thread(alerts.scan, self.s.alerts_dir):
            raw = os.fsencode(name)
            if raw in self.bad_alerts:
                continue
            try:
                self.relay_alert(name, raw, alert)
            except Exception as exc:  # noqa: BLE001 - one hostile file must not stop the others
                self.alert_failed(raw, exc)

    def relay_alert(self, name: str, raw: bytes, alert: alerts.Alert | None) -> None:
        if self.store.relayed(raw):
            return
        key = alerts.outbox_key(raw)
        text = alerts.render(name, alert, self.s.chunk_chars)
        if self.store.relay_alert(raw, key, text):
            self.audit.write("alert", name=show(alerts.display_name(name), False)[:64], key=show(key, False),
                             malformed=alert is None)
            self.wake.set()

    def alert_failed(self, raw: bytes, exc: Exception) -> None:
        """Report an alert admind could not relay: by exception type and opaque key only, and tell the
        operator with fixed wording. If even that fails the alert is skipped for this process."""
        key = alerts.outbox_key(raw)
        self.audit.write("alert", key=show(key, False), action="failed", error=type(exc).__name__)
        try:
            if self.store.relay_alert(raw, key, ALERT_FAILED.format(ref=key.partition(":")[2][:12])):
                self.wake.set()
        except Exception as inner:  # noqa: BLE001 - nothing more can be done for this file
            self.bad_alerts.add(raw)
            self.audit.write("alert", key=show(key, False), action="skipped", error=type(inner).__name__)

    async def group_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.group_check_seconds)
            await self.check_group()
