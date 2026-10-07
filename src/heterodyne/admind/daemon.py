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
import dataclasses
import hmac
import json
import os
import re
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from heterodyne.admind import (
    alerts,
    approvals,
    asks,
    backstop,
    chunk,
    commands,
    ctl,
    guard,
    membership,
    summarize,
)
from heterodyne.admind.agent import AdminAgent, AgentStuck
from heterodyne.admind.approvals import ApproveBead, Attempt, BtqError, Busy, Readout
from heterodyne.admind.audit import Audit, ref_id
from heterodyne.admind.hook import (
    HOOK_EVENTS,
    MAX_REPLY,
    READ_CANCEL,
    TRUNCATED,
    Delivery,
    HookEvent,
    HookServer,
    render_tool_calls,
    reply_text,
    same_prompt,
    transcript_size,
)
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import AdmindSettings, Operator
from heterodyne.admind.store import AskRow, Store, TurnRow, now
from heterodyne.agents.claude_code import headless_argv
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
# Asks (relay spec §6, R15, R17). Fixed wording; an ask ID is from asks.ID_ALPHABET and safe to show.
ASK_BUSY = "admind is already handling two posts; try again shortly."
ASK_LATCHED = "admind is latched and posts nothing; the ask was not posted."
ASK_NO_APPROVALS = "Approval asks are not configured on this host ([admind] approve_bead)."
ASK_REDACTED = asks.REDACTED
ASK_BEAD_BUSY = "ask {ask_id} for {bead} is being decided or awaits a read-back; post again once it settles."
ASK_SUPERSEDED_NOTICE = ("Ask {old} is superseded by ask {new}, a newer card for {bead}. This card decides "
                         "nothing any more.")
# Approval decisions (relay spec §6). Every value shown is a bead ID, a digest prefix, an ask ID, a policy
# name, or approve-bead's first stderr line after `redact`; the replies go through `post` (redacted) too.
NOTED = ("Noted on ask {ask_id}; this is not a decision. To decide, reply to the card with !approve {bead} "
         "{digest12} or !deny {bead} <reason>.")
NOT_A_CARD_REPLY = "To decide, reply to the approval card itself (or its !details). Nothing recorded."
NO_MATCH = "That does not match ask {ask_id} (bead {bead}, digest {digest12}). Nothing recorded."
NOT_DELIVERED = ("Ask {ask_id} has not been fully delivered yet. Wait for every part, then reply again. "
                 "Nothing recorded.")
NEEDS_DETAILS = "Ask {ask_id} was shortened. Reply !details to it and read it first. Nothing recorded."
STALE = ("{bead} changed after it was shown (shown {shown}, now {now}). Nothing recorded; ask {ask_id} is "
         "stale and the poster must post it again.")
NOT_APPROVER = "{name} is not a btq approver. Nothing recorded."
NOT_DECIDABLE = "{bead} is {why}. Nothing recorded."
BEAD_BUSY = "{bead} is being decided elsewhere right now. Nothing recorded; try again in a minute."
PREFLIGHT_FAILED = "admind could not check {bead} ({word}); nothing was recorded. Try again."
APPROVED_ACCEPTED = "Approved {bead} as {name} (digest {digest12}, via Marmot). btq's design gate accepts it."
APPROVED_REJECTED = ("Approved {bead} as {name} (digest {digest12}, via Marmot), but btq's design gate "
                     "rejects it: {why}. Check it on the host.")
DENIED = "Denied {bead} as {name} (via Marmot)."
UNTOUCHED = "Not recorded: \"{line}\". {bead} is unchanged; you can decide again."
UNTOUCHED_SILENT = "Not recorded. {bead} is unchanged; you can decide again."
BLOCKED = ("{bead} holds a decision admind cannot confirm as yours{quoted}. Nothing more will be done from "
           "Marmot. Resolve it on the host with approve-bead {bead}.")
UNCERTAIN_READ = ("admind could not read {bead} back. No further decision is taken from Marmot until it can; "
                  "check it on the host with approve-bead {bead}.")
CONFLICT = "Ask {ask_id} changed while this decision was settled; see !asks. Check {bead} on the host."
RECONCILE_RESTARTED = "admind restarted while recording this decision; nothing was recorded. Decide again."
RECONCILE_STRANDED = ("admind could not finish settling the last decision on ask {ask_id}; nothing was "
                      "recorded. Decide again.")
RECONCILE_UNVERIFIED = ("admind could not check whether this decision on {bead} was recorded: "
                        "approve-bead is not configured on this host ([admind] approve_bead). Check it on "
                        "the host with approve-bead {bead}.")
ASK_TOO_MANY = f"there are already {asks.MAX_OPEN} active asks; cancel some or wait for answers."
ASK_TOO_OFTEN = f"{asks.MAX_PER_HOUR} asks were posted in the last hour; try again later."
ASK_CANCELLED_NOTICE = "Ask {ask_id} was cancelled by its poster. Nothing more is needed."
NO_ACTIVE_ASKS = "No active asks."
MAX_SEND_ATTEMPTS = 10
AGENT_POLL = 5.0        # seconds between checks that the admin agent's pane is still alive
READY_TIMEOUT = 120.0   # seconds after a launch with no SessionStart before the agent is relaunched
AGENT_STUCK_NOTICE = "The admin agent is not running ({why}). Use !tail, then !new."
MESSAGE_ID = re.compile(r"[0-9a-f]{64}")
KNOWN_HOOKS = frozenset(HOOK_EVENTS)
EXTRACT_SECONDS = 10.0      # the transcript fallback's deadline (a stalled filesystem must not hold a slot)
OFFSET_SECONDS = 2.0        # a transcript size measurement's deadline (B19, B20)
SUMMARY_POLL = 5.0          # summary_loop re-reads the database at least this often (B9)
DETAILS_READ_SECONDS = 30.0     # `!details full`'s transcript read (B20)
DETAILS_BUSY = "(the transcript is busy or slow; try `!details full` again)"
NO_TOOL_CALLS = "(tool calls are not available for this turn)"
DETAILS_NOT_READ = "(not read: time limit)"    # a turn the command's budget ran out before
MAX_DETAILS_PARTS = 20_000     # rows one !details publishes (and discards) in a single statement
DETAILS_SLICE = 500     # parts staged per turn of the event loop
EXTRACT_FAILED = ("(admind could not read this reply from the transcript in time. Reply `!details full` "
                  "for the turn's tool calls, or ask the agent to repeat it.)")
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


def details_notice(cap: int) -> str:
    """What `!details` says it stopped at: TRUNCATED for the full 64 MiB, otherwise the smaller limit."""
    if cap >= MAX_REPLY:
        return TRUNCATED
    size = f"{cap >> 20} MiB" if cap % (1 << 20) == 0 else f"{cap} bytes"      # exact, never rounded
    return f"(details truncated at {size})"


def utf8_size(text: str) -> int:
    """UTF-8 bytes of `text`, a lone surrogate counting as the one byte it is replaced by."""
    return len(text.encode("utf-8", "replace"))


def own_text(text: str) -> str:
    """The operator's own text for the audit log, whole (revision 13), redacted (B1; the audit redacts again,
    idempotently)."""
    return redact(text)


