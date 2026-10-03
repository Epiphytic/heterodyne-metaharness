"""The admind daemon (ADR 0001 §8). See plan 2, Task 8, "Behaviour", for the contract the tests pin.

Output safety. Everything that leaves this module is one of:
- fixed wording, or a number;
- a message ID (an event hash, not an identity), audited for correlation;
- an operator's own text, audited whole after `redact`; every audit field is redacted centrally;
- the admin agent's reply, a summary, a batch, `!details` or the `!tail` screen, relayed to the operators in
  chat after `redact`, never written to the audit log.
Peer- and child-supplied text (a stranger's message, an agent reply, a terminal prompt, a peer's error
`detail`, a hook's session ID or event name) is never logged. Exceptions are reported by type only.
"""

import asyncio
import contextlib
import contextvars
import hmac
import json
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from heterodyne.admind import alerts, chunk, commands, ctl, guard, membership
from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HOOK_EVENTS, Delivery, HookEvent, HookServer, reply_text, same_prompt
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import AdmindSettings, Operator
from heterodyne.admind.store import Store, now
from heterodyne.config.secret_scan import show
from heterodyne.marmot.control import (
    ControlClient,
    ControlError,
    GroupStateChanged,
    InboundMessage,
    PeerError,
    ReactionAdded,
)
from heterodyne.tmux import TmuxError, TmuxPasteUncertain

READY_NOTICE = ("admind is listening. Your messages go to the admin agent verbatim; "
                "replies normally come back in thread. " + commands.HELP)
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
AGENT_POLL = 5.0        # seconds between checks that the admin agent's pane is still alive
READY_TIMEOUT = 120.0   # seconds after a launch with no SessionStart before the agent is relaunched
AGENT_STUCK_NOTICE = "The admin agent is not running ({why}). Use !tail, then !new."
MESSAGE_ID = re.compile(r"[0-9a-f]{64}")
KNOWN_HOOKS = frozenset(HOOK_EVENTS)
EXTRACT_SECONDS = 10.0      # the transcript fallback's deadline (a stalled filesystem must not hold a slot)
HOOK_DEADLINE = 30.0        # one hook event's whole processing; on expiry the slot is released
HOOK_LOCK_WAIT = 120.0      # an event's wait for the turn lock; expiry also holds dispatch (R6)
HOOK_LOST_NOTICE = ("admind lost an agent hook event; new messages are held. "
                    "When the agent is idle, send !interrupt (or !new) to resume.")
ADOPT_HOLD_NOTICE = ("admind restarted and adopted the running agent; new messages are held until the agent "
                     "finishes its current turn or you send !interrupt (or !new).")
SESSION_SOURCES = frozenset({"startup", "resume", "clear", "compact"})   # Claude Code's SessionStart sources


@dataclass
class HookRun:
    """The event hook_loop is processing: its deadline (rescheduled when the turn lock is taken, so only
    processing counts) and whether it was decided to be a no-op (stale, or outside the session)."""
    deadline: asyncio.Timeout | None = None
    noop: bool = False


_RUN: contextvars.ContextVar[HookRun | None] = contextvars.ContextVar("admind_hook_run", default=None)


def noop_event() -> None:
    """The event being processed was found stale or foreign: failing later must not hold dispatch."""
    run = _RUN.get()
    if run is not None:
        run.noop = True


def reason(exc: BaseException) -> str:
    """A reason fit for the chat and the audit log: never `str(exc)` of an OS, tmux or peer error."""
    if isinstance(exc, AgentStuck):
        return show(str(exc), False)    # admind's own wording
    if isinstance(exc, TmuxError):
        return "a tmux command failed"
    return "internal error"


def own_text(text: str) -> str:
    """The operator's own text for the audit log, whole (revision 13), redacted (B1; the audit redacts again,
    idempotently)."""
    return redact(text)


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
            crashed = type(exc).__name__
        else:
            crashed = None
        with contextlib.suppress(Exception):    # a broken log must not end the supervisor (and the TaskGroup)
            if crashed is None:
                audit.write("task", name=name, action="returned")
            else:
                audit.write("task", name=name, action="crashed", error=crashed)
        if clock() - started >= healthy:
            delay = base
        await sleep(delay)
        delay = min(delay * 2, cap)


def applied(confirmed: set[str], pending: membership.Pending) -> set[str]:
    """The confirmed keys once `pending` took effect."""
    return confirmed | {pending.member_hex} if pending.op == "add" else confirmed - {pending.member_hex}


def reconcile(count: int, confirmed: set[str] | None, pending_raw: str | None,
              policy: tuple[Operator, ...]) -> tuple[set[str] | None, str]:
    """Which operators rearm confirms for a trusted `count` (B21). Returns (keys, how), or (None, what
    admind had) when the count can't be reconciled. A pending change whose `to` count matches took effect;
    one whose `from` count matches did not. Then the confirmed keys stand if the count fits them; else,
    if the count fits every policy operator, the operator's rearm asserts they are all in the group."""
    keys: set[str] = set() if confirmed is None else set(confirmed)
    if pending_raw is not None:
        pending = membership.Pending.load(pending_raw)
        if count == pending.to_count:
            keys = applied(keys, pending)
    eligible = {o.hex for o in policy}
    keys &= eligible
    if confirmed is not None and count == 1 + len(keys):
        return keys, "confirmed"
    if count == 1 + len(eligible):
        return eligible, "policy"
    return None, f"{len(keys)}"


class Admind:
    def __init__(self, settings: AdmindSettings, client: ControlClient, store: Store, audit: Audit,
                 agent: AdminAgent, runner: commands.CommandRunner, account: str, group: str,
                 load_operators: Callable[[], tuple[Operator, ...]] | None = None) -> None:
        self.s = settings
        self.load_operators = load_operators or (lambda: settings.operators)
        self.policy_operators = settings.operators
        self.operators: dict[str, str] = {}     # set by run() from group_operators (B21)
        self.senders: dict[str, str] = {}       # message ID -> sender key, so queued work can be revalidated
        self.dispatching: str | None = None     # the prompt being pasted; it may return to `held`
        self.client = client
        self.store = store
        self.audit = audit
        self.agent = agent
        self.runner = runner
        self.account = account
        self.group = group
        self.hooks: asyncio.Queue[HookEvent | Delivery] = asyncio.Queue()
        self.work: asyncio.Queue[InboundMessage] = asyncio.Queue()    # operator messages for worker_loop
        self.ready = asyncio.Event()
        self.ready_nonce: str | None = None     # the nonce of the launch that set `ready`
        self.hook_server: HookServer | None = None
        # Hook arrival indices at or below it were accepted before a turn was released. In memory only:
        # a restart resets the indices, and startup recovery already fails closed.
        self.turn_floor = 0
        self.extract_timeout = EXTRACT_SECONDS      # replaced in tests
        self.hook_deadline = HOOK_DEADLINE          # replaced in tests
        self.hook_lock_wait = HOOK_LOCK_WAIT        # replaced in tests
        self._extraction: asyncio.Future[str] | None = None     # the one transcript-read thread allowed
        self._idle_tasks: set[asyncio.Task[None]] = set()
        self.held: list[tuple[str, str]] = []
        self.clock: Callable[[], float] = time.monotonic    # replaced in tests
        self.agent_poll = AGENT_POLL                        # replaced in tests
        self.ready_timeout = READY_TIMEOUT                  # replaced in tests
        self.launched_at = self.clock()
        self.stuck: str | None = None
        self.not_ready_sent = False
        self.group_ok = False
        self.observing = False      # a membership subscription is confirmed active (acked, then verified)
        self.acked = False          # the current subscription was acknowledged; check_group may then observe
        self.sub_gen = 0            # bumped at the start and end of every subscription attempt
        self.wake = asyncio.Event()
        self.send_lock = asyncio.Lock()     # held across each send; a membership transition drains it (B7)
        self.transition_lock = asyncio.Lock()   # one membership change or rearm at a time (B7)
        self.work_lock = asyncio.Lock()         # held by worker_loop for each operator message
        self.changing = False                   # a transition holds dispatch and posting (B7)
        self.membership_epoch = 0               # bumped when a transition or rearm begins; see check_group
        self.group_events = 0                   # membership events seen; rearm refuses if one comes meanwhile
        self.reading = False                    # the subscription's events are being read (B22)
        self._backoff: dict[int, asyncio.Future[None]] = {}     # each row waiting out a retry: its own timer
        self.dispatch_lock = asyncio.Lock()
        self.dispatched_at = 0.0
        self.generation = 0     # bumped by every dispatch and every turn-starting hook; see _flush
        self.busy_since = time.monotonic()      # meaningful only while the store's `busy` is set
        self.noticed: set[str] = set()          # held-queue notices already sent, see notify_held()
        self.block_audited = False              # the dispatch block has been audited; reset when it lifts
        self.dispatch_blocked = False           # a lost hook holds dispatch (in memory); see hold_now()
        self.shutting_down = False              # set synchronously when shutdown begins: nothing is pasted
        self.bad_alerts: set[bytes] = set()     # alert files that could not even be marked; skipped
        self.retired: set[str] = set()          # sessions replaced by !new; their hooks are ignored
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep     # replaced in tests

    # --- state helpers -------------------------------------------------------------------------
    def mark_ready(self, nonce: str | None = None) -> None:
        """The agent is ready, as established by the launch whose `nonce` the event carried (no nonce: an
        adopted pane, whose own nonce is the current one). An event from any other launch establishes
        nothing."""
        current = self.agent.launch_nonce
        if nonce is not None and nonce != current:
            self.audit.write("hook", action="ignored-stale-launch")
            return
        self.ready_nonce = current
        self.ready.set()

    # --- the hook gate and the turn floor ------------------------------------------------------
    def make_server(self, path: Path) -> HookServer:
        """The hook server, wired to this daemon: its accept count gates dispatch and its idle edge
        triggers a flush."""
        self.hook_server = HookServer(path, self.hooks, self.audit, self.hooks_idle, self.hold_dispatch,
                                      self.hold_now)
        return self.hook_server

    def pending_hooks(self) -> int:
        return 0 if self.hook_server is None else self.hook_server.pending_hooks

    def accepted_hooks(self) -> int:
        return 0 if self.hook_server is None else self.hook_server.accepted

    def raise_floor(self, upto: int | None = None) -> None:
        """Every hook accepted so far (or up to `upto`) predates the release of the turn just ended. Called
        under dispatch_lock, after the releasing action completed."""
        self.turn_floor = max(self.turn_floor, self.accepted_hooks() if upto is None else upto)

    def below_floor(self, arrival: int | None) -> bool:
        return arrival is not None and arrival <= self.turn_floor

    def hooks_idle(self) -> None:
        """The last accepted hook finished: held prompts may now be dispatched."""
        if self.shutting_down or not self.held:
            return
        task = asyncio.get_running_loop().create_task(self._idle_flush())
        self._idle_tasks.add(task)
        task.add_done_callback(self._idle_tasks.discard)

    async def _idle_flush(self) -> None:
        if self.shutting_down:
            return
        try:
            await self.flush()
        except Exception as exc:  # noqa: BLE001 - a failed flush is retried by the next outbox pass
            self.audit.write("handler", action="flush-failed", error=type(exc).__name__)

    def is_ready(self) -> bool:
        """`ready` counts only for the launch that set it: once the nonce changes (a replacement was
        started, or the departing launch was invalidated) the event is cleared and the agent is not
        ready until the new launch reports in."""
        if self.ready.is_set() and self.ready_nonce != self.agent.launch_nonce:
            self.ready.clear()
        return self.ready.is_set()

    def confirmed_operators(self) -> set[str] | None:
        """The keys admind has confirmed are in the group; None before the plan-2 migration."""
        raw = self.store.get("group_operators")
        return None if raw is None else {str(k) for k in json.loads(raw)}

    def authorise(self, confirmed: Iterable[str]) -> None:
        """Who may command admind: the policy operators whose key is confirmed in the group."""
        keys = set(confirmed)
        self.operators = {o.hex: o.name for o in self.policy_operators if o.hex in keys}

    def expected_members(self) -> int:
        """The trusted member count (B3). Absent only before the startup migration has run."""
        return int(self.store.get("expected_members") or "2")

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
        return (not self.latched() and self.group_ok and self.observing and not self.changing
                and self.store.get("operator_seen_at") is not None)

    def sender_current(self, mid: str) -> bool:
        """Is the sender of message `mid` still an authorised operator? A latch cleared by `rearm` may have
        left it out. Fails closed: a message with no recorded sender is not current."""
        sender = self.senders.get(mid)
        return sender is not None and sender in self.operators

    def remember_sender(self, mid: str, sender: str) -> None:
        """Record who sent `mid`. A record is dropped only once its message is past every queue: never
        while it is held, waiting for a lock, being pasted (it may return to `held` if the paste fails)."""
        if len(self.senders) > 256:
            live = {held_mid for held_mid, _ in self.held} | set(self.store.inbound_with_status("received"))
            live |= set(self.store.inbound_with_status("executing"))
            if self.dispatching is not None:
                live.add(self.dispatching)
            self.senders = {m: k for m, k in self.senders.items() if m in live}
        self.senders[mid] = sender

    def authorised(self, mid: str | None = None) -> bool:
        """May admind act for the operator right now: not latched, group verified, subscription live, and
        (given a message) its sender still an authorised operator. Checked after every await that verifies
        and again immediately before a side effect."""
        return (not self.latched() and self.group_ok and self.observing and not self.changing
                and (mid is None or self.sender_current(mid)))

    def deny(self, mid: str, what: str) -> None:
        """Refuse a message that was accepted before admind stopped being authorised. Fixed wording; no
        reply is queued (a latch is final, and an unverified group re-answers via UNVERIFIED)."""
        with self.store.transaction():
            self.store.set_inbound(mid, "dropped")
            if not self.latched():
                self.reply(mid, UNVERIFIED, "unverified")
        self.audit.write("drop", message_id=mid, reason="no longer authorised", what=what)

    def post(self, key: str, text: str, reply_to: str | None, lane: int = 1) -> None:
        """Queue one message. Everything admind posts is redacted here (B1); callers that chunk redact the
        whole text first, so a value can't escape redaction by straddling a chunk boundary."""
        if self.store.enqueue(key, redact(text), reply_to, lane):
            self.wake.set()

    def reply(self, mid: str, text: str, tag: str) -> None:
        for i, part in enumerate(chunk.split(redact(text), self.s.chunk_chars)):
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
                self.audit_quietly("recover", message_id=mid, action="answered-restarted")

    async def run(self) -> None:
        if self.store.get("expected_members") is None:     # a plan-2 group: always two members (B3)
            self.store.set("expected_members", "2")
            self.audit.write("guard", action="migrated", expected_members=2)
        if self.store.get("membership_pending") is not None:      # B15: a count can't tell which change
            self.latch("a membership change was interrupted; check the group's members in your client, "
                       "then run `admind rearm` on the host")
        confirmed: set[str] | None = self.confirmed_operators()
        if confirmed is None:          # a plan-2 database (B21)
            if len(self.policy_operators) == 1:     # plan 2 allowed exactly one operator, so it is that one
                confirmed = {self.policy_operators[0].hex}
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.audit.write("guard", action="migrated-operators", operators=1)
            else:
                confirmed = set()
                self.latch("admind does not know which operators are in the group (upgraded with several "
                           "operators in policy.toml); make policy.toml's operators match the group's, then "
                           "run `admind rearm` on the host")
        self.authorise(confirmed)
        rewritten = self.store.redact_pending_outbox(self.s.chunk_chars)   # B17: a plan-2 outbox
        if rewritten:
            self.audit.write("outbox", action="redacted-after-upgrade", rows=rewritten)
        self.recover()
        await self.check_group()
        server = self.make_server(self.s.state_dir / "hook.sock")
        control: ctl.CtlServer | None = None
        try:
            await server.start()
            control = ctl.CtlServer(self.s.state_dir / ctl.CTL_SOCKET, self.on_ctl, self.audit)
            await control.start()
            await self.start_agent(startup=True)
            async with asyncio.TaskGroup() as tg:
                for name, loop in (("inbound", self.inbound_loop), ("worker", self.worker_loop),
                                   ("hooks", self.hook_loop), ("outbox", self.outbox_loop),
                                   ("alerts", self.alerts_loop), ("group", self.group_loop),
                                   ("agent", self.agent_loop)):
                    tg.create_task(self.guarded(supervised(name, loop, self.audit)))
        except BaseException:
            self.shutting_down = True
            raise
        finally:
            self.shutting_down = True
            for timer in self._backoff.values():
                timer.cancel()
            self._backoff.clear()
            if control is not None:
                await control.close()
            await server.close()

    async def on_ctl(self, req: ctl.CtlRequest) -> ctl.CtlReply:
        """A host command. The reply's message names the operator, so it is redacted like any output."""
        if req.op == "rearm":
            result, message = await self.rearm()
        else:
            result, message = await self.change_membership(req.op, req.name or "")
        if result in ("committed", "rearmed"):
            try:
                await self.flush()      # prompts held during the transition may go now
            except Exception as exc:  # noqa: BLE001 - the change is settled; the worker flushes later
                self.audit_quietly("ctl", action="flush-failed", error=type(exc).__name__)
        return ctl.CtlReply(result, redact(message))

    async def guarded(self, coro: Awaitable[None]) -> None:
        """Run one of the daemon's loops; its cancellation (the TaskGroup tearing down on SIGTERM) sets
        `shutting_down` synchronously, before anything it unblocks can run."""
        try:
            await coro
        except asyncio.CancelledError:
            self.shutting_down = True
            raise

    async def start_agent(self, relaunch: bool = False, startup: bool = False) -> None:
        self.ready.clear()
        self.ready_nonce = None
        if relaunch or self.agent.replace_pending() is not None:
            # The departing launch stops validating now, before this coroutine yields: ensure_running
            # (in a thread) gives the replacement its own nonce before it starts the pane.
            self.agent.invalidate_launch()
        self.launched_at = self.clock()
        self.not_ready_sent = False
        try:
            mode = await asyncio.to_thread(self.agent.ensure_running, relaunch)
        except Exception as exc:  # noqa: BLE001 - AgentStuck, tmux or filesystem failure; reported by type
            self.stuck = reason(exc)
            self.audit.write("agent", action="start-failed", error=type(exc).__name__)
            return
        self.stuck = None
        self.audit_quietly("agent", action=mode, session=self.agent.session_id)
        if mode == "adopted":
            if startup:
                self.hold_adopted()
            self.mark_ready()
        else:
            self.is_ready()     # an event from a launch that has since been replaced is not readiness

    def hold_adopted(self) -> None:
        """Startup adopted a live pane (not a fresh launch). Whether it is mid-turn cannot be known: a
        terminal prompt may have been accepted by the hook server and lost with the previous process. So
        readiness (the pane is there) is separate from permission to paste: the same fail-closed hold as
        a lost hook is set, in memory first and then persisted, before any flush can run. Only a current
        Stop, a successful !interrupt or !new releases it."""
        self.hold_now()
        self.contained("hold-persist-failed", self.persist_hold)
        self.audit_quietly("agent", action="adopted-hold")
        self.contained("hold-notice-failed", self.notify_adopted)

    def notify_adopted(self) -> None:
        with self.store.transaction():
            self.post(f"adopt-hold:{self.next_seq('adopt_seq')}", ADOPT_HOLD_NOTICE, None)

    async def agent_loop(self) -> None:
        while True:
            await self._sleep(self.agent_poll)
            await self.check_agent()

    async def check_agent(self) -> None:
        """One supervision pass (D8). If the agent's pane has died, or a launch has gone `ready_timeout`
        without a SessionStart, whatever was in flight is abandoned as after a restart, the busy state is
        cleared and the agent is relaunched through ensure_running, so its crash-loop limit applies. Once
        that limit is hit the agent stays down until `!new` (only `!new` resets the count): this pass
        then does nothing, so a broken agent is never relaunched in a tight loop."""
        if self.stuck is not None:
            return
        async with self.dispatch_lock:      # !new and the dispatcher's own relaunch hold this lock
            if self.stuck is not None:
                return
            alive = await asyncio.to_thread(self.agent.alive)
            timed_out = not self.is_ready() and self.clock() - self.launched_at > self.ready_timeout
            if alive and not timed_out:
                return
            # The replacement intent is committed with the abandonment: a crash before the pane is
            # replaced must not let a restart adopt the old pane and dispatch into it.
            self.abandon_in_flight("the admin agent restarted before answering; resend if needed", idle=True,
                                   replace="died" if not alive else "ready-timeout")
            self.audit_quietly("agent", action="died" if not alive else "ready-timeout")
            await self.start_agent(relaunch=True)
            self.raise_floor()
            if self.stuck is None:
                return
            if self.held:
                await self._flush()         # the held messages are answered with the stuck notice
            else:
                self.post(f"agent-stuck:{self.next_seq('stuck_seq')}",
                          AGENT_STUCK_NOTICE.format(why=self.stuck), None)

    def next_seq(self, key: str) -> int:
        n = int(self.store.get(key) or "0") + 1
        self.store.set(key, str(n))
        return n

    async def check_group(self) -> bool:
        """Verify the member count. The result is bound to the subscription it was started on: a check
        begun on an acknowledged subscription may establish observation only if that same subscription
        is still the current one when the answer arrives. An answer from an earlier subscription (or one
        started before the acknowledgement) can still fail closed, latching on a bad count, but never
        establishes observation or group_ok, so a replacement is always verified by its own check."""
        generation = self.sub_gen
        acked_at_start = self.acked
        epoch = self.membership_epoch
        if self.changing:
            self.audit_quietly("guard", action="group-check-deferred")
            return False        # the transition reads the count itself and settles (B7)
        try:
            info = await self.client.group_info(self.account, self.group)
        except ControlError as exc:
            if self.changing or epoch != self.membership_epoch:
                self.audit_quietly("guard", action="group-check-deferred")
                return False
            self.group_ok = False
            self.audit.write("guard", action="group-check-failed", code=exc.code)   # never the peer's detail
            return False
        if self.changing or epoch != self.membership_epoch:
            # A transition or rearm began while this check waited: its count may be from either side of
            # the change, so it decides nothing; group_ok stays as it is.
            self.audit_quietly("guard", action="group-check-deferred")
            return False
        verdict = guard.judge_member_count(info.member_count, self.expected_members())
        if verdict.action == "latch":
            self.group_ok = False
            self.latch(verdict.reason)
            return False
        if generation != self.sub_gen:
            self.audit.write("guard", action="group-check-superseded")
            return False        # the subscription this check was made for is gone
        self.group_ok = True
        if acked_at_start and self.acked:   # a passing check after a rearm restores a live subscription
            self.observing = True
        self.wake.set()
        return True

    # --- membership (ADR 0001 §8, revision 13) -------------------------------------------------
    def membership_refusal(self) -> str | None:
        """Why a membership change can't start (or go on) now; None if it can."""
        if self.latched():
            return "admind is latched; check the group's members, then run `admind rearm` first."
        if self.store.get("membership_pending") is not None:
            return "a membership change is already pending; run `admind rearm`."
        if not (self.observing and self.group_ok):
            return ("admind is not watching the group yet (its membership subscription is not verified); "
                    "nothing changed. Try again shortly.")
        return None

    async def change_membership(self, op: str, name: str) -> tuple[str, str]:
        """`admind operators add|remove NAME` (ADR 0001 §8, revision 13; B7, B18). Returns (result, message);
        the message is admind's own wording plus the operator's policy name (the caller redacts it)."""
        async with self.transition_lock:
            refusal = self.membership_refusal()
            if refusal is not None:
                return "refused", refusal
            try:
                loaded = {o.name: o for o in self.load_operators()}
            except Exception as exc:  # noqa: BLE001 - ConfigError, or a policy file that can't be read
                self.audit.write("membership", action="refused", why="policy-unreadable",
                                 error=type(exc).__name__)
                return "refused", "policy.toml could not be read; nothing changed."
            if op == "add":
                target = loaded.get(name)
                if target is None:
                    return "refused", (f"{name} is not an operator with a marmot_npub in policy.toml; add it "
                                       "there first. Nothing changed.")
                if target.hex in self.operators:
                    return "refused", f"{name} is already an operator in the group; nothing changed."
                member_hex = target.hex
            else:
                found = [key for key, known in self.operators.items() if known == name]
                if not found:
                    return "refused", f"{name} is not an operator in the group; nothing changed."
                member_hex = found[0]
                eligible = {o.hex for o in loaded.values()}
                if not ({key for key in self.operators if key != member_hex} & eligible):
                    return "refused", ("the last operator can't be removed: no one else in the group is "
                                       "an operator in policy.toml. Nothing changed.")
            policy = tuple(loaded.values())
            # Step 1: hold. The message in hand finishes first; then nothing new is dispatched or sent, and
            # a paste or a send already under way is waited for, before the count is read.
            async with self.work_lock:
                self.changing = True
                self.membership_epoch += 1
                journaled = settled = False
                try:
                    async with self.dispatch_lock:
                        pass
                    async with self.send_lock:
                        pass
                    generation = self.sub_gen
                    refusal = self.membership_refusal()     # a latch or a lost subscription meanwhile
                    if refusal is not None:
                        settled = True
                        return "refused", refusal
                    try:
                        count = (await self.client.group_info(self.account, self.group)).member_count
                    except ControlError as exc:
                        settled = True
                        return "refused", (f"could not read the group's member count ({exc.code}); "
                                           "nothing changed.")
                    refusal = self.membership_refusal() or (
                        None if generation == self.sub_gen else
                        "the membership subscription was replaced; nothing changed. Try again.")
                    if refusal is not None:
                        settled = True
                        return "refused", refusal
                    expected = self.expected_members()
                    if count != expected:
                        self.latch(f"group has {count} members, expected {expected}")
                        settled = True
                        return "latched", (f"the group has {count} members, not the expected {expected}; "
                                           "latched.")
                    to = expected + 1 if op == "add" else expected - 1
                    if to < 2:
                        settled = True
                        return "refused", "the last operator can't be removed."
                    pending = membership.Pending("add" if op == "add" else "remove", name, member_hex,
                                                 expected, to, now(), uuid.uuid4().hex)
                    journaled = True        # from here an exception latches (B18)
                    self.store.set("membership_pending", pending.dump())
                    self.audit.write("membership", action="pending", op=pending.op, operator=name,
                                     from_count=expected, to_count=to)
                    reported = await self.member_call(pending)
                    try:
                        info = await self.client.group_info(self.account, self.group)
                        after: int | None = info.member_count
                    except ControlError:
                        after = None
                    outcome = membership.settle(reported, after, pending)
                    if self.latched() or not self.observing or generation != self.sub_gen:
                        outcome = "latch"   # an event, a latch or a lost subscription during it (step 3, B5)
                    result = self.settle_membership(pending, outcome, reported, after, policy)
                    settled = True
                    return result
                except BaseException:
                    if journaled and not settled:
                        with contextlib.suppress(Exception):
                            self.latch("a membership change failed before it was settled; check the group's "
                                       "members in your client, then run `admind rearm` on the host")
                    raise
                finally:
                    if settled or not journaled or self.latched():
                        self.changing = False
                    else:   # pending, unsettled and not latched: hold until rearm or the restart latch (B15)
                        self.audit_quietly("membership", action="held-unsettled")
                    self.wake.set()

    async def member_call(self, pending: membership.Pending) -> membership.Reported:
        try:
            if pending.op == "add":
                await self.client.group_member_add(self.account, self.group, [pending.member_hex])
            else:
                await self.client.group_member_remove(self.account, self.group, [pending.member_hex])
        except PeerError as exc:
            self.audit.write("membership", action="refused-by-wn-agent", code=exc.code)
            return "failed"
        except ControlError as exc:
            self.audit.write("membership", action="no-answer", code=exc.code)
            return "unknown"
        return "ok"

    def settle_membership(self, pending: membership.Pending, outcome: membership.Outcome, reported: str,
                          after: int | None, policy: tuple[Operator, ...]) -> tuple[str, str]:
        """Apply the outcome. A commit writes the count, the confirmed operators (B21) and the notice and
        clears the record in one transaction, then authorises from them (B2)."""
        if outcome == "commit":
            verb = "added to" if pending.op == "add" else "removed from"
            confirmed = applied(self.confirmed_operators() or set(), pending)
            with self.store.transaction():
                self.store.set("expected_members", str(pending.to_count))
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.store.delete("membership_pending")
                # The key holds the change's own ID, not its start time: two changes in one second differ.
                self.post(f"membership:{pending.change_id}",
                          f"Operator {pending.name} was {verb} the group.", None)
            self.policy_operators = policy
            self.authorise(confirmed)
            self.audit_quietly("membership", action="committed", op=pending.op, operator=pending.name,
                               member_count=pending.to_count)
            return "committed", f"Operator {pending.name} was {verb} the group ({pending.to_count} members)."
        if outcome == "abort":
            self.store.delete("membership_pending")
            self.audit_quietly("membership", action="aborted", op=pending.op, operator=pending.name)
            return "aborted", ("wn-agent refused the change and the member count is unchanged; "
                               "nothing changed.")
        self.latch(f"membership change {pending.op} ended unconfirmed (reported {reported}, "
                   f"count {'unknown' if after is None else after}, expected {pending.to_count})")
        return "latched", ("the change could not be confirmed, so admind latched. Check the group's members "
                           "in your client, then run `admind rearm` on the host.")

    async def rearm(self) -> tuple[str, str]:
        """`admind rearm`: trust the current member count and clear the latch, any pending change and a
        held transition (§8: "takes the current member count as trusted"). It also reconciles which
        operators are confirmed in the group (B21), and refuses, changing nothing, if the count can't be
        reconciled with them or if the group changed while the count was read (B22)."""
        async with self.transition_lock:
            if not self.reading:
                # Acknowledged is not enough: until confirm_observing returns, events wait unread in the
                # stream, and a resubscription would drop them (Codex r3 finding 3).
                return "refused", ("admind is not reading the group's events right now, so a change during "
                                   "rearm could be missed; nothing changed. Try again shortly.")
            try:
                policy = self.load_operators()
            except Exception as exc:  # noqa: BLE001 - ConfigError, or a policy file that can't be read
                self.audit.write("guard", action="rearm-refused", why="policy-unreadable",
                                 error=type(exc).__name__)
                return "refused", "policy.toml could not be read; nothing changed."
            generation, events, latched_before = self.sub_gen, self.group_events, self.store.get("latched")
            try:
                count = (await self.client.group_info(self.account, self.group)).member_count
            except ControlError as exc:
                return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
            if (not self.reading or generation != self.sub_gen or events != self.group_events
                    or self.store.get("latched") != latched_before):
                self.audit_quietly("guard", action="rearm-refused", why="changed-meanwhile")
                return "refused", ("the group changed while rearm read it (a membership event, a new "
                                   "latch or a resubscription); nothing changed. Check the group's "
                                   "members, then run `admind rearm` again.")
            if count < 2:
                return "refused", f"the group has {count} member(s) and no operator; nothing changed."
            confirmed, how = reconcile(count, self.confirmed_operators(),
                                       self.store.get("membership_pending"), policy)
            if confirmed is None:
                self.audit_quietly("guard", action="rearm-refused", why="operators-unknown",
                                   member_count=count, policy_operators=len(policy))
                return "refused", (f"the group has {count} members but policy.toml lists {len(policy)} "
                                   f"operator(s) and admind has confirmed {how}; make policy.toml's "
                                   "operators match the group's members, then run `admind rearm` again. "
                                   "Nothing changed.")
            previous = self.store.get("latched")
            with self.store.transaction():
                self.store.set("expected_members", str(count))
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.store.delete("membership_pending")
                self.store.delete("latched")
            self.policy_operators = policy
            self.authorise(confirmed)
            self.changing = False           # a transition held unsettled (B18) ends here
            self.membership_epoch += 1      # a check begun before this one judged against the old count
            self.audit_quietly("guard", action="rearm", member_count=count, previous=previous, operators=how)
        await self.check_group()
        self.wake.set()
        return "rearmed", f"Cleared the latch; the trusted member count is now {count}."

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
            self.acked = False
            self.reading = False
            self.sub_gen += 1
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
            self.acked = False
            self.reading = False
            self.sub_gen += 1
            self.group_ok = False
            self.audit.write("subscribe", action="reconnect", code=code)
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)

    async def confirm_observing(self) -> None:
        """Called once a subscription is acknowledged, before any event is read: resubscribe first, then
        re-verify the group, then (and only then) observe. `group_info` reports a count, not members, so
        a swap during an outage that leaves the count at two is not detected (ADR 0001 §3.4)."""
        self.acked = True       # check_group observes on success, here and on every later passing check
        if not await self.check_group() and not self.latched():
            raise ControlError("group could not be re-verified", "unverified", True)
        self.reading = True     # acknowledged but not observing if latched; a rearm then restores it

    def on_event(self, event: object) -> None:
        if isinstance(event, InboundMessage):
            self.work.put_nowait(event)
        elif isinstance(event, GroupStateChanged):
            self.group_events += 1
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
        """Process operator messages one at a time, in arrival order, apart from the subscription reader.
        Each holds `work_lock`: a membership transition waits for the message in hand, and the next one
        waits for the transition (B7), so a message is never denied just because a transition ran."""
        while True:
            event = await self.work.get()
            try:
                async with self.work_lock:
                    await self.on_message(event)
            except Exception as exc:  # noqa: BLE001 - one bad message must not end the worker
                self.audit.write("handler", action="failed", error=type(exc).__name__)

    async def on_message(self, ev: InboundMessage) -> None:
        verdict = guard.judge_message(ev, group_id=self.group, operators=self.operators,
                                      latched=self.latched())
        mid = ev.message.message_id_hex.lower()
        if verdict.action == "ignore":
            return
        if verdict.action == "drop":
            if verdict.operator is not None:    # an operator, while latched: logged in full, never acted on
                self.audit.write("drop", operator=verdict.operator, message_id=mid, reason=verdict.reason,
                                 text=own_text(ev.message.text))
                return
            # The sender is peer-supplied: a short prefix correlates without recording an identifier,
            # and the text, which a stranger controls, is not recorded at all.
            self.audit.write("drop", sender_prefix=ev.message.sender.account_id_hex[:8],
                             reason=verdict.reason, text_chars=len(ev.message.text))
            return
        if not MESSAGE_ID.fullmatch(mid):
            self.audit.write("drop", operator=verdict.operator, reason="malformed message id",
                             text=own_text(ev.message.text))
            return
        if not self.store.claim_inbound(mid):
            self.audit.write("drop", operator=verdict.operator, message_id=mid, reason="replayed message id",
                             text=own_text(ev.message.text))
            return
        self.remember_sender(mid, ev.message.sender.account_id_hex.lower())
        text = ev.message.text
        self.audit.write("inbound", operator=verdict.operator, message_id=mid, text=own_text(text))
        if not await self.check_group() or not self.authorised(mid):   # the latch may have come meanwhile
            self.deny(mid, "message")
            return
        if self.store.get("operator_seen_at") is None:
            with self.store.transaction():      # the marker and the notice: both or neither
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
            if not self.authorised(mid):
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
            if not self.authorised(mid):    # the lock wait may have spanned a latch, or a revoked operator
                self.deny(mid, "command")
                return
            target = self.store.get("in_flight")
            anchor = self.store.get("anchor")
            period = self.store.get("busy")
            result, ok = await self.execute(cmd)
            if ok:
                self.raise_floor()      # a prompt accepted before this point belongs to the released turn
                # Claude Code does not run the Stop hook for a user interrupt, so the turn ends here.
                if target is not None and self.store.get("in_flight") == target:
                    self.abandon_in_flight("interrupted by !interrupt")
                elif target is None and anchor is not None and self.store.get("anchor") == anchor:
                    self.store.delete("anchor")     # no reservation owns it any more
                # A prompt that started a turn while the Escape was pending opened a new busy period;
                # the Escape is no evidence that that turn ended.
                if self.store.get("busy") == period:
                    self.set_idle()
            else:
                result += ". Later messages are still held."
        self.audit_quietly("command", message_id=mid, command=cmd.name, result_chars=len(result))
        self.finish(mid, "done", result, "cmd")

    async def new_session(self, mid: str, cmd: commands.Command) -> None:
        """!new holds the dispatch lock while the old session is replaced, so nothing is pasted into it.
        The old session ID is retired first: its hooks (a late SessionStart included) are ignored from
        here on, whenever they arrive. The new session's SessionStart sets ready as usual."""
        async with self.dispatch_lock:
            if not self.authorised(mid):
                self.deny(mid, "command")
                return
            old = self.agent.session_id
            if old is not None:
                self.retired.add(old)
            self.ready.clear()
            self.ready_nonce = None
            self.agent.invalidate_launch()
            self.launched_at = self.clock()
            self.not_ready_sent = False
            self.stuck = None
            self.abandon_in_flight("the admin agent session was replaced by !new", idle=True, replace="new")
            result, ok = await self.execute(cmd)
            self.raise_floor()
            if not ok:
                self.stuck = result.partition(": ")[2] or "internal error"
        self.audit_quietly("command", message_id=mid, command=cmd.name, result_chars=len(result))
        self.finish(mid, "done", result, "cmd")

    async def flush(self) -> None:
        async with self.dispatch_lock:      # one dispatcher at a time: flush() is called from several tasks
            await self._flush()

    async def _flush(self) -> None:
        if self.shutting_down:
            return      # under dispatch_lock, with the other gates: nothing is pasted once shutdown began
        # Same gate as the outbound side (D4): while latched, or while the group is not verified as
        # the operator and admind, nothing reaches the agent. Held prompts stay held, state untouched.
        if self.latched() or not self.group_ok or not self.observing or self.changing:
            if self.held and not self.block_audited:
                self.block_audited = True
                self.audit.write("agent", action="dispatch-blocked", held=len(self.held),
                                 latched=self.latched())
            return
        self.block_audited = False
        while self.held and not self.sender_current(self.held[0][0]):   # revoked while it waited
            self.deny(self.held.pop(0)[0], "held prompt")
        if self.stuck is not None:
            for mid, _ in self.held:
                self.finish(mid, "dropped", f"Not delivered: the admin agent is not running ({self.stuck}). "
                                            "Use !tail, then !new.", "stuck")
            self.held.clear()
            return
        if not self.is_ready():
            waited = self.clock() - self.launched_at
            if self.held and waited > self.s.start_timeout_seconds and not self.not_ready_sent:
                self.not_ready_sent = True
                self.reply(self.held[-1][0], NOT_READY, "notready")
            return
        # One prompt at a time, and only into an idle agent (D9). The message is reserved as in_flight
        # before the paste, so nothing else can dispatch meanwhile; its reply anchor is set only by the
        # agent's UserPromptSubmit.
        if not self.held or self.store.get("in_flight") is not None or self.store.get("busy") is not None:
            return
        if self.dispatch_blocked:
            return      # a lost hook event holds dispatch, whatever the store says (see hold_now)
        if self.pending_hooks() > 0 or not self.hooks.empty():
            return      # an accepted hook (maybe a new turn) is not applied yet; the idle edge flushes
        if self.hook_server is not None and self.hook_server.backlog_waiting():
            return      # a connection is waiting to be accepted (maybe a new turn); its idle edge flushes
        mid, text = self.held.pop(0)
        with self.store.transaction():
            self.store.set("in_flight", mid)
            self.store.set("in_flight_text", text)
            self.store.set_inbound(mid, "dispatched")   # claimed before the paste: never replayed (D6)
            self.set_busy()
        self.dispatched_at = time.monotonic()
        self.generation += 1
        generation = self.generation        # a UserPromptSubmit or SessionStart meanwhile makes it stale
        self.dispatching = mid
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
                    if self.store.get("anchor") == mid:
                        # A terminal prompt with identical text anchored the reservation while the
                        # send was pending; the message was not delivered, so its retry must start clean.
                        self.store.delete("anchor")
                    self.store.set_inbound(mid, "received")
                    if self.generation == generation:
                        self.set_idle()
            if reserved:
                self.held.insert(0, (mid, text))
            self.audit_quietly("agent", action="send-failed", error=type(exc).__name__, message_id=mid)
            await self.start_agent()
        except Exception as exc:  # noqa: BLE001 - anything else: delivery can't be ruled out
            self.paste_uncertain(mid, exc)
        else:
            self.audit.write("dispatch", message_id=mid, session=self.agent.session_id)
        finally:
            self.dispatching = None

    def paste_uncertain(self, mid: str, exc: Exception) -> None:
        """The paste may have been submitted: never retry it (D6). The operator is told, in fixed words,
        to resend. The reservation and any anchor are cleared; the agent is still treated as busy,
        because nothing shows it idle (a Stop, a SessionStart, !interrupt or !new will release the queue)."""
        try:
            self.abandon_in_flight(UNCERTAIN)
        finally:
            self.audit_quietly("agent", action="send-uncertain", error=type(exc).__name__, message_id=mid)

    def abandon_in_flight(self, why: str, floor: int | None = None, idle: bool = False,
                          replace: str | None = None) -> None:
        """Release the reservation and its anchor. Hooks accepted so far (a hook releasing the turn passes
        its own index instead: what was accepted behind it is newer) can no longer start or end it.
        Three steps, in this order. (a) One transaction holds only the safety and state change: the anchor
        and reservation cleared, and the idle state if `idle`. It commits on its own, so call this outside
        any transaction a later fallible step could roll back. (b) The operator's notice is queued in its
        own transaction; a failure there is contained and undoes nothing from (a). (c) A quiet audit.
        `replace` (a fixed word) is the caller's intent to replace the pane: it is stored in (a), so no
        restart can see the turn idle without also seeing that the old pane must not be adopted."""
        self.raise_floor(floor)
        with self.store.transaction():
            mid = self.store.get("in_flight")
            self.store.delete("anchor")
            self.store.delete("in_flight_text")
            self.store.delete("stopped_flight")
            if mid is not None:
                self.store.delete("in_flight")
            if replace is not None:
                self.store.set("replace_pending", replace)
            if idle:
                self.set_idle()
        if mid is None:
            return
        self.contained("abandon-notice-failed", lambda: self.notify_abandoned(mid, why))
        self.audit_quietly("agent", action="abandon", message_id=mid, reason=why)

    def notify_abandoned(self, mid: str, why: str) -> None:
        with self.store.transaction():
            self.reply(mid, f"No reply to this message: {why}.", "abandoned")

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
        self.store.delete("hook_lost")      # a release ends the hook-lost episode
        self.dispatch_blocked = False       # ... and lifts the in-memory hold with it

    def audit_quietly(self, kind: str, **fields: object) -> None:
        """An audit record on a path whose safeguard is already established: a failure to log must not
        undo, skip or delay anything after it."""
        with contextlib.suppress(Exception):    # the log is what failed; there is nowhere left to report it
            self.audit.write(kind, **fields)

    def hold_now(self) -> None:
        """The first action on every lost-hook path, before any audit, store write or notice: dispatch is
        refused from this instant. Synchronous, no I/O and cannot fail. `generation` advances so that a
        send failing meanwhile cannot clear the busy state. Lifted only by `set_idle` (a current Stop,
        !interrupt, !new, a relaunch)."""
        self.dispatch_blocked = True
        self.generation += 1

    async def hold_dispatch(self) -> None:
        """A hook event that might have started a turn was lost (deadline, failure, dropped frame): hold
        dispatch until a Stop, !interrupt or !new releases it. The in-memory hold is set first and cannot
        fail; persisting busy, the audit record and the operator notice are separate, best-effort steps,
        so a failure of one changes nothing already established. Taken under the dispatch lock; if the
        lock cannot be had within `hook_lock_wait` the hold is applied anyway."""
        self.hold_now()
        locked = False
        try:
            await asyncio.wait_for(self.dispatch_lock.acquire(), self.hook_lock_wait)
            locked = True
        except TimeoutError:
            pass
        try:
            self.hold_now()     # again: a release while this waited for the lock must not undo the hold
            self.contained("hold-persist-failed", self.persist_hold)
            self.audit_quietly("hook", action="hook-lost-hold")
            self.contained("hold-notice-failed", self.notify_hook_lost)
        finally:
            if locked:
                self.dispatch_lock.release()

    def contained(self, what: str, step: Callable[[], None]) -> None:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - a failed step must not undo the hold; by type only
            self.audit_quietly("handler", action=what, error=type(exc).__name__)

    def persist_hold(self) -> None:
        with self.store.transaction():
            if self.store.get("busy") is None:
                self.set_busy()
            self.generation += 1

    def notify_hook_lost(self) -> None:
        """One fixed notice per hold episode, in its own transaction: the episode flag is set only if the
        notice was queued, so a notice that failed is retried by the next lost event."""
        if self.store.get("hook_lost") is not None:
            return
        busy = self.store.get("busy")
        key = f"hook-lost:{busy}" if busy is not None else f"hook-lost:g{self.generation}"
        with self.store.transaction():
            self.store.set("hook_lost", "1")
            self.post(key, HOOK_LOST_NOTICE, None)

    @contextlib.asynccontextmanager
    async def turn_lock(self) -> AsyncGenerator[None]:
        """dispatch_lock for hook processing. Under hook_loop the event's deadline covers only the time the
        lock is held: waiting for it (behind a dispatch, bounded by tmux's own timeouts) has its own,
        longer bound."""
        run = _RUN.get()
        if run is None or run.deadline is None:
            async with self.dispatch_lock:
                yield
            return
        loop = asyncio.get_running_loop()
        run.deadline.reschedule(loop.time() + self.hook_lock_wait)
        await self.dispatch_lock.acquire()
        run.deadline.reschedule(loop.time() + self.hook_deadline)
        try:
            yield
        finally:
            self.dispatch_lock.release()

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
        """Process hook events one at a time, in the order the hook server queued them. The hook's
        connection is answered only after its event was processed, so Claude Code (which waits for its
        synchronous hooks) never runs ahead of admind's view of the turn. Each event has an overall
        deadline: a stuck one is cancelled and its slot released, so the sequencer cannot stay blocked."""
        while True:
            item = await self.hooks.get()
            ev, done, arrival = ((item.event, item.done, item.arrival) if isinstance(item, Delivery)
                                 else (item, None, None))
            ok = False
            run = HookRun()
            token = _RUN.set(run)
            try:
                failure: tuple[str, dict[str, object]] | None = None
                try:
                    async with asyncio.timeout(self.hook_deadline) as deadline:
                        run.deadline = deadline
                        await self.route_hook(ev, arrival)
                    ok = True
                except asyncio.CancelledError:
                    # Shutdown while this event was being processed: it may have started a turn. The
                    # hold is applied synchronously, before the delivery is resolved (the finally).
                    self.shutting_down = True
                    if not run.noop:
                        self.hold_now()
                        self.contained("hold-persist-failed", self.persist_hold)
                    raise
                except TimeoutError:
                    failure = ("hook", {"action": "hook-deadline"})     # fixed wording; the event is dropped
                except Exception as exc:  # noqa: BLE001 - one bad event must not end the hook loop
                    failure = ("handler", {"action": "hook-failed", "error": type(exc).__name__})
                if failure is not None:
                    # The event might have started a turn: dispatch stays held until idle evidence. The
                    # hold comes first; neither the audit nor the persisting can skip it.
                    holding = not run.noop
                    if holding:
                        self.hold_now()
                    self.audit_quietly(failure[0], **failure[1])
                    if holding:
                        try:
                            await self.hold_dispatch()
                        except Exception as exc:  # noqa: BLE001
                            self.audit_quietly("handler", action="hold-failed", error=type(exc).__name__)
            finally:
                _RUN.reset(token)
                if done is not None and not done.done():
                    done.set_result(ok)
            if self.pending_hooks() == 0 and self.hooks.empty():
                await self.flush()      # (with a wired server the idle edge flushes once it counts zero)

    def classify(self, ev: HookEvent) -> str:
        """`other` (not the current session), `stale` (not the current launch: missing or malformed nonces
        fail closed) or `current`. The answer is only good while the dispatch lock is held."""
        if not self.session_current(ev.session_id):
            return "other"
        nonce = self.agent.launch_nonce
        if nonce is None or ev.launch is None or not (nonce.isascii() and ev.launch.isascii()):
            return "stale"
        return "current" if hmac.compare_digest(ev.launch.encode(), nonce.encode()) else "stale"

    async def route_hook(self, ev: HookEvent, arrival: int | None = None) -> None:
        # Only a cheap early drop here: the session and launch are decided again under the lock, in the
        # same critical section that applies the event's effects, because the lock wait can span a relaunch.
        if self.classify(ev) == "other":
            # Neither the session ID nor the event name is recorded: any local process can send both.
            noop_event()
            self.audit.write("hook", action="ignored-other-session")
            return
        await self.on_hook(ev, arrival, validate=True)

    async def on_hook(self, ev: HookEvent, arrival: int | None = None, validate: bool = False) -> None:
        """One event from the current session, applied under the turn-state lock (never mid-dispatch,
        never mid-!interrupt). `validate` (the routed path) decides session and launch under that lock;
        a stale launch never changes turn, busy, anchor or reservation state, and only a stale Stop's
        own text is posted. `arrival` is the connection's accept-order index (None: not from the server).
        hook_loop flushes after it once nothing else is pending."""
        if ev.hook_event_name not in KNOWN_HOOKS:
            noop_event()
            self.audit.write("hook", action="ignored-unknown-event")
        elif ev.hook_event_name == "Stop":
            await self.on_stop(ev, arrival, validate=validate)
        else:
            async with self.turn_lock():
                kind = self.classify(ev) if validate else "current"
                if kind == "other":
                    noop_event()
                    self.audit.write("hook", action="ignored-other-session")
                elif kind == "stale":
                    noop_event()
                    self.audit.write("hook", action="ignored-stale-launch")
                elif ev.hook_event_name == "SessionStart":
                    self.generation += 1
                    self.on_session_start(ev, arrival)
                else:
                    self.on_prompt(ev, arrival)

    def on_prompt(self, ev: HookEvent, arrival: int | None = None) -> None:
        if self.below_floor(arrival):
            noop_event()
            # Accepted before the turn it would start or anchor was released (!interrupt, a relaunch, !new,
            # an abandon, or the Stop that ended it): it must not set busy, anchor or touch a reservation.
            self.audit.write("agent", action="stale-prompt")
            return
        self.generation += 1
        in_flight = self.store.get("in_flight")
        if in_flight is not None and self.store.get("stopped_flight") == in_flight:
            # The reservation's turn already ended (its Stop was processed first): this prompt is late.
            # It must not anchor the reservation or start a busy period for a turn that is over.
            self.audit.write("agent", action="ignored-late-prompt", message_id=in_flight)
            return
        anchored = False
        with self.store.transaction():      # the busy period and the anchor: one step
            self.set_busy()                 # a turn is running, whoever started it
            pasted = self.store.get("in_flight_text")
            if (in_flight is not None and pasted is not None and self.store.get("anchor") is None
                    and ev.prompt is not None and same_prompt(ev.prompt, pasted)):
                self.store.set("anchor", in_flight)
                anchored = True
        # Audits and the abandon's notice come after the busy period is committed: a failure of either
        # must not roll back the busy state.
        if anchored:
            self.audit_quietly("agent", action="prompt-submitted", message_id=in_flight)
            return
        self.audit_quietly("agent", action="prompt-not-from-admind",
                           prompt_chars=None if ev.prompt is None else len(ev.prompt))
        if self.store.get("anchor") is not None:
            # The agent runs one turn at a time, so the anchored turn is over and its Stop
            # was lost. Release the anchor before this turn's Stop can take it.
            self.abandon_in_flight("admind did not see the admin agent finish it before "
                                   "another prompt started a turn", arrival)

    def on_session_start(self, ev: HookEvent, arrival: int | None = None) -> None:
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
            self.audit_quietly("agent", action="session-start", session=ev.session_id, source=source,
                               effect="ready")
            self.mark_ready(ev.launch)
            return
        self.abandon_in_flight("the admin agent restarted before answering; resend if needed", arrival,
                               idle=True)
        self.agent.started(ev.session_id)
        self.audit_quietly("agent", action="session-start", session=ev.session_id, source=source,
                           effect="restart")
        self.mark_ready(ev.launch)

    def turn_identity(self, session_id: str) -> tuple[str, str | None, str | None, str | None, str | None]:
        """What a Stop must still find true when it applies its effects: the session it came from, the
        message in flight, its confirmed anchor, the busy period and the launch."""
        return (session_id, self.store.get("in_flight"), self.store.get("anchor"), self.store.get("busy"),
                self.agent.launch_nonce)

    def session_current(self, session_id: str) -> bool:
        return session_id == self.agent.session_id and session_id not in self.retired

    async def extract(self, ev: HookEvent) -> str | None:
        """The transcript fallback, bounded: None if it took longer than `extract_timeout`, or if an
        earlier read is still running (at most one extraction thread, so a stalled filesystem cannot pile
        them up). The abandoned thread runs to completion in the background."""
        if self._extraction is not None and not self._extraction.done():
            return None
        task = asyncio.ensure_future(asyncio.to_thread(reply_text, ev))
        self._extraction = task
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())  # never "not retrieved"
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.extract_timeout)
        except TimeoutError:
            return None

    async def on_stop(self, ev: HookEvent, arrival: int | None = None, stale: bool = False,
                      validate: bool = False) -> None:
        """A Stop ends a turn only if admind has already processed the prompt that anchored it (or no
        reservation is in flight at all: a turn begun at the terminal, or after a lost prompt hook). A Stop
        that finds a reservation in flight but unanchored, that is stale (an earlier launch, or accepted
        before the turn was released) changes no turn state: its own text is posted top-level, otherwise
        nothing is and the record says so."""
        async with self.turn_lock():
            if validate:
                kind = self.classify(ev)        # under the lock: it protects the effects applied below
                if kind == "other":
                    noop_event()
                    self.audit.write("hook", action="ignored-other-session")
                    return
                stale = stale or kind == "stale"
            stale = stale or self.below_floor(arrival)
            if stale:
                noop_event()        # a stale Stop changes no state: failing later holds nothing
            # The identity is captured here, before the transcript read yields: !interrupt, !new, a
            # new dispatch or a relaunch can run meanwhile (they take the lock the read does not hold).
            identity = self.turn_identity(ev.session_id)
            in_flight, anchor = identity[1], identity[2]
            unanchored = in_flight is not None and anchor is None
            own = ev.last_assistant_message or ""
            if unanchored and not stale and self.session_current(ev.session_id):
                self.store.set("stopped_flight", in_flight or "")    # a prompt for it is now late
            if (stale or unanchored) and not own.strip():
                # No transcript fallback: its last assistant text may belong to another turn.
                if self.session_current(ev.session_id):
                    self.audit.write("agent", action="stale-stop-unrecoverable")
                return
            need_fallback = not stale and not unanchored and not ev.last_assistant_message
        raw = own
        if need_fallback:
            extracted = await self.extract(ev)      # may read the transcript file (lock not held)
            if extracted is None:
                self.audit.write("agent", action="reply-extraction-timeout")
                return
            raw = extracted
        async with self.turn_lock():
            if not self.session_current(ev.session_id):
                noop_event()
                self.audit.write("agent", action="stale-stop")   # a retired session: fixed wording only
                return
            current = (not (stale or unanchored) and not self.below_floor(arrival)
                       and self.turn_identity(ev.session_id) == identity)
            if need_fallback and not current:
                # The turn changed while the transcript was read: its last text may be the new turn's.
                self.audit.write("agent", action="stale-stop-unrecoverable")
                return
            text = raw if raw.strip() else NO_REPLY
            parts = chunk.split(redact(text), self.s.chunk_chars)
            reply_seq = int(self.store.get("reply_seq") or "0") + 1
            self.store.set("reply_seq", str(reply_seq))
            # Thread only to a prompt the agent confirmed receiving, and only if the turn this Stop was
            # captured for is still the current one. Otherwise the reply is real and goes out unthreaded,
            # and the current turn's state is left exactly as it is.
            reply_to = anchor if current else None
            with self.store.transaction():      # reply, cleared turn and idle state: all or nothing
                for i, part in enumerate(parts):
                    self.post(f"reply:{ev.session_id}:{reply_seq}:{i}", part, reply_to)
                if current:
                    if anchor is not None:
                        self.store.delete("anchor")
                        self.store.delete("in_flight")
                        self.store.delete("in_flight_text")
                    self.set_idle()             # the turn ended; an unconfirmed in_flight still holds
                    if arrival is not None:
                        self.raise_floor(arrival)   # a prompt accepted before this Stop is that turn's
            if not current:
                self.audit.write("agent", action="late-stop")   # fixed wording; nothing from the event
            # The agent's text goes to the operator's chat only; the audit log records its size.
            self.audit.write("reply", session=ev.session_id, reply_to=reply_to, chars=len(text),
                             chunks=len(parts))

    # --- outbound ------------------------------------------------------------------------------
    async def outbox_loop(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), 5)
            self.wake.clear()
            await self.outbox_pass()

    async def outbox_pass(self) -> None:
        """One sweep of the outbox (a loop iteration, callable on its own). Each send holds `send_lock`,
        and the gate and the next row are read under it: a membership transition that has drained the
        lock sees no send in progress and none can start (B7). Rows are redacted again at delivery (B1).
        A retry's backoff is a timer, not a sleep in this loop: the pass ends, and a row queued meanwhile
        (`post` sets `wake`) is sent at once if it comes first, so a lane-1 message never waits behind a
        lane-2 row's backoff (B14). The timer sets `wake` when it ends."""
        if self.held:
            await self.flush()              # emits NOT_READY once the start timeout passes
        self.notify_held()
        while True:
            async with self.send_lock:
                if not self.may_post():     # rechecked per row: a latch mid-batch stops the rest
                    return
                row = self.store.next_pending()     # re-read each time: a new lane-1 row goes next (B14)
                if row is None:
                    return
                timer = self._backoff.get(row.seq)
                if timer is not None and not timer.done():
                    return                  # its retry is not due; its timer or a new row wakes the loop
                try:
                    sent = await self.client.send_final(self.account, self.group, redact(row.text),
                                                        row.reply_to, row.key)
                except ControlError as exc:
                    attempts = self.store.mark_attempt(row.seq)
                    if not (exc.retryable and attempts < MAX_SEND_ATTEMPTS):
                        self._backoff.pop(row.seq, None)
                        self.send_failed(row.seq, row.key)
                        self.audit.write("send", key=row.key, action="failed", code=exc.code)
                        continue
                    self.audit.write("send", key=row.key, action="retry", attempts=attempts, code=exc.code)
                    timer = asyncio.ensure_future(self._sleep(min(60, 2 ** attempts)))
                    timer.add_done_callback(lambda _: self.wake.set())
                    self._backoff[row.seq] = timer      # per row: no other row's retry cuts it short
                    return
                else:
                    self._backoff.pop(row.seq, None)
                    self.store.mark_sent(row.seq, sent.message_ids_hex[0] if sent.message_ids_hex else None)
                    self.audit.write("send", key=row.key, action="sent")
                    continue

    def send_failed(self, seq: int, key: str) -> None:
        """A message admind gave up on (Task 8 adds the backstop for replies)."""
        self.store.mark_failed(seq)

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
        text = redact(alerts.render(name, alert, self.s.chunk_chars))
        if self.store.relay_alert(raw, key, text):
            self.wake.set()
            self.audit_quietly("alert", name=alerts.display_name(name),
                               key=key, malformed=alert is None)    # name whole: redacted before any cut

    def alert_failed(self, raw: bytes, exc: Exception) -> None:
        """Report an alert admind could not relay: by exception type and opaque key only, and tell the
        operator with fixed wording. If even that fails the alert is skipped for this process."""
        key = alerts.outbox_key(raw)
        skipped: str | None = None
        try:
            if self.store.relay_alert(raw, key, ALERT_FAILED.format(ref=key.partition(":")[2][:12])):
                self.wake.set()
        except Exception as inner:  # noqa: BLE001 - nothing more can be done for this file
            self.bad_alerts.add(raw)
            skipped = type(inner).__name__
        # The fallback or the skip is recorded first; the audit (quiet) cannot undo or pre-empt either.
        self.audit_quietly("alert", key=key, action="failed", error=type(exc).__name__)
        if skipped is not None:
            self.audit_quietly("alert", key=key, action="skipped", error=skipped)

    async def group_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.group_check_seconds)
            await self.check_group()