def prepare_reply(raw: str, lines: int, chars: int) -> tuple[str, str] | None:
    """A reply's redacted text and its mode, `verbatim` or `summary`. CPU-bound on a large reply, so it runs
    in a thread. None if the text can't be stored (a lone surrogate) or is over MAX_REPLY UTF-8 bytes once
    redacted: the caller treats the turn as unread."""
    text = redact(raw if raw.strip() else NO_REPLY)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    if size > MAX_REPLY:
        return None                 # redaction can expand control characters about fourfold
    return text, "summary" if summarize.needs_summary(text, lines, chars) else "verbatim"


def render_batch(rows: list[TurnRow]) -> str:
    """One batch's message, redacted and fitted. CPU-bound, so it runs in a thread."""
    return backstop.fit(redact(backstop.render([backstop.Entry(r.origin, r.text) for r in rows])),
                        backstop.BATCH_MAX_CHARS)      # one message, not chunk_chars (B10)


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
        self.details_timeout = DETAILS_READ_SECONDS         # replaced in tests
        self.hook_deadline = HOOK_DEADLINE          # replaced in tests
        self.hook_lock_wait = HOOK_LOCK_WAIT        # replaced in tests
        self._reader: asyncio.Future[Any] | None = None     # the one transcript-read thread allowed (B20)
        self._idle_tasks: set[asyncio.Task[None]] = set()
        self.held: list[tuple[str, str]] = []
        self.clock: Callable[[], float] = time.monotonic    # replaced in tests
        self.agent_poll = AGENT_POLL                        # replaced in tests
        self.ready_timeout = READY_TIMEOUT                  # replaced in tests
        self.launched_at = self.clock()
        self.stuck: str | None = None
        self.not_ready_sent = False
        self.summary_wake = asyncio.Event()
        self.summarizer_argv: list[str] | None = (
            None if settings.summarizer is None or settings.summarizer_binary is None
            else headless_argv(settings.summarizer_binary, settings.summarizer))
        self.summary_timeout = summarize.SUMMARY_TIMEOUT    # replaced in tests
        self.summary_poll = SUMMARY_POLL                    # replaced in tests
        self.offset_timeout = OFFSET_SECONDS                # replaced in tests
        self.batch_seconds = backstop.BATCH_SECONDS         # replaced in tests
        self.batch_poll = 1.0                               # replaced in tests
        self.wallclock: Callable[[], float] = time.time     # replaced in tests; batches outlive a restart
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
        self.posts_in_flight = 0                # ask posts on ask.sock being handled (R15)
        self.ask_post_lock = asyncio.Lock()     # posts run one at a time (R22)
        self.ask_server: ctl.JsonSocketServer[asks.AskRequest, asks.AskReply] | None = None
        self.approve_bead = None if settings.approve_bead is None else ApproveBead(settings.approve_bead)

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
            self.deny_rows(mid)
        self.audit.write("drop", message_id=mid, reason="no longer authorised", what=what)

    def deny_rows(self, mid: str) -> None:
        """`deny`'s store writes, for a caller that joins them to its own transaction."""
        self.store.set_inbound(mid, "dropped")
        if not self.latched():
            self.reply(mid, UNVERIFIED, "unverified")

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
        self.store.discard_details()    # a !details in preparation died with the process
        # `busy` is kept: an adopted session may still be mid-turn. Its Stop, a SessionStart (a launched
        # or resumed agent), or the operator's !interrupt clears it.
        # Received (held, never pasted) or executing (a command that may not have run to the end): each
        # is answered once, and the state change and its notice commit together. The message of a persisted
        # decision attempt is left to the ask reconcile (R26), which reads the bead back first: it may have
        # been recorded, and "resend it" would then be wrong. The reconcile marks it done with its notice.
        attempts = {attempt for _, attempt, _ in self.store.recovery_snapshot()}
        for status in ("received", "executing"):
            for mid in self.store.inbound_with_status(status):
                if mid in attempts:
                    continue
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
        snapshot = self.store.recovery_snapshot()      # before any loop starts (R26, r3-2)
        await self.check_group()
        server = self.make_server(self.s.state_dir / "hook.sock")
        control: ctl.JsonSocketServer[ctl.CtlRequest, ctl.CtlReply] | None = None
        try:
            await server.start()
            control = ctl.CtlServer(self.s.state_dir / ctl.CTL_SOCKET, self.on_ctl, self.audit)
            await control.start()
            self.ask_server = ctl.JsonSocketServer(
                self.s.state_dir / asks.ASK_SOCKET, self.on_ask, self.audit, kind="ask",
                max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY, request_type=asks.AskRequest,
                check=asks.check, refused=asks.refused, failed=asks.failed, describe=asks.describe)
            await self.ask_server.start()
            await self.start_agent(startup=True)
            async with asyncio.TaskGroup() as tg:
                for name, loop in (("inbound", self.inbound_loop), ("worker", self.worker_loop),
                                   ("hooks", self.hook_loop), ("outbox", self.outbox_loop),
                                   ("alerts", self.alerts_loop), ("group", self.group_loop),
                                   ("agent", self.agent_loop), ("summaries", self.summary_loop),
                                   ("batches", self.batch_loop)):
                    tg.create_task(self.guarded(supervised(name, loop, self.audit)))
                tg.create_task(self.guarded(self.reconcile_asks(snapshot)))
        except BaseException:
            self.shutting_down = True
            raise
        finally:
            self.shutting_down = True
            for timer in self._backoff.values():
                timer.cancel()
            self._backoff.clear()
            if self.ask_server is not None:
                await self.ask_server.close()
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

    # --- asks (relay spec) ----------------------------------------------------------------------
    async def on_ask(self, req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        """A request on ask.sock, already checked by `asks.check`. Get, list and cancel work while
        latched (R17); a post does not."""
        if isinstance(req, asks.AskPost):
            return await self.serialised_post(req, peer)
        if isinstance(req, asks.AskList):
            rows = self.store.asks_with_status(*asks.ACTIVE) + self.store.recent_asks(20)
            return asks.AskReply("ok", f"{len(rows)} asks", asks=[self.ask_summary(r) for r in rows])
        ask_id = asks.normalise_id(req.ask_id)
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None:
            return asks.refused(f"no ask {show(req.ask_id, False)}")
        if isinstance(req, asks.AskGet):
            return asks.AskReply("ok", row.status, ask=self.ask_view(row))
        return self.cancel_ask(row)

    async def serialised_post(self, req: asks.AskPost, peer: ctl.Peer) -> asks.AskReply:
        """At most MAX_IN_FLIGHT posts at once (the next is refused), run one at a time (R15, R22)."""
        if self.posts_in_flight >= asks.MAX_IN_FLIGHT:
            return asks.refused(ASK_BUSY)
        self.posts_in_flight += 1
        try:
            async with self.ask_post_lock:
                if req.kind == "approval":
                    return await self.post_approval(req, peer)
                return self.post_ask(req, peer)
        finally:
            self.posts_in_flight -= 1

    def post_ask(self, req: asks.AskPost, peer: ctl.Peer) -> asks.AskReply:
        """Under `ask_post_lock`: check the latch and the limits, then store a question or merge ask and
        queue its card in one transaction."""
        if self.latched():
            return asks.refused(ASK_LATCHED)
        if len(self.store.asks_with_status(*asks.ACTIVE)) >= asks.MAX_OPEN:
            return asks.refused(ASK_TOO_MANY)
        if self.store.ask_posted_since(asks.hour_ago()) >= asks.MAX_PER_HOUR:
            return asks.refused(ASK_TOO_OFTEN)
        at = asks.stamp(asks.now())
        row = AskRow(asks.new_id(lambda i: self.store.ask(i) is not None), req.kind, req.poster, req.title,
                     req.body, req.pr_url, req.head_sha, req.bead, None, False, 0, "open", None, None, at, at)
        text, truncated = asks.question_card(row)
        parts = chunk.split(text, self.s.chunk_chars)
        row = dataclasses.replace(row, truncated=truncated, card_parts=len(parts))
        with self.store.transaction():
            self.store.insert_ask(row, peer.pid)
            for i, part in enumerate(parts):
                self.post(f"ask:{row.ask_id}:{i}", part, None)
        self.audit.write("ask", action="posted", ask_id=row.ask_id, ask_kind=row.kind, poster=row.poster,
                         pid=peer.pid, parts=len(parts), truncated=truncated, title_chars=len(row.title),
                         body_chars=len(row.body))
        return asks.AskReply("posted", f"ask {row.ask_id} posted", ask=self.ask_view(row))

    def approval_post_refusal(self, bead: str) -> str | None:
        """Spec §8 "Post", steps 1 and 4 (R15, R17, R22): checked before the read and again after it."""
        if self.latched():
            return ASK_LATCHED
        if self.approve_bead is None:
            return ASK_NO_APPROVALS
        if len(self.store.asks_with_status(*asks.ACTIVE)) >= asks.MAX_OPEN:
            return ASK_TOO_MANY
        if self.store.ask_posted_since(asks.hour_ago()) >= asks.MAX_PER_HOUR:
            return ASK_TOO_OFTEN
        busy = self.store.ask_for_bead(bead, "deciding", "uncertain")
        if busy is not None:
            return ASK_BEAD_BUSY.format(ask_id=busy.ask_id, bead=bead)
        return None

    def approval_refused(self, bead: str, message: str, why: str) -> asks.AskReply:
        self.audit.write("ask", action="refused", ask_kind="approval", bead=bead, reason=why)
        return asks.refused(message)

    async def post_approval(self, req: asks.AskPost, peer: ctl.Peer) -> asks.AskReply:
        """Spec §8 "Post an approval ask", under `ask_post_lock` (R22): read the bead with approve-bead,
        render the card from its readout (R21), re-check, then supersede, store and queue in one
        transaction."""
        bead = req.bead or ""
        refusal = self.approval_post_refusal(bead)
        if refusal is not None or self.approve_bead is None:
            return self.approval_refused(bead, refusal or ASK_NO_APPROVALS, "limits")
        try:
            r = await self.approve_bead.read(bead)
        except BtqError as exc:
            return self.approval_refused(bead, f"admind could not read {bead} ({exc.word}); try again.",
                                         exc.word)
        if isinstance(r, Busy):
            return self.approval_refused(bead, BEAD_BUSY.format(bead=bead), "busy")
        why = approvals.postable(r)
        if why is not None:
            return self.approval_refused(bead, redact(why), "not postable")
        at = asks.stamp(asks.now())
        row = AskRow(asks.new_id(lambda i: self.store.ask(i) is not None), "approval", req.poster,
                     asks.approval_title(r, bead), "", None, None, bead, r.digest, False, 0, "open", None,
                     None, at, at)
        card = asks.approval_card(row, r, self.s.chunk_chars)
        if isinstance(card, str):
            return self.approval_refused(bead, card, "redaction" if card == ASK_REDACTED else "too long")
        row = dataclasses.replace(row, body=asks.approval_body(r, card), card_parts=len(card.card_chunks))
        refusal = self.approval_post_refusal(bead)      # the read awaited: the latch and limits again (R22)
        if refusal is not None:
            return self.approval_refused(bead, refusal, "limits")
        superseded: list[str] = []
        with self.store.transaction():
            for old in self.store.asks_with_status("open", "answered"):
                if old.bead == bead:
                    superseded.append(old.ask_id)
                    self.store.set_ask(old.ask_id, "superseded")
                    self.ask_notice(old.ask_id, "superseded", row.ask_id, ASK_SUPERSEDED_NOTICE.format(
                        old=old.ask_id, new=row.ask_id, bead=bead))
            self.store.insert_ask(row, peer.pid)
            for i, part in enumerate(card.card_chunks):     # the checked chunks, as they are (R21)
                self.post(f"ask:{row.ask_id}:{i}", part, None)
        self.audit.write("ask", action="posted", ask_id=row.ask_id, ask_kind=row.kind, poster=row.poster,
                         pid=peer.pid, bead=bead, digest12=(r.digest or "")[:12], parts=len(card.card_chunks),
                         details_parts=len(card.details_chunks), truncated=False,
                         superseded=superseded)
        return asks.AskReply("posted", f"ask {row.ask_id} posted", ask=self.ask_view(row))

    def cancel_ask(self, row: AskRow) -> asks.AskReply:
        if row.status not in ("open", "answered"):
            return asks.refused(f"ask {row.ask_id} is {row.status}; only an open or answered ask can be "
                                "cancelled")
        with self.store.transaction():
            self.store.set_ask(row.ask_id, "cancelled")
            self.ask_notice(row.ask_id, "cancelled", "0", ASK_CANCELLED_NOTICE.format(ask_id=row.ask_id))
        self.audit.write("ask", action="cancelled", ask_id=row.ask_id)
        return asks.AskReply("ok", f"ask {row.ask_id} cancelled")

    def ask_notice(self, ask_id: str, what: str, ref: str, text: str) -> None:
        """A notice about an ask, threaded to its card's first sent chunk (unthreaded if none was sent)."""
        thread = self.store.first_card_message(ask_id)
        for i, part in enumerate(chunk.split(redact(text), self.s.chunk_chars)):
            self.post(f"asknote:{ask_id}:{what}:{ref}:{i}", part, thread)

    def ask_summary(self, row: AskRow) -> asks.AskSummary:
        count, _ = self.store.answer_totals(row.ask_id)
        return asks.summary(row, self.store.card_delivered(row.ask_id), count)

    def ask_view(self, row: AskRow) -> asks.AskView:
        return asks.view(row, self.store.card_delivered(row.ask_id), self.store.answers(row.ask_id))

    def operator_name(self, mid: str) -> str:
        return self.operators.get(self.senders.get(mid, ""), "?")

    async def ask_reply(self, mid: str, ask_id: str, text: str) -> None:
        """A plain reply to a card (R13, R14): stored as the answer, never pasted to the agent."""
        self.store.set_inbound(mid, "executing")
        if not self.authorised(mid):
            self.deny(mid, "ask")
            return
        self.answer_ask(mid, ask_id, text)

    def answer_ask(self, mid: str, ask_id: str, text: str) -> None:
        """Store an operator's answer to a question or merge ask and reply in thread, as one step."""
        row = self.store.ask(ask_id)
        if row is None:
            self.finish(mid, "done", f"No ask {ask_id}.", "ask")
            return
        refusal = self.answer_refusal(row, text)
        if refusal is not None:
            self.finish(mid, "done", refusal, "ask")
            self.audit.write("ask", action="answer-refused", message_id=mid, ask_id=ask_id, chars=len(text))
            return
        if row.kind == "approval":      # a note the poster sees; never a decision (R13)
            with self.store.transaction():
                self.store.add_answer(ask_id, "note", self.operator_name(mid), mid, text)
                self.finish(mid, "done", NOTED.format(ask_id=ask_id, bead=row.bead,
                                                      digest12=(row.digest or "")[:12]), "ask")
            self.audit.write("ask", action="noted", message_id=mid, ask_id=ask_id, chars=len(text))
            return
        again = row.status == "answered"
        with self.store.transaction():
            self.store.add_answer(ask_id, "answer", self.operator_name(mid), mid, text)
            self.store.set_ask(ask_id, "answered")
            self.finish(mid, "done", (f"Added to ask {ask_id}; it was already answered, and the poster sees "
                                      "both.") if again else f"Answer recorded for ask {ask_id}.", "ask")
        self.audit.write("ask", action="answered", message_id=mid, ask_id=ask_id, chars=len(text))

    def answer_refusal(self, row: AskRow, text: str) -> str | None:
        """Why an answer is not stored (R15): fixed wording, or None."""
        if row.status not in ("open", "answered"):
            return f"Ask {row.ask_id} is already {row.status}. Nothing recorded."
        if not text.strip():
            return "Not recorded: the answer is empty."
        if len(text) > asks.MAX_ANSWER:
            return f"Not recorded: an answer is at most {asks.MAX_ANSWER:,} characters."
        count, total = self.store.answer_totals(row.ask_id)
        if count >= asks.MAX_ANSWERS:
            return f"Not recorded: ask {row.ask_id} already has {asks.MAX_ANSWERS} answers."
        if total + len(text) > asks.MAX_ANSWER_TOTAL:
            return (f"Not recorded: ask {row.ask_id}'s answers would pass {asks.MAX_ANSWER_TOTAL:,} "
                    "characters.")
        return None

    async def list_asks(self, mid: str) -> None:
        """`!asks`: first the backstop (spec §8): in the worker, under `work_lock`, no decision is in
        flight, so every `deciding` or `uncertain` ask is stranded and is reconciled as at startup. Then
        the active asks, one line each."""
        for ask_id, attempt, status in self.store.recovery_snapshot():
            await self.reconcile_quietly(ask_id, attempt, status, restarted=False)
        if not self.authorised(mid):        # the read-backs awaited
            self.deny(mid, "command")
            return
        at = asks.now()
        lines = [asks.list_line(r, at) for r in self.store.asks_with_status(*asks.ACTIVE)]
        self.finish(mid, "done", "\n".join(lines) or NO_ACTIVE_ASKS, "cmd")
        self.audit.write("command", message_id=mid, command="asks", asks=len(lines))

    def bang_answer(self, mid: str, cmd: commands.Command) -> None:
        """`!answer <id> <text>`: as a reply to the card, from anywhere."""
        ask_id = asks.normalise_id(cmd.arg or "")
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None or ask_id is None:
            self.finish(mid, "done", show(f"No ask {cmd.arg}.", False), "cmd")
            return
        if row.kind == "approval":
            self.finish(mid, "done", f"Ask {ask_id} is an approval ask: reply to its card with !approve "
                        f"{row.bead} {(row.digest or '')[:12]} or !deny {row.bead} <reason>. Nothing "
                        "recorded.", "cmd")
            return
        self.answer_ask(mid, ask_id, cmd.rest or "")

    def ask_details(self, mid: str, ask_id: str) -> None:
        """`!details` as a reply to a card or its details: the whole ask in lane 2, threaded to the
        command, with the number of parts recorded for R8."""
        row = self.store.ask(ask_id)
        if row is None:
            self.finish(mid, "done", f"No ask {ask_id}.", "cmd")
            return
        if row.kind == "approval":     # the chunks checked when it was posted, as they are (R21, r2-1)
            parts = asks.stored_details(row)
        else:
            parts = chunk.split(asks.full_text(row), self.s.chunk_chars)
        with self.store.transaction():
            for i, part in enumerate(parts):
                self.post(f"askd:{ask_id}:{mid}:{i}", part, mid, lane=2)
            self.store.add_ask_details(ask_id, self.operator_name(mid), mid, len(parts))
            self.store.set_inbound(mid, "done")
        self.audit.write("ask", action="details", message_id=mid, ask_id=ask_id, parts=len(parts))

    # --- approval decisions (relay spec §8 "Decide", R7-R12, R23, R26) ------------------------------
    def decide_refused(self, mid: str, text: str, why: str, ask_id: str | None = None) -> None:
        """A refusal before the attempt exists: the ask is unchanged."""
        self.finish(mid, "done", text, "ask")
        self.audit.write("ask", action="refused", message_id=mid, ask_id=ask_id, reason=why)

    def not_open(self, row: AskRow) -> str:
        """`Ask p4xw is already <status>[ by <name>][; see ask q9rt]. Nothing recorded.`"""
        by = f" by {row.decided_by}" if row.decided_by else ""
        newer = self.store.ask_for_bead(row.bead or "", *asks.ACTIVE, *asks.TERMINAL)
        see = f"; see ask {newer.ask_id}" if row.status == "superseded" and newer is not None else ""
        return f"Ask {row.ask_id} is already {row.status}{by}{see}. Nothing recorded."

    async def decide(self, mid: str, cmd: commands.Command, target: str | None) -> None:
        """`!approve <bead> <digest12>` / `!deny <bead> <reason>`, in the worker under `work_lock` and after
        `handle`'s control-character and authorisation checks (spec §8 "Decide", steps 1-8)."""
        bead, rest = cmd.arg or "", cmd.rest or ""
        ask_id = self.store.ask_for_message(target)            # step 1 (R7)
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None or row.kind != "approval" or row.bead is None or row.digest is None:
            self.decide_refused(mid, NOT_A_CARD_REPLY, "not a card reply", ask_id)
            return
        digest12 = row.digest[:12]
        if bead != row.bead or (cmd.name == "approve" and rest != digest12):        # step 2
            self.decide_refused(mid, NO_MATCH.format(ask_id=row.ask_id, bead=row.bead, digest12=digest12),
                                "no match", row.ask_id)
            return
        if row.status != "open":
            self.decide_refused(mid, self.not_open(row), "not open", row.ask_id)
            return
        name = self.operator_name(mid)
        if cmd.name == "approve":                       # R8: the whole context was delivered
            if not self.store.card_delivered(row.ask_id):
                self.decide_refused(mid, NOT_DELIVERED.format(ask_id=row.ask_id), "not delivered", row.ask_id)
                return
            if row.truncated and not self.store.details_delivered(row.ask_id, name):
                self.decide_refused(mid, NEEDS_DETAILS.format(ask_id=row.ask_id), "needs details", row.ask_id)
                return
        if not self.authorised(mid):                    # step 3
            self.deny(mid, "decision")
            return
        if self.approve_bead is None:
            self.decide_refused(mid, ASK_NO_APPROVALS, "not configured", row.ask_id)
            return
        deny = cmd.name == "deny"
        a = Attempt(row.ask_id, mid, "deny" if deny else "approve", name, "marmot:" + ref_id(mid), row.digest,
                    rest if deny else None)
        if not self.store.begin_attempt(a):             # step 4 (R22, R23)
            now_row = self.store.ask(row.ask_id)
            self.decide_refused(mid, self.not_open(now_row or row), "not open", row.ask_id)
            return
        await self.run_attempt(a, row)

    def preflight(self, r: Readout | Busy, a: Attempt, bead: str) -> tuple[str, str, str] | None:
        """Step 5: why the attempt stops before the decision runs, as (new ask status, reply, reason), or
        None to go on."""
        if isinstance(r, Busy):
            return "open", BEAD_BUSY.format(bead=bead), "busy"
        if r.digest != a.digest:
            return "stale", STALE.format(bead=bead, shown=a.digest[:12], now=(r.digest or "none")[:12],
                                         ask_id=a.ask_id), "stale"
        if r.decided:
            return "blocked", BLOCKED.format(bead=bead, quoted=""), "decided"
        if r.status != "open":
            return "open", NOT_DECIDABLE.format(bead=bead, why=f"{r.status}, not open"), "not open"
        if not r.kind_approval:
            return "open", NOT_DECIDABLE.format(bead=bead, why="not a kind:approval bead"), "not approval"
        if a.operator not in r.approvers:
            return "open", NOT_APPROVER.format(name=a.operator), "not an approver"
        return None

    def close_refused(self, a: Attempt, new_status: str, text: str, why: str) -> None:
        """Close the attempt before anything was launched, with its reply, in one compare-and-set."""
        with self.store.transaction():
            closed = self.store.close_attempt(a.ask_id, a.message_id, expect_status="deciding",
                                              settled="refused", exit_status=None, new_status=new_status,
                                              outcome=text, decided_by=None)
            if closed:
                self.finish(a.message_id, "done", text, "ask")
        if not closed:
            self.conflict(a, "deciding")
            return
        self.audit.write("ask", action="refused", message_id=a.message_id, ask_id=a.ask_id, reason=why,
                         status=new_status)

    def conflict(self, a: Attempt, expected: str) -> None:
        """A compare-and-set found the ask changed: nothing was written. Fixed wording."""
        self.audit_quietly("ask", action="conflict", ask_id=a.ask_id, message_id=a.message_id,
                           expected=expected)

    async def run_attempt(self, a: Attempt, row: AskRow) -> None:
        """Steps 4-8 of a persisted attempt. Until the decision child exists, every failure closes the
        attempt as `refused` (r5-1); once it exists, every exit goes through the reap and the read-back."""
        assert self.approve_bead is not None and row.bead is not None      # noqa: S101 - checked by decide
        ab, bead, mid = self.approve_bead, row.bead, a.message_id
        launched = False

        def on_launch() -> None:
            nonlocal launched
            launched = True

        code: int | None = None
        line = ""
        audited = unauthorised = False
        stop: tuple[str, str, str] | None = None
        try:
            self.audit.write("ask", action="deciding", message_id=mid, ask_id=a.ask_id, bead=bead,
                             decision=a.action, operator=a.operator)
            audited = True
            r = await ab.read(bead)                                     # step 5
            stop = self.preflight(r, a, bead)
            if stop is None:
                unauthorised = not self.authorised(mid)                 # step 6: the commit point (R11)
                if not unauthorised:
                    code, line = await ab.decide(a, bead, on_launch)    # step 7
        except asyncio.CancelledError:
            raise       # left `deciding`; the startup reconcile settles it (R26)
        except Exception as exc:  # noqa: BLE001 - fixed words; nothing launched means nothing was written
            if not launched:
                # approve-bead's own failures carry their word (a spawn OSError is "unavailable"); the
                # audit write failing, or a bug, is "error" (spec §8's table)
                word = (exc.word if isinstance(exc, BtqError)
                        else "unavailable" if isinstance(exc, OSError) and audited else "error")
                stop = ("open", PREFLIGHT_FAILED.format(bead=bead, word=word), word)
        if unauthorised:
            with self.store.transaction():
                closed = self.store.close_attempt(a.ask_id, mid, expect_status="deciding", settled="refused",
                                                  exit_status=None, new_status="open", outcome=None,
                                                  decided_by=None)
                if closed:
                    self.deny_rows(mid)
            if not closed:
                self.conflict(a, "deciding")
                return
            self.audit.write("drop", message_id=mid, reason="no longer authorised", what="decision")
            return
        if stop is not None:
            self.close_refused(a, *stop)
            return
        latched_during = self.latched()                                 # step 8
        settled, back = await self.read_and_settle(a, bead)
        new_status, text = self.settlement(settled, back, a, bead, line)
        with self.store.transaction():
            closed = self.store.close_attempt(a.ask_id, mid, expect_status="deciding", settled=settled,
                                              exit_status=code, new_status=new_status, outcome=text,
                                              decided_by=a.operator if settled == "recorded" else None)
            if closed:
                self.finish(mid, "done", text, "ask")
        if not closed:
            self.conflict(a, "deciding")
            self.finish(mid, "done", CONFLICT.format(ask_id=a.ask_id, bead=bead), "ask")
        self.audit.write("ask", action="decided", message_id=mid, ask_id=a.ask_id, bead=bead, outcome=settled,
                         status=new_status, exit_status=code,
                         gate_valid=None if back is None else back.gate_valid, latched_during=latched_during)

    async def read_and_settle(self, a: Attempt, bead: str) -> tuple[str, Readout | None]:
        """The read-back (R12) against the attempt: its `settled` value, and the readout if there was one.
        Any failure, a bug included, is `uncertain`, which keeps the attempt for a later read-back."""
        assert self.approve_bead is not None    # noqa: S101 - only reached with approve-bead configured
        try:
            back = await approvals.read_back(self.approve_bead, bead)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - by type only; the ask stays uncertain
            self.audit_quietly("ask", action="read-back-failed", ask_id=a.ask_id, error=type(exc).__name__)
            back = None
        return ("uncertain" if back is None else approvals.settle(back, a)), back

    def settlement(self, settled: str, back: Readout | None, a: Attempt, bead: str,
                   line: str) -> tuple[str, str]:
        """(the ask's new status, the reply) for a settled attempt (R12, spec §6). Exit 5 (written, then
        the content changed) and "written, but the gate rejects it" are not read from the exit status:
        both settle from the read-back like every other outcome. approve-bead's stderr line is quoted in
        every reply that has one (R12): inside the untouched and blocked wording, and on a line of its own
        after the recorded and uncertain wording (exit 5's warning, say)."""
        if settled == "untouched":
            return "open", (UNTOUCHED.format(line=line, bead=bead) if line
                            else UNTOUCHED_SILENT.format(bead=bead))
        if settled == "blocked":
            return "blocked", BLOCKED.format(bead=bead, quoted=f' ("{line}")' if line else "")
        said = f'\napprove-bead said: "{line}"' if line else ""
        if settled == "recorded" and back is not None:
            if a.action == "deny":
                return "denied", DENIED.format(bead=bead, name=a.operator) + said
            if back.gate_valid:
                return "approved", APPROVED_ACCEPTED.format(bead=bead, name=a.operator,
                                                            digest12=a.digest[:12]) + said
            why = (f'"{back.gate_reasons[0]}"' if back.gate_reasons
                   else f"(btq gave no reason; run approve-bead {bead} on the host)")
            return "approved", APPROVED_REJECTED.format(bead=bead, name=a.operator, digest12=a.digest[:12],
                                                        why=why) + said
        return "uncertain", UNCERTAIN_READ.format(bead=bead) + said

    async def reconcile_asks(self, snapshot: list[tuple[str, str, str]]) -> None:
        """The startup reconcile (R26): each ask of the snapshot `run()` took before any loop started,
        under `work_lock`, so never while the worker decides. It never raises."""
        for ask_id, attempt, status in snapshot:
            async with self.work_lock:
                await self.reconcile_quietly(ask_id, attempt, status, restarted=True)

    async def reconcile_quietly(self, ask_id: str, attempt: str, status: str, *, restarted: bool) -> None:
        try:
            await self.reconcile_one(ask_id, attempt, status, restarted=restarted)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one ask's failure must not stop the rest; by type only
            self.audit_quietly("ask", action="reconcile-failed", ask_id=ask_id, error=type(exc).__name__)

    async def reconcile_one(self, ask_id: str, attempt: str, status: str, *, restarted: bool) -> None:
        """Read back one stranded attempt and settle it with a compare-and-set on the status it was seen
        in (r3-2). Its notice is threaded to the card; the attempt's inbound message, if it is still
        `executing` (or, defensively, `received`), is marked `done` in the same transaction (r6), so a
        restart does not answer it."""
        a = self.store.current_attempt(ask_id)
        row = self.store.ask(ask_id)
        if a is None or a.message_id != attempt or row is None or row.bead is None:
            self.audit_quietly("ask", action="reconcile-skipped", ask_id=ask_id)
            return
        if self.approve_bead is None:
            self.unverified(ask_id, attempt, row.bead)
            return
        settled, back = await self.read_and_settle(a, row.bead)
        new_status, text = self.settlement(settled, back, a, row.bead, "")
        if settled == "untouched":
            text = RECONCILE_RESTARTED if restarted else RECONCILE_STRANDED.format(ask_id=ask_id)
        with self.store.transaction():
            closed = self.store.close_attempt(ask_id, attempt, expect_status=status, settled=settled,
                                              exit_status=None, new_status=new_status, outcome=text,
                                              decided_by=a.operator if settled == "recorded" else None)
            if closed:
                if self.store.inbound_status(attempt) in ("received", "executing"):
                    self.store.set_inbound(attempt, "done")
                if new_status != status:
                    self.ask_notice(ask_id, "reconciled", str(self.next_seq("reconcile_seq")), text)
        if not closed:
            self.conflict(a, status)
            return
        self.audit.write("ask", action="reconciled", ask_id=ask_id, message_id=attempt, outcome=settled,
                         status=new_status, was=status, restarted=restarted)

    def unverified(self, ask_id: str, attempt: str, bead: str) -> None:
        """A stranded attempt that cannot be read back: approve_bead was removed (say, for a rollback).
        recover() left its message to the reconcile, so it is answered here, once: the notice and `done`
        commit together, and a later run finds the message done. It says the decision may be recorded, never
        "decide again". The ask and its attempt stay as they are, for a reconcile with approve_bead set."""
        with self.store.transaction():
            answered = self.store.inbound_status(attempt) in ("received", "executing")
            if answered:
                self.store.set_inbound(attempt, "done")
                self.ask_notice(ask_id, "unverified", attempt, RECONCILE_UNVERIFIED.format(bead=bead))
        self.audit_quietly("ask", action="reconcile-unverified" if answered else "reconcile-skipped",
                           ask_id=ask_id)

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
        self.store.record_prompt(mid, verdict.operator or "?", backstop.first_words(redact(text)))
        if not await self.check_group() or not self.authorised(mid):   # the latch may have come meanwhile
            self.deny(mid, "message")
            return
        if self.store.get("operator_seen_at") is None:
            with self.store.transaction():      # the marker and the notice: both or neither
                self.store.set("operator_seen_at", now())
                self.post("ready", READY_NOTICE, None)
        target = ev.reply_to.message_id_hex.lower() if ev.reply_to is not None else None
        await self.handle(mid, text, target)

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

    async def handle(self, mid: str, text: str, target: str | None = None) -> None:
        # Control characters are refused before anything else: they can break out of bracketed paste,
        # and a command name or argument holding one must never be parsed or echoed (plan decision D3).
        if commands.has_control_chars(text):
            self.finish(mid, "dropped", CONTROL_REFUSED, "refused")
            return
        ask_id = self.store.ask_for_message(target)
        if ask_id is not None and not text.startswith("!"):
            await self.ask_reply(mid, ask_id, text)        # R13/R14: never pasted to the agent
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
            if cmd.name == "details":
                await self.details(mid, cmd, target)
                return
            if cmd.name in ("interrupt", "new"):   # these recheck once they hold the dispatch lock
                await (self.interrupt(mid, cmd) if cmd.name == "interrupt" else self.new_session(mid, cmd))
                await self.flush()
                return
            if not self.authorised(mid):
                self.deny(mid, "command")
                return
            if cmd.name in ("approve", "deny"):
                await self.decide(mid, cmd, target)
                return
            if cmd.name == "asks":
                await self.list_asks(mid)
                return
            if cmd.name == "answer":
                self.bang_answer(mid, cmd)
                return
            result, _ = await self.execute(cmd)
            self.audit.write("command", message_id=mid, command=cmd.name,
                             arg=None if cmd.arg is None else own_text(cmd.arg), result_chars=len(result))
            self.finish(mid, "done", result, "cmd")
            await self.flush()
            return
        self.held.append((mid, text))
        await self.flush()

    async def details(self, mid: str, cmd: commands.Command, target: str | None) -> None:
        """`!details [full]`: the full redacted reply (or every reply of a batch), each under its origin,
        in lane 2 and threaded to the command (ADR 0001 §8, revision 13). Rendering stops at a budget of
        min(MAX_REPLY, MAX_DETAILS_PARTS * chunk_chars) UTF-8 bytes, with a notice (B13). It is a budget, not
        a hard cap: it is checked before the final redaction (which can expand the text), a stored reply is
        shown whole and may exceed it, and the number of parts is cut separately at MAX_DETAILS_PARTS. All of
        one command's transcript reads share one time budget, `details_timeout` (DETAILS_READ_SECONDS); a turn
        it ran out before says it was not read."""
        if not self.authorised(mid):
            self.deny(mid, "command")
            return
        ask_id = self.store.ask_for_message(target)
        if ask_id is not None:
            self.ask_details(mid, ask_id)
            return
        post = self.store.details_target(target)
        if post is not None and post.turn_id is not None:
            ids = [post.turn_id]
        else:
            ids = [] if post is None else self.store.batch_turn_ids(post.batch_id or 0)
        if not ids:
            why = ("that message has no details. Reply to a summary or a batch." if target is not None
                   else "there is no delivered summary or batch yet.")
            self.finish(mid, "done", f"!details: {why}", "cmd")
            return
        full = cmd.arg == "full"
        sections: list[str] = []
        # Deviation from the plan, which gave each turn its own 30 s: one `!details full` has ONE budget,
        # `details_timeout`, for all its reads. A batch can hold any number of turns, and 30 s each would
        # hold the work lock (operator messages, !interrupt, membership transitions) for 30 s x N.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.details_timeout
        # Deviation from the plan (extends B13): what `!details` renders is capped at MAX_REPLY, the same
        # 64 MiB of UTF-8 as a reply, however many legal records or turns there are. A tool call or result
        # is never shown in part: the first that would pass the cap, and everything after it, is replaced
        # by TRUNCATED. A stored reply is shown whole (it is already bounded by MAX_REPLY), so the text can
        # pass the cap by one reply. The cap counts the rendered text before redaction, which can expand it.
        # Also part of that extension: the cap is min(MAX_REPLY, MAX_DETAILS_PARTS * chunk_chars), and the
        # parts themselves are cut at MAX_DETAILS_PARTS below, so the publish and the cleanup are each one
        # statement over at most 20k rows (about 40 ms) however the text was built. A batch's replies are
        # loaded one at a time as they are rendered, and none past the cap is loaded at all.
        cap = min(MAX_REPLY, MAX_DETAILS_PARTS * self.s.chunk_chars)
        notice = details_notice(cap)
        left_bytes = cap
        shown = 0
        for turn_id in ids:
            if left_bytes <= 0:
                sections.append(notice)
                break
            row = self.store.turn(turn_id)
            if row is None:
                continue
            shown += 1
            section = f"{row.origin}\n{row.text}"      # a stored reply: shown whole, even past the cap
            left_bytes -= utf8_size(section) + 2
            cut = False
            if full:
                left = deadline - loop.time()
                calls, cut = (await self.tool_calls(row, left, left_bytes) if left > 0
                              else (DETAILS_NOT_READ, False))
                left_bytes -= utf8_size(calls) + 2
                section += "\n\n" + calls
            sections.append(section)
            if cut:
                sections.append(notice)
                break
        # whole text redacted and chunked in a thread: it may be huge and nothing is cut (B1, B13)
        def prepare() -> list[str]:
            text = redact("\n\n".join(sections))
            sections.clear()        # one full copy at a time
            out = chunk.split(text, self.s.chunk_chars)
            if len(out) > MAX_DETAILS_PARTS:    # a long stored reply, or redaction that grew the text
                del out[MAX_DETAILS_PARTS:]
                out.append(notice)
            return out

        parts = await asyncio.to_thread(prepare)
        # Deviation from the plan (Codex T9 r1): the parts are staged, in slices with a yield between, in a
        # table delivery ignores; one statement then makes them all visible. Inserting 335k rows (64 MiB
        # at 200 chars) in one transaction would stop the loop, alerts and latching included. The guards
        # are checked after every slice and again just before the publish, and a failed or cancelled
        # command discards what it staged. `parts` are already redacted whole, so they aren't redacted again.
        published = False
        try:
            for first in range(0, len(parts), DETAILS_SLICE):
                if not self.authorised(mid):
                    self.deny(mid, "command")
                    return
                self.store.stage_details(mid, first, parts[first:first + DETAILS_SLICE])
                await asyncio.sleep(0)
            if not self.authorised(mid):
                self.deny(mid, "command")
                return
            with self.store.transaction():
                self.store.set_inbound(mid, "done")
                self.store.publish_details(mid, f"details:{mid}", mid, 2)
            published = True
            self.wake.set()
        finally:
            if not published:
                self.store.discard_details(mid)
        self.audit.write("command", message_id=mid, command="details", full=full, replies=shown,
                         chars=sum(len(p) for p in parts))

    async def tool_calls(self, row: TurnRow, seconds: float, limit: int) -> tuple[str, bool]:
        """One turn's tool calls for `!details full`, read in the shared slot (B20), within `seconds`."""
        if row.transcript is None or row.transcript_start is None or row.transcript_end is None:
            return NO_TOOL_CALLS, False
        path = Path(row.transcript)
        if path.name != f"{row.session}.jsonl" or not path.is_absolute():
            return NO_TOOL_CALLS, False
        cancel = threading.Event()
        token = READ_CANCEL.set(cancel)         # the worker thread inherits it; it stops if we give up
        try:
            out = await self.bounded_read(seconds, render_tool_calls, path, row.transcript_start,
                                          row.transcript_end, max(limit, 0))
        except BaseException:
            cancel.set()
            raise
        finally:
            READ_CANCEL.reset(token)
        if out is None:
            cancel.set()
            return DETAILS_BUSY, False
        return out

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
            # The turn's first byte, measured before the lock (B19): the transcript only grows, so a size
            # taken later could skip the turn's own first records.
            start = await self.transcript_offset(ev) if ev.hook_event_name == "UserPromptSubmit" else None
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
                    self.on_prompt(ev, arrival, start)

    def on_prompt(self, ev: HookEvent, arrival: int | None = None, start: int | None = None) -> None:
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
        with self.store.transaction():      # the busy period, its transcript start and the anchor: one step
            self.set_busy()                 # a turn is running, whoever started it
            if start is None:
                self.store.delete("turn_start")
            else:
                self.store.set("turn_start", f"{self.store.get('busy')}:{start}")
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

    async def bounded_read[T](self, timeout: float, fn: Callable[..., T], *args: object) -> T | None:
        """Run one transcript read in a thread, bounded: None if it took longer than `timeout`, or if an
        earlier read is still running. One slot for every transcript reader (extraction, offsets and
        `!details full`), so a stalled filesystem cannot pile up threads (B20). An abandoned thread runs to
        completion in the background."""
        if self._reader is not None and not self._reader.done():
            return None
        task = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        self._reader = task
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())  # never "not retrieved"
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            return None

    async def transcript_offset(self, ev: HookEvent) -> int | None:
        return await self.bounded_read(self.offset_timeout, transcript_size, ev)

    async def extract(self, ev: HookEvent, start: int | None, end: int | None) -> str | None:
        """The reply of a Stop without its own text, read from the turn's span (B19) in the reader slot.
        None if it can't be read in time or at all; the caller then sends `EXTRACT_FAILED` to the
        backstop (B9)."""
        cancel = threading.Event()
        token = READ_CANCEL.set(cancel)         # the worker thread inherits it; it stops if we give up
        try:
            raw = await self.bounded_read(self.extract_timeout, reply_text, ev, start, end)
        except BaseException:
            cancel.set()                        # cancelled or failed: nobody will use the result
            raise
        finally:
            READ_CANCEL.reset(token)
        if raw is None:
            cancel.set()
        return raw

    async def on_stop(self, ev: HookEvent, arrival: int | None = None, stale: bool = False,
                      validate: bool = False) -> None:
        """A Stop ends a turn only if admind has already processed the prompt that anchored it (or no
        reservation is in flight at all: a turn begun at the terminal, or after a lost prompt hook). A Stop
        that finds a reservation in flight but unanchored, that is stale (an earlier launch, or accepted
        before the turn was released) changes no turn state: its own text is posted top-level, otherwise
        nothing is and the record says so."""
        end = await self.transcript_offset(ev)      # the turn's bytes end here, before a later turn can write
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
            span = self.turn_span(identity[3], end)     # the turn's bytes, known before the read
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
        raw: str | None = own
        if need_fallback:
            raw = await self.extract(ev, *span)     # may read the transcript file (lock not held)
            if raw is None:
                self.audit_quietly("agent", action="reply-extraction-failed")
        text, mode = EXTRACT_FAILED, "backstop"
        if raw is not None:
            prepared = await asyncio.to_thread(prepare_reply, raw, self.s.reply_verbatim_lines,
                                               self.s.reply_verbatim_chars)
            if prepared is not None:
                text, mode = prepared
            else:
                self.audit_quietly("agent", action="reply-extraction-failed")
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
            reply_to = anchor if current else None
            start, stop = span if current else (None, None)
            origin = self.origin_for(reply_to)
            parts = chunk.split(text, self.s.chunk_chars) if mode == "verbatim" else []

            def record(how: str, body: str) -> int:
                """The reply's record, its posts or its route, and the turn's end: one transaction."""
                with self.store.transaction():
                    reply_seq = int(self.store.get("reply_seq") or "0") + 1
                    self.store.set("reply_seq", str(reply_seq))
                    key = f"reply:{ev.session_id}:{reply_seq}"
                    turn_id = self.store.add_turn(
                        key, ev.session_id, reply_to, origin, body, ev.transcript_path, start, stop,
                        "verbatim" if how == "verbatim" else "summarizing")
                    if how == "verbatim":
                        for i, part in enumerate(parts):
                            self.post(f"{key}:{i}", part, reply_to)
                            self.store.record_post(f"{key}:{i}", "verbatim", turn_id, None)
                    elif how == "backstop":
                        self.queue_backstop(turn_id)
                    if current:
                        self.end_turn(anchor, arrival)
                return turn_id

            try:
                turn_id = record(mode, text)
            except Exception as exc:  # noqa: BLE001 - the reply must still reach the operator (B9)
                self.audit_quietly("reply", action="record-failed", error=type(exc).__name__)
                mode, parts = "backstop", []
                if isinstance(exc, UnicodeEncodeError):
                    text = EXTRACT_FAILED           # text the database can't hold: never insert it again
                turn_id = record(mode, text)        # any other failure keeps the reply; if this fails
                                                    # too, hook_loop holds dispatch
            if mode == "summary":
                self.summary_wake.set()
            elif mode == "backstop":
                self.audit_quietly("backstop", action="queued", turn=turn_id)
            if not current:
                self.audit_quietly("agent", action="late-stop")    # fixed wording; nothing from the event
            # The agent's text goes to the operator's chat only; the audit log records its size.
            self.audit_quietly("reply", session=ev.session_id, reply_to=reply_to, chars=len(text),
                               chunks=len(parts), mode=mode)

    def turn_span(self, busy: str | None, end: int | None) -> tuple[int | None, int | None]:
        """The transcript bytes of the turn a current Stop ends (B19): from its UserPromptSubmit to the Stop.
        Unknown (both None) unless that prompt's mark belongs to the busy period the Stop was captured in."""
        mark = self.store.get("turn_start")
        if busy is None or end is None or mark is None:
            return None, None
        owner, _, offset = mark.partition(":")
        if owner != busy or not offset.isdigit() or int(offset) > end:
            return None, None
        return int(offset), end

    def end_turn(self, anchor: str | None, arrival: int | None) -> None:
        """A current Stop's effects on turn state. Store calls and in-memory flags only (the caller's
        transaction); repeating them after a rollback is harmless."""
        if anchor is not None:
            self.store.delete("anchor")
            self.store.delete("in_flight")
            self.store.delete("in_flight_text")
        self.store.delete("turn_start")
        self.set_idle()                 # the turn ended; an unconfirmed in_flight still holds
        if arrival is not None:
            self.raise_floor(arrival)   # a prompt accepted before this Stop is that turn's

    def origin_for(self, mid: str | None) -> str:
        row = None if mid is None else self.store.prompt(mid)
        if row is None:
            return backstop.origin(None, now(), None)
        return backstop.origin(row.operator, row.received_at, row.words)

    def queue_backstop(self, turn_id: int) -> None:
        """Put a reply in the open backstop batch, opening one. Store calls only, in one transaction that
        joins the caller's; the caller audits after it commits."""
        with self.store.transaction():
            batch = self.store.open_batch(self.wallclock(), self.batch_seconds)
            self.store.add_to_batch(turn_id, batch)

    async def summary_loop(self) -> None:
        """Summarize every turn that waits for one (B9). Rows are read from the database on every pass, so
        a failure at any step leaves the row for the next pass, without a restart."""
        while True:
            self.summary_wake.clear()
            for row in self.store.turns_with_status("summarizing"):
                try:
                    await self.summarize_turn(row)
                except Exception as exc:  # noqa: BLE001 - the row stays `summarizing`; the next pass retries
                    self.audit_quietly("summary", action="pass-failed", turn=row.turn_id,
                                       error=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.summary_wake.wait(), self.summary_poll)

    async def summarize_turn(self, row: TurnRow) -> None:
        """Post a summary, or send the reply to the backstop. Either outcome is one transaction; if even the
        backstop's fails, the exception reaches summary_loop and the row is tried again."""
        try:
            text = await summarize.summarize(self.summarizer_argv, self.s.state_dir / "summarizer", row.text,
                                             self.summary_timeout)
            with self.store.transaction():
                for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                    self.post(f"{row.key}:s{i}", part, row.reply_to)
                    self.store.record_post(f"{row.key}:s{i}", "summary", row.turn_id, None)
                self.store.set_turn_status(row.turn_id, "summarized")
        except Exception as exc:  # noqa: BLE001 - any failure sends the reply to the backstop (§8)
            reason = exc.reason if isinstance(exc, summarize.SummaryFailed) else "internal"
            self.audit_quietly("summary", action="failed", reason=reason, turn=row.turn_id,
                               error=None if reason != "internal" else type(exc).__name__)
            self.queue_backstop(row.turn_id)
            self.audit_quietly("backstop", action="queued", turn=row.turn_id)
            return
        self.audit_quietly("summary", action="queued", turn=row.turn_id)

    async def batch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.batch_poll)    # not self._sleep: tests make that one return at once
            await self.close_due_batches()

    async def close_due_batches(self) -> None:
        """Post each batch whose window has passed, as one unthreaded message (B10)."""
        for batch, attempt in self.store.due_batches(self.wallclock(), self.batch_seconds):
            rows = self.store.batch_turns(batch)
            key = f"batch:{batch}.{attempt}"        # a new key per attempt: the outbox ignores a known key
            text = await asyncio.to_thread(render_batch, rows)
            with self.store.transaction():
                self.post(key, text, None)
                self.store.record_post(key, "batch", None, batch)
                self.store.close_batch(batch)
            self.audit_quietly("backstop", action="posted", batch=batch, replies=len(rows),
                               lines=len(text.splitlines()), chars=len(text))

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
        """A message admind gave up on. If it carried a reply or a summary, the reply goes to the backstop
        in the same transaction (B9). If it was a batch, the batch opens again in the same transaction and
        is posted after a new window under a new key, so its replies are never stranded (B10); this
        repeats until it is delivered, and the audit records each attempt."""
        queued = reopened = None
        with self.store.transaction():
            self.store.mark_failed(seq)
            post = self.store.post_record(key)
            turn = None if post is None or post.turn_id is None else self.store.turn(post.turn_id)
            if turn is not None and turn.status in ("verbatim", "summarized"):
                self.queue_backstop(turn.turn_id)
                queued = turn.turn_id
            if post is not None and post.kind == "batch" and post.batch_id is not None:
                self.store.reopen_batch(post.batch_id, self.wallclock())
                reopened = post.batch_id
        if queued is not None:
            self.audit_quietly("backstop", action="queued", turn=queued, why="send-failed")
        if reopened is not None:
            self.audit_quietly("backstop", action="reopened", batch=reopened, why="send-failed")

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
