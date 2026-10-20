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
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import msgspec

from heterodyne.admind import (
    alerts,
    approvals,
    asks,
    backstop,
    binding,
    chunk,
    commands,
    ctl,
    fulfillment,
    gateway_custody,
    gateway_custody_control,
    guard,
    guard_state,
    membership,
    notices,
    operations,
    recovery,
    summarize,
    verbs,
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
from heterodyne.admind.notices import NoticePost
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import AdmindSettings, Operator
from heterodyne.admind.store import (
    MAINTENANCE_CAPACITY,
    MAINTENANCE_RETRY_SECONDS,
    MAINTENANCE_SUSPENDED,
    REACTION_PREFIX,
    AskRow,
    OutboxRow,
    Store,
    TurnRow,
    now,
)
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
DECIDE_HOW = "React 👍 to approve or 👎 to deny, or reply approve / deny <reason>."
NOTED = "Noted on ask {ask_id}; this is not a decision. " + DECIDE_HOW
NOT_A_CARD_REPLY = "To decide, react to the approval card or reply to it. Nothing recorded."
MISMATCH = "This card is for {bead}. " + DECIDE_HOW + " Nothing recorded."
NOT_DELIVERED = ("Ask {ask_id} has not been fully delivered yet. Wait for every part, then react or reply "
                 "again. Nothing recorded.")
NEEDS_DETAILS = "Ask {ask_id} was shortened. Reply !details to it and read it first. Nothing recorded."
REASON_TOO_LONG = f"Not recorded: a deny reason is at most {commands.MAX_REASON:,} characters."
APPROVAL_NOT_ANSWERED = ("Ask {ask_id} is an approval ask. To decide, react to its card or reply to it. "
                         "Nothing recorded.")
# R29: a stale card's settlement. `why` is a post refusal without its closing full stop.
STALE_REFRESHED = ("{bead} changed after this card was posted (shown {shown}, now {now}). Nothing recorded. "
                   "A fresh card follows: ask {new}.")
STALE_NOT_REFRESHED = ("{bead} changed after this card was posted (shown {shown}, now {now}). Nothing "
                       "recorded, and admind could not post a fresh card: {why}.")
UPDATED_NOT_SHOWN = " Its updated content is not shown: {why}."
NOT_APPROVER = "{name} is not a btq approver. Nothing recorded."
# btq-ryfxf: `why` is the gate preview's reasons, redacted
GATE_REFUSED = ("btq's gate would reject approving {bead} now: {why}. Nothing recorded. Fix the bead on the "
                "host, or deny it.")
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
BUMP_HINT = ("That was a reminder. React or reply on ask {ask_id}'s card (the message the reminder replies "
             "to).")
CARD_COMMANDS = (["approve"], ["deny"], ["details"])     # what B6 intercepts on a bump, as parse names them
MAX_SEND_ATTEMPTS = 10
# A continuation part of a top-level post (K22): an ask card, a repeat or a notice, part 2 or later.
_CONTINUATION = re.compile(r"\A((?:ask|askr|notice):.+):[1-9][0-9]*\Z")
_PART = re.compile(r"\A↳ part ([0-9]+)/([0-9]+)\n")
PART_FAILED = "part {k}/{n} failed to deliver, ask the poster to repeat"      # Amendment 1 §2 (K22f)
AGENT_POLL = 5.0        # seconds between checks that the admin agent's pane is still alive
READY_TIMEOUT = 120.0   # seconds after a launch with no SessionStart before the agent is relaunched
AGENT_STUCK_NOTICE = "The admin agent is not running ({why}). Use !tail, then !new."
MESSAGE_ID = re.compile(r"[0-9a-f]{64}")
KNOWN_HOOKS = frozenset(HOOK_EVENTS)
EXTRACT_SECONDS = 10.0      # the transcript fallback's deadline (a stalled filesystem must not hold a slot)
OFFSET_SECONDS = 2.0        # a transcript size measurement's deadline (B19, B20)
SUMMARY_POLL = 5.0          # summary_loop re-reads the database at least this often (B9)
DETAILS_READ_SECONDS = 30.0     # `!details full`'s transcript read (B20)
AUTOBUMP_SECONDS = 300.0        # how often automatic bumps are checked (asks bump B14)
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
LOGIN_CHANGED_WHY = "the admin agent was restarted on a new session because its login changed"
# Fixed: it names no account and no path (D10).
LOGIN_CHANGED_NOTICE = ("The admin agent was restarted on a new session because its login changed. "
                        "Resend anything unanswered.")
SESSION_SOURCES = frozenset({"startup", "resume", "clear", "compact"})   # Claude Code's SessionStart sources


@dataclass
class HookRun:
    """The event hook_loop is processing: its deadline (rescheduled when the turn lock is taken, so only
    processing counts) and whether it was decided to be a no-op (stale, or outside the session)."""
    deadline: asyncio.Timeout | None = None
    noop: bool = False


@dataclass(frozen=True)
class FreshCard:
    """An approval ask prepared for posting (spec §8 "Post", R29): its row and its checked card. Nothing
    is stored until `commit_approval`."""
    row: AskRow
    card: asks.ApprovalCard


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


def canonical_ref(mid: str) -> str:
    """The one reference to an inbound message or reaction, in the audit and in `via_ref` (R20, R27): the
    `ref_id` of its bare 64-hex event ID, never of a reaction's `r:` key."""
    return ref_id(mid.removeprefix(REACTION_PREFIX))


def pinned_only(r: Readout) -> bool:
    """Is btq's stored pin the only reason `postable` refuses `r` (R29: the updated content is delivered,
    marked undecidable)?"""
    if r.posted_digest is None or r.posted_digest == r.digest:
        return False
    return approvals.postable(msgspec.structs.replace(r, posted_digest=None)) is None


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
    """Preserve confirmed keys through rearm, intersected with fresh policy.

    A count never settles pending RPCs. Only a pre-confirmed-set migration may use
    the operator's explicit rearm assertion that all policy operators are present.
    """
    eligible = {o.hex for o in policy}
    if confirmed is not None:
        return confirmed & eligible, "confirmed"
    # Explicit operator rearm assertion only for a pre-confirmed-set migration.
    if count == 1 + len(eligible):
        return eligible, "policy"
    return None, "unknown"


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
        store.fulfillment.notice_authority = self.fulfillment_notice_authority
        self.audit = audit
        self.agent = agent
        self.runner = runner
        self.account = account
        # Ports deliberately absent until a separately qualified source/transport
        # enrollment exists. Merely exposing the closed socket creates no authority.
        self.gateway_custody = gateway_custody.Service(store)
        self.custody_lock = asyncio.Lock()
        self.custody_work: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=256)
        try:
            self.maintenance_recovery: recovery.Service | None = recovery.Service(
                store, settings.state_dir / "maintenance-recovery")
        except (recovery.Invalid, OSError, sqlite3.Error):
            self.maintenance_recovery = None
            self.audit.write("maintenance-recovery", action="feature-unavailable")
        self.group = group
        self.hooks: asyncio.Queue[HookEvent | Delivery] = asyncio.Queue()
        # operator messages and reactions for worker_loop
        self.work: asyncio.Queue[InboundMessage | ReactionAdded] = asyncio.Queue()
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
        self.autobump_seconds = AUTOBUMP_SECONDS            # replaced in tests
        self.batch_poll = 1.0                               # replaced in tests
        self.wallclock: Callable[[], float] = time.time     # replaced in tests; batches outlive a restart
        self.group_ok = False
        self.observing = False      # a membership subscription is confirmed active (acked, then verified)
        self.acked = False          # the current subscription was acknowledged; check_group may then observe
        self.sub_gen = 0            # bumped at the start and end of every subscription attempt
        self.wake = asyncio.Event()
        self.store.fulfillment.post = lambda key, text, target, thread, owners: self.post(
            key, text, target, thread_key=thread, owner=owners)
        self.send_lock = asyncio.Lock()     # held across each send; a membership transition drains it (B7)
        self.count_lock = asyncio.Lock()        # never acquire transition/send while held
        self.check_requested = False
        self.check_task: asyncio.Task[None] | None = None
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
        self.ask_server: ctl.JsonSocketServer[
            asks.AskRequest | fulfillment.Request | operations.Request,
            asks.AskReply | fulfillment.Reply | operations.Reply] | None = None
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
        """The RPC-confirmed operator keys; None before the plan-2 migration."""
        raw = self.store.get("group_operators")
        if raw is None:
            return None
        value: object = json.loads(raw)
        if not isinstance(value, list):
            raise ValueError("malformed confirmed authority")
        values = cast(list[object], value)
        if any(not isinstance(k, str) or not MESSAGE_ID.fullmatch(k) for k in values):
            raise ValueError("malformed confirmed authority")
        return cast(set[str], set(values))

    def authorise(self, confirmed: Iterable[str]) -> None:
        """Who may command admind: the policy operators whose key is confirmed in the group."""
        keys = set(confirmed)
        self.operators = {o.hex: o.name for o in self.policy_operators if o.hex in keys}

    def refresh_authority(self) -> bool:
        """No stale-policy fallback. Run synchronously at ingress and each side-effect boundary."""
        try:
            policy = self.load_operators()
            confirmed = self.confirmed_operators()
            if confirmed is None:
                self.operators = {}
                return False
            self.policy_operators = policy
            self.authorise(confirmed)
            return True
        except Exception:  # noqa: BLE001 - unreadable/invalid policy closes only this gate
            self.operators = {}
            self.audit_quietly("guard", action="policy-unreadable")
            return False

    def fulfillment_notice_authority(self) -> set[str] | None:
        """Pure readback for notice diagnostics; no group RPC or guard-state mutation."""
        try:
            confirmed = self.confirmed_operators()
            if confirmed is None:
                return None
            return confirmed & {o.hex for o in self.load_operators()}
        except (OSError, ValueError):
            return None

    def request_owners(self, mid: str | None) -> tuple[str, ...]:
        sender = None if mid is None else self.store.inbound_sender(mid)
        return () if sender is None else (sender,)

    def batch_owners(self, rows: list[TurnRow]) -> tuple[str, ...]:
        owners = [self.request_owners(row.reply_to) for row in rows]
        return tuple(sorted({k for group in owners for k in group})) if all(owners) else ()

    def ownership_for_key(self, key: str) -> tuple[str, ...] | None:
        row = self.store.db.execute("SELECT seq, ownership_kind FROM outbox WHERE key = ?", (key,)).fetchone()
        if row is None:
            return ()
        return None if row[1] == "system" else self.store.row_owners(int(row[0]))

    def expected_members(self) -> int:
        """The trusted member count (B3). Absent only before the startup migration has run."""
        return int(self.store.get("expected_members") or "2")

    def latched(self) -> bool:
        return self.store.get("latched") is not None

    def latch(self, why: str) -> None:
        """`why` is built by admind from numbers and allowlisted change names, never from peer text."""
        if not self.latched():
            with self.store.transaction():
                self.store.set("latched", why)
                self.store.set("maintenance_latch_token", uuid.uuid4().hex)
            # Commit the safety latch before best-effort overlay suspension. Failure below must
            # never roll back the latch; no await allows rearm between these synchronous writes.
            with self.store.transaction():
                for ask_id, mid, due in self.store.maintenance_pending():
                    attempt = self.store.current_attempt(ask_id)
                    if attempt is not None and attempt.message_id == mid:
                        self.store.defer_maintenance(attempt, due, reason="latched")
            self.audit.write("guard", action="latch", reason=why)

    def may_post(self) -> bool:
        """Outbound gate, checked immediately before every send: not latched, the operator has been
        seen in the group (D5), the last membership check succeeded and the membership subscription is
        confirmed active. A count check alone never counts: with nobody watching, a swap goes unseen."""
        return (self.refresh_authority() and not self.latched() and self.group_ok and self.observing
                and not self.changing and self.store.get("operator_seen_at") is not None)

    def sender_current(self, mid: str) -> bool:
        """Is the sender of message `mid` still an authorised operator? A latch cleared by `rearm` may have
        left it out. Fails closed: a message with no recorded sender is not current."""
        sender = self.store.inbound_sender(mid)
        return self.refresh_authority() and sender is not None and sender in self.operators

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
        return (self.refresh_authority() and not self.latched() and self.group_ok and self.observing
                and not self.changing and (mid is None or self.sender_current(mid)))

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

    def post(self, key: str, text: str, reply_to: str | None, lane: int = 1,
             thread_key: str | None = None, *, owner: tuple[str, ...] | None) -> None:
        """Queue one message. Everything admind posts is redacted here (B1); callers that chunk redact the
        whole text first, so a value can't escape redaction by straddling a chunk boundary. A continuation
        chunk names its chunk 0 as `thread_key`, resolved to a reply target when it is sent (K22)."""
        if self.store.enqueue(key, redact(text), reply_to, lane, thread_key,
                              ownership_kind="system" if owner is None else "request" if owner else "unknown",
                              owners=owner or (),
                              delivery_group=guard_state.legacy_group(key, thread_key)):
            self.wake.set()

    def post_parts(self, prefix: str, parts: list[str], lane: int = 1, *,
                   owner: tuple[str, ...] | None) -> None:
        """A top-level post in parts: chunk 0 top-level, chunks 1.. threaded under it when sent (K22)."""
        for i, part in enumerate(parts):
            self.post(f"{prefix}:{i}", part, None, lane, None if i == 0 else f"{prefix}:0", owner=owner)

    def reply(self, mid: str, text: str, tag: str) -> None:
        """Answer inbound `mid` in its thread: a message's own, a reaction's reacted message (delta §5). A
        reaction with no target gets no reply, rather than a top-level one."""
        target = self.store.reply_target(mid)
        if target is None:
            return
        for i, part in enumerate(chunk.split(redact(text), self.s.chunk_chars)):
            self.post(f"{tag}:{mid}:{i}", part, target, owner=self.request_owners(mid))

    # --- lifecycle -----------------------------------------------------------------------------
    def recover(self) -> None:
        """Settle what a previous run left half done (D6). Idempotent."""
        self.store.fulfillment.rebuild()  # roll-forward index before serving any socket or worker
        # A turn in flight when admind stopped can't be tied to its Stop any more (it may have been
        # lost, or arrive later). Close it out; a late Stop then has no anchor and posts top-level.
        self.abandon_in_flight("admind restarted during this turn; a late reply may appear unthreaded")
        self.store.discard_details()    # a !details in preparation died with the process
        # Notices whose delivery ended a week ago (K7); posting a notice prunes too.
        self.store.prune_notices(asks.stamp(asks.now() - timedelta(days=notices.NOTICE_RETENTION_DAYS)))
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
                    target = self.store.reply_target(mid)       # a reaction's is the reacted message
                    if target is not None:
                        self.post(f"restarted:{mid}", RESTARTED_NOTICE, target,
                                  owner=self.request_owners(mid))
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
        recovery_server: ctl.JsonSocketServer[recovery.Request, recovery.Reply] | None = None
        custody_server: (
            ctl.JsonSocketServer[gateway_custody.Request, gateway_custody_control.Reply] | None
        ) = None
        try:
            await server.start()
            control = ctl.CtlServer(self.s.state_dir / ctl.CTL_SOCKET, self.on_ctl, self.audit)
            await control.start()
            recovery_server = ctl.JsonSocketServer(
                self.s.state_dir / recovery.SOCKET,
                self.on_recovery,
                self.audit,
                kind="maintenance-recovery",
                max_request=recovery.MAX_BYTES,
                max_reply=recovery.MAX_BYTES,
                request_type=recovery.Request,
                check=recovery.check,
                refused=lambda why: recovery.Reply("refused", why),
                failed=lambda: recovery.Reply("unavailable", "recovery unavailable"),
                describe=lambda req: {"op": type(req).__name__},
            )
            if self.maintenance_recovery is not None:
                await recovery_server.start()
            custody_server = ctl.JsonSocketServer(
                self.s.state_dir / gateway_custody_control.SOCKET, self.on_gateway_custody, self.audit,
                kind="gateway-custody", max_request=gateway_custody.MAX_REQUEST,
                max_reply=gateway_custody.MAX_REQUEST, request_type=gateway_custody.Request,
                check=gateway_custody.check,
                refused=lambda why: gateway_custody_control.Reply("refused", why),
                failed=lambda: gateway_custody_control.Reply("unavailable", "custody unavailable"),
                describe=lambda req: {"op": type(req).__name__},
            )
            await custody_server.start()
            self.ask_server = ctl.JsonSocketServer(
                self.s.state_dir / asks.ASK_SOCKET, self.on_ask_wire, self.audit, kind="ask",
                max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY,
                request_type=asks.AskRequest | fulfillment.Request | operations.Request,
                check=self.check_ask_wire,
                refused=asks.refused, failed=asks.failed,
                describe=lambda req: (asks.describe(req) if isinstance(req, asks.AskRequest) else
                    {"op": type(req).__name__}))
            await self.ask_server.start()
            await self.start_agent(startup=True)
            async with asyncio.TaskGroup() as tg:
                for name, loop in (("inbound", self.inbound_loop), ("worker", self.worker_loop),
                                   ("hooks", self.hook_loop), ("outbox", self.outbox_loop),
                                   ("alerts", self.alerts_loop), ("group", self.group_loop),
                                   ("agent", self.agent_loop), ("summaries", self.summary_loop),
                                   ("batches", self.batch_loop), ("autobump", self.autobump_loop),
                                   ("maintenance-retry", self.maintenance_retry_loop)):
                    tg.create_task(self.guarded(supervised(name, loop, self.audit)))
                # Verification is independent of whether producers are enabled.
                from heterodyne.admind.fulfillment_projection import worker
                tg.create_task(self.guarded(supervised(
                    "fulfillment-verification", lambda: worker(self.store), self.audit, base=30, cap=30
                )))
                tg.create_task(self.guarded(supervised("gateway-custody", self.custody_loop, self.audit)))
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
            if recovery_server is not None:
                await recovery_server.close()
            if custody_server is not None:
                await custody_server.close()
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

    def recovery_authority(self, sender: str | None = None) -> str | None:
        """Actual current host policy/confirmed-set observation, never local request claims."""
        if not self.authorised() or (sender is not None and sender not in self.operators):
            return None
        return recovery.sha(
            {
                "policy": sorted((o.hex, o.name) for o in self.policy_operators),
                "confirmed": sorted(self.confirmed_operators() or ()),
            }
        )

    async def custody_authority(self, account: str, group: str, sender: str) -> str | None:
        if (account != self.account or group != self.group or not self.refresh_authority()
                or not await self.check_group()):
            return None
        return self.recovery_authority(sender)

    async def on_gateway_custody(
        self, req: gateway_custody.Request, peer: ctl.Peer,
    ) -> gateway_custody_control.Reply:
        """Own lock and worker: custody control cannot wait for a held model dispatcher."""
        try:
            gateway_custody.Service.peer(peer.uid)
            if why := gateway_custody.check(req):
                raise gateway_custody.Invalid(why)
            async with self.custody_lock:
                if not isinstance(req, gateway_custody.GatewayCustodyStatus):
                    if (not self.refresh_authority() or not await self.check_group()
                            or self.recovery_authority() is None):
                        raise gateway_custody.Invalid("authority")
                service = gateway_custody_control.independent(self.gateway_custody, self.custody_authority)
                result = await gateway_custody_control.worker(lambda: gateway_custody_control.perform(
                    service, req, uid=peer.uid, max_chars=self.s.chunk_chars))
                self.wake.set()
                return result
        except gateway_custody.Invalid as exc:
            return gateway_custody_control.Reply("refused", str(exc))
        except (OSError, sqlite3.Error):
            return gateway_custody_control.Reply("unavailable", "custody source or store unavailable")

    async def custody_message(self, ev: InboundMessage) -> None:
        """Native event only. It never joins generic prompt execution or ask slots."""
        try:
            async with self.custody_lock:
                service = gateway_custody_control.independent(self.gateway_custody, self.custody_authority)
                outcome = await gateway_custody_control.worker(lambda: service.message(
                    account=ev.account_id_hex.lower(), group=ev.group_id_hex.lower(),
                    mid=ev.message.message_id_hex.lower(), sender=ev.message.sender.account_id_hex.lower(),
                    target=None if ev.reply_to is None else ev.reply_to.message_id_hex.lower(),
                    text=ev.message.text))
                self.audit.write("gateway-custody", action=outcome)
        except gateway_custody.Invalid as exc:
            self.audit.write("gateway-custody", action="refused", reason=str(exc))
        except (OSError, sqlite3.Error):
            self.audit.write("gateway-custody", action="unavailable")

    async def custody_loop(self) -> None:
        while True:
            event = await self.custody_work.get()
            try:
                await self.custody_message(event)
            finally:
                self.custody_work.task_done()

    async def on_recovery(self, req: recovery.Request, peer: ctl.Peer) -> recovery.Reply:
        try:
            service = self.maintenance_recovery
            if service is None:
                return recovery.Reply("unavailable", "recovery unavailable")
            if peer.uid != os.getuid():
                raise recovery.Invalid("peer")
            if why := recovery.check(req):
                raise recovery.Invalid(why)
            if isinstance(req, recovery.Status):
                return recovery.Reply(
                    "ok", "recovery status", service.get(req.proposal_sha256)
                )
            async with self.work_lock:
                if not await self.check_group() or self.recovery_authority() is None:
                    raise recovery.Invalid("authority")
                # Hash the bounded source closure off the subscription event loop. Keep
                # work_lock until the worker has actually stopped, including cancellation.
                loop = asyncio.get_running_loop()

                async def observe_authority(sender: str) -> str | None:
                    # Policy refresh and membership state belong to the daemon loop,
                    # never to the source-hashing worker. work_lock is still held.
                    return self.recovery_authority(sender)

                def authority(sender: str) -> str | None:
                    pending = asyncio.run_coroutine_threadsafe(observe_authority(sender), loop)
                    try:
                        return pending.result(timeout=5)
                    except TimeoutError:
                        pending.cancel()
                        raise recovery.Invalid("authority-unavailable") from None

                def perform() -> recovery.Reply:
                    if isinstance(req, recovery.Register):
                        def post(key: str, text: str) -> None:
                            self.store.enqueue(key, text, None, lane=2, ownership_kind="system")
                            loop.call_soon_threadsafe(self.wake.set)
                        return service.register(
                            req, uid=peer.uid, pid=peer.pid,
                            chunk_chars=self.s.chunk_chars, post=post,
                        )
                    return service.permit(
                        req, uid=peer.uid, authority=authority,
                    )

                task = asyncio.create_task(asyncio.to_thread(perform))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                        except Exception:  # worker failure must not replace cancellation
                            break
                    if not task.cancelled():
                        task.exception()
                    raise
        except recovery.Invalid as exc:
            return recovery.Reply("refused", str(exc))
        except (OSError, sqlite3.Error):
            return recovery.Reply("unavailable", "recovery store or enrollment unavailable")

    async def recovery_message(self, ev: InboundMessage) -> None:
        if (ev.account_id_hex.lower() != self.account.lower()
                or ev.group_id_hex.lower() != self.group.lower()):
            self.audit.write("drop", reason="maintenance account/group mismatch",
                             sender_prefix=ev.message.sender.account_id_hex[:8],
                             text_chars=len(ev.message.text))
            return
        service = self.maintenance_recovery
        if service is None or not await self.check_group():
            self.audit.write("maintenance-recovery", action="authority-unavailable")
            return
        sender = ev.message.sender.account_id_hex.lower()
        mid = ev.message.message_id_hex.lower()
        fields = recovery.MessageFields(
                      account=ev.account_id_hex.lower(), group=ev.group_id_hex.lower(),
                      mid=mid, sender=sender,
                      target=None if ev.reply_to is None else ev.reply_to.message_id_hex.lower(),
                      text=ev.message.text, authority=lambda: self.recovery_authority(sender))
        response_key = "maintenance-response:" + recovery.sha(
            [fields["account"], fields["group"], mid]
        )

        def post(text: str) -> None:
            self.post(response_key, text, mid, owner=(sender,))

        try:
            reply = service.message(**fields, post=post)
            self.audit.write("maintenance-recovery", action=reply.message)
        except recovery.Invalid as exc:
            self.audit.write("maintenance-recovery", action="refused", reason=str(exc))
            try:
                service.refusal(**fields, reason=str(exc), post=post)
            except (recovery.Invalid, OSError, sqlite3.Error):
                self.audit.write("maintenance-recovery", action="refusal-unavailable")
        except (OSError, sqlite3.Error):
            self.audit.write("maintenance-recovery", action="unavailable")

    # --- asks (relay spec) ----------------------------------------------------------------------
    @staticmethod
    def check_ask_wire(req: asks.AskRequest | fulfillment.Request | operations.Request) -> str | None:
        """Validate the selected closed protocol without weakening legacy DTO checks."""
        if isinstance(req, operations.Request):
            return operations.check(req)
        if isinstance(req, fulfillment.Request):
            return fulfillment.check(req)
        return asks.check(req)

    async def on_ask_wire(self, req: asks.AskRequest | fulfillment.Request | operations.Request,
                          peer: ctl.Peer) -> asks.AskReply | fulfillment.Reply | operations.Reply:
        if isinstance(req, operations.Request):
            return await self.on_operation(req, peer)
        if isinstance(req, fulfillment.Request):
            reply = await self.on_fulfillment(req, peer)
            if len(msgspec.json.encode(reply)) > fulfillment.MAX_REPLY:
                return fulfillment.Reply("unavailable", "lifecycle reply exceeds bound")
            return reply
        return await self.on_ask(req, peer)

    async def on_operation(self, req: operations.Request, peer: ctl.Peer) -> operations.Reply:
        return await operations.handle(self, req, peer)

    async def on_fulfillment(self, req: fulfillment.Request, peer: ctl.Peer) -> fulfillment.Reply:
        """Separate opt-in protocol. No long-lived daemon lock or external operation is involved."""
        if not self.s.fulfillment_enabled:
            return fulfillment.Reply("unavailable", "capability disabled")
        try:
            fulfillment.Lifecycle.require_uid(peer.uid)
            why = fulfillment.check(req)
            if why:
                return fulfillment.Reply("refused", why)
            lifecycle = self.store.fulfillment
            if isinstance(req, fulfillment.Inspect):
                return fulfillment.Reply("ok", "intent diagnostics", lifecycle.inspect())
            if isinstance(req, fulfillment.Get):
                with self.store.transaction(read_only=True):
                    view = lifecycle.view(req.ask_id)
                    if view["binding"] is None:
                        return fulfillment.Reply("unavailable", "binding-unavailable", view)
                    try:
                        lifecycle.require_projection()
                    except fulfillment.Invalid as exc:
                        return fulfillment.Reply("unavailable", str(exc), view)
                    pending = view["pending"]
                    message = (
                        "lifecycle"
                        if pending is None
                        else "notice-"
                        + lifecycle.notice_state(str(cast(dict[str, object], pending)["intent_id"]))[0]
                    )
                    return fulfillment.Reply("ok", message, view)
            if isinstance(req, fulfillment.Apply):
                return lifecycle.apply(
                    req.receipt, uid=peer.uid if peer.uid is not None else -1, pid=peer.pid
                )
            if isinstance(req, fulfillment.Propose):
                return lifecycle.propose(
                    req, uid=peer.uid if peer.uid is not None else -1, chunk_chars=self.s.chunk_chars
                )
            with self.store.transaction():
                return lifecycle.retire(req.ask_id, req.expect_intent, req.reason)
        except fulfillment.Invalid as exc:
            if isinstance(req, fulfillment.Get) and str(exc) != "missing":
                return fulfillment.Reply("unavailable", "binding-unavailable")
            return fulfillment.Reply("refused", str(exc))
        except (msgspec.DecodeError, UnicodeError, TypeError, ValueError):
            if isinstance(req, fulfillment.Get):
                return fulfillment.Reply("unavailable", "binding-unavailable")
            raise
        except sqlite3.Error:
            return fulfillment.Reply("unavailable", "store unavailable")

    async def on_ask(self, req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        """A request on ask.sock, already checked by `asks.check`. Get, list and cancel work while
        latched (R17); a post does not."""
        if isinstance(req, asks.AskBinding):
            ask_id = asks.normalise_id(req.ask_id)
            if ask_id is None:
                return binding.AskBindingReply("refused", "ask.binding.v1 unavailable",
                                               code="invalid-state")
            return await asyncio.to_thread(binding.read, self.store, ask_id, self.s.chunk_chars,
                                           self.load_operators)
        if isinstance(req, asks.AskPost):
            return await self.serialised_post(req, peer)
        if isinstance(req, NoticePost):
            async with self.ask_post_lock:
                return self.post_notice(req, peer)
        if isinstance(req, asks.AskList):
            rows = self.store.capacity_asks() + self.store.inactive_asks(20)
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
        if asks.management_check(req) is not None:
            self.audit.write("ask", action="refused", ask_kind=req.kind, poster=req.poster,
                             pid=peer.pid, reason="management-contract",
                             presentation=req.presentation, render_version=req.render_version)
            return asks.refused(asks.management.REFUSED)
        if self.latched():
            return asks.refused(ASK_LATCHED)
        if self.s.fulfillment_enabled:
            try:
                self.store.fulfillment.require_projection()
            except fulfillment.Invalid as exc:
                if str(exc) != "fulfillment-projection-unavailable":
                    return asks.refused(str(exc))
                # Damaged derived verification cannot suppress capacity. The
                # Store's conservative count keeps ordinary posting available.
        if self.store.capacity_count() >= asks.MAX_OPEN:
            return asks.refused(ASK_TOO_MANY)
        if self.store.ask_posted_since(asks.hour_ago()) >= asks.MAX_PER_HOUR:
            return asks.refused(ASK_TOO_OFTEN)
        at = asks.stamp(asks.now())
        question_form, choices = asks.stored_form(req)
        row = AskRow(asks.new_id(lambda i: self.store.ask(i) is not None), req.kind, req.poster, req.title,
                     req.body, req.pr_url, req.head_sha, req.bead, None, False, 0, "open", None, None, at, at,
                     question_form=question_form, choices=choices, links=asks.stored_links(req.links),
                     presentation=req.presentation, render_version=req.render_version,
                     management=(None if req.management is None
                                 else msgspec.json.encode(req.management).decode()))
        try:
            parts, truncated = asks.card_chunks(row, self.s.chunk_chars)
        except asks.management.Refusal:
            self.audit.write("ask", action="refused", ask_kind=row.kind, poster=row.poster,
                             pid=peer.pid, reason="management-contract",
                             presentation=row.presentation, render_version=row.render_version)
            return asks.refused(asks.management.REFUSED)
        except chunk.TooWide:
            self.audit.write("ask", action="refused", ask_kind=row.kind, poster=row.poster, pid=peer.pid,
                             reason="link too long")
            return asks.refused(asks.ASK_LINK_TOO_LONG)
        row = dataclasses.replace(row, truncated=truncated, card_parts=len(parts))
        try:
            with self.store.transaction():
                self.store.insert_ask(row, peer.pid)
                if asks.management.present(row):
                    saved = self.store.ask(row.ask_id)
                    expected = dataclasses.replace(row, last_activity_at=row.last_activity_at or at)
                    if saved is None or saved != expected:
                        raise asks.management.Refusal()
                    asks.management.stored(saved)
                    row = saved
                self.post_parts(f"ask:{row.ask_id}", parts, owner=None)
        except asks.management.Refusal:
            self.audit.write("ask", action="refused", ask_kind=row.kind, poster=row.poster,
                             pid=peer.pid, reason="management-readback",
                             presentation=row.presentation, render_version=row.render_version)
            return asks.refused(asks.management.REFUSED)
        self.audit.write("ask", action="posted", ask_id=row.ask_id, ask_kind=row.kind, poster=row.poster,
                         pid=peer.pid, parts=len(parts), truncated=truncated, title_chars=len(row.title),
                         body_chars=len(row.body), form=req.form, choices=len(req.choices),
                         links=len(req.links),
                         **({"presentation": row.presentation, "render_version": row.render_version}
                            if asks.management.present(row) else {}))
        message = f"ask {row.ask_id} posted"
        if row.kind == "question" and req.form is None:     # the K14 grace window
            message += "\n" + asks.FORM_WARNING
        if req.presentation is not None:
            return asks.ManagementReply("posted", message, ask=self.ask_view(row),
                                        presentation=row.presentation, render_version=row.render_version,
                                        management=asks.management.stored(row))
        return asks.AskReply("posted", message, ask=self.ask_view(row))

    def post_notice(self, req: NoticePost, peer: ctl.Peer) -> asks.AskReply:
        """Under `ask_post_lock` (K6): the latch, the K4 render and measure, the K5 counts, the `notices` row
        and every outbox row, in one transaction with no await. Never an ask (K2). Audited once, with a fixed
        `why` code on a refusal, never text (K9)."""
        fields: dict[str, object] = {"poster": req.poster, "peer_pid": peer.pid,
                                     "title_chars": len(req.title), "body_chars": len(req.body),
                                     "links": len(req.links)}
        try:
            result = self.admit_notice(req, peer)
        except Exception as exc:  # noqa: BLE001 - audited as `internal` by class name only (K9)
            self.audit.write("notice", action="refused", why="internal", error=type(exc).__name__, **fields)
            return asks.failed()
        if isinstance(result, str):
            self.audit.write("notice", action="refused", why=result, **fields)
            return asks.refused(notices.MESSAGES[result])
        notice_id, parts = result
        self.audit.write("notice", action="posted", notice_id=notice_id, parts=parts, **fields)
        return asks.AskReply("posted", f"posted {notice_id}")

    def admit_notice(self, req: NoticePost, peer: ctl.Peer) -> tuple[str, int] | str:
        """`post_notice`'s checks and writes: (notice ID, parts), or the K9 code it is refused for."""
        if self.latched():
            return "latched"
        admitted = notices.admit(req, self.s.chunk_chars)
        if isinstance(admitted, str):
            return admitted
        at = asks.now()
        with self.store.transaction():
            self.store.prune_notices(asks.stamp(at - timedelta(days=notices.NOTICE_RETENTION_DAYS)))
            if self.store.notices_since(asks.stamp(at - timedelta(hours=1))) >= notices.NOTICE_PER_HOUR:
                return "rate-hour"
            if self.store.undelivered_notices() >= notices.NOTICE_UNDELIVERED:
                return "undelivered"
            notice_id = notices.new_id(self.store.notice_exists)
            self.store.insert_notice(notice_id, req.poster, peer.pid, len(req.title), len(req.body),
                                     len(req.links), len(admitted.chunks), asks.stamp(at))
            self.post_parts(f"notice:{notice_id}", admitted.chunks, lane=2, owner=None)
        return notice_id, len(admitted.chunks)

    def approval_post_refusal(self, bead: str, retiring: tuple[str, str] | None = None) -> str | None:
        """Spec §8 "Post", steps 1 and 4 (R15, R17, R22): checked before the read and again after it. A
        refresh (R29) passes the stale ask it replaces as `retiring`, (ask ID, attempt): that exact pair is
        not "another ask for the bead being decided"; every other `deciding` or `uncertain` ask is."""
        if self.latched():
            return ASK_LATCHED
        if self.approve_bead is None:
            return ASK_NO_APPROVALS
        if self.store.capacity_count() >= asks.MAX_OPEN:
            return ASK_TOO_MANY
        if self.store.ask_posted_since(asks.hour_ago()) >= asks.MAX_PER_HOUR:
            return ASK_TOO_OFTEN
        for busy in reversed(self.store.asks_with_status("deciding", "uncertain")):    # the newest first
            if busy.bead == bead and not self.retiring(busy, retiring):
                return ASK_BEAD_BUSY.format(ask_id=busy.ask_id, bead=bead)
        return None

    def retiring(self, row: AskRow, retiring: tuple[str, str] | None) -> bool:
        """Is `row` the retiring ask, with exactly the retiring attempt as its current one (R29)?"""
        if retiring is None or row.ask_id != retiring[0]:
            return False
        a = self.store.current_attempt(row.ask_id)
        return a is not None and a.message_id == retiring[1]

    def approval_refused(self, bead: str, message: str, why: str) -> asks.AskReply:
        self.audit.write("ask", action="refused", ask_kind="approval", bead=bead, reason=why)
        return asks.refused(message)

    async def post_approval(self, req: asks.AskPost, peer: ctl.Peer) -> asks.AskReply:
        """Spec §8 "Post an approval ask", under `ask_post_lock` (R22): read the bead with approve-bead,
        prepare the card from its readout, then commit it in one transaction."""
        bead = req.bead or ""
        with self.store.transaction(read_only=True):
            if operations.bead_binding(self.store, bead) is not None:
                return asks.refused("source-owned bead; use approval.operation.v1")
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
        fresh = self.prepare_approval(req.poster, bead, r)
        if not isinstance(fresh, FreshCard):
            return self.approval_refused(bead, *fresh)
        with self.store.transaction():
            superseded = self.commit_approval(fresh, peer.pid)
        self.audit_posted(fresh, peer.pid, superseded)
        return asks.AskReply("posted", f"ask {fresh.row.ask_id} posted", ask=self.ask_view(fresh.row))

    def prepare_approval(self, poster: str, bead: str, r: Readout,
                         retiring: tuple[str, str] | None = None) -> FreshCard | tuple[str, str]:
        """Under `ask_post_lock`, with no await: the card for readout `r`, or (the refusal, its audit
        reason). `postable` (R4), the R21 redaction check and the R8 cap, then the latch, limits and
        same-bead checks again (the read awaited, R22). A refresh (R29) names the stale ask it replaces."""
        refusal = approvals.postable(r)
        if refusal is not None:
            return redact(refusal[0]), refusal[1]
        at = asks.stamp(asks.now())
        row = AskRow(asks.new_id(lambda i: self.store.ask(i) is not None), "approval", poster,
                     asks.approval_title(r, bead), "", None, None, bead, r.digest, False, 0, "open", None,
                     None, at, at, None if retiring is None else retiring[0])
        others = asks.count_others(o for o in self.store.capacity_asks()
                                   if o.bead != bead and (retiring is None or o.ask_id != retiring[0]))
        card = asks.approval_card(row, r, self.s.chunk_chars, others)
        if isinstance(card, str):
            return card, ("redaction" if card == ASK_REDACTED else "too long" if card == asks.TOO_LONG
                          else "link too long")
        refusal = self.approval_post_refusal(bead, retiring)
        if refusal is not None:
            return refusal, "limits"
        row = dataclasses.replace(row, body=asks.approval_body(r, card), card_parts=len(card.card_chunks))
        return FreshCard(row, card)

    def commit_approval(self, fresh: FreshCard, peer_pid: int | None, *,
                        operation_key: str | None = None) -> list[str]:
        """Inside the caller's transaction: supersede the bead's open and answered asks, store the fresh
        one and queue its card. Returns the superseded ask IDs."""
        row, bead = fresh.row, fresh.row.bead
        superseded: list[str] = []
        with self.store.transaction():
            bound_key = operations.before_commit(self.store, bead, operation_key)
            for old in self.store.asks_with_status("open", "answered"):
                if old.bead == bead and not self.store.fulfillment.protected(old.ask_id):
                    superseded.append(old.ask_id)
                    self.store.set_ask(old.ask_id, "superseded")
                    self.ask_notice(old.ask_id, "superseded", row.ask_id, ASK_SUPERSEDED_NOTICE.format(
                        old=old.ask_id, new=row.ask_id, bead=bead))
            self.store.insert_ask(row, peer_pid)
            if bound_key is not None:
                operations.attach(self.store, bound_key, row.ask_id)
            self.post_parts(f"ask:{row.ask_id}", fresh.card.card_chunks, owner=None)  # checked chunks
        return superseded

    def audit_posted(self, fresh: FreshCard, pid: int | None, superseded: list[str]) -> None:
        row, card = fresh.row, fresh.card
        self.audit.write("ask", action="posted", ask_id=row.ask_id, ask_kind=row.kind, poster=row.poster,
                         pid=pid, bead=row.bead, digest12=(row.digest or "")[:12],
                         parts=len(card.card_chunks), details_parts=len(card.details_chunks), truncated=False,
                         superseded=superseded, refreshed_from=row.refreshed_from)

    def cancel_ask(self, row: AskRow) -> asks.AskReply:
        if row.status not in ("open", "answered"):
            return asks.refused(f"ask {row.ask_id} is {row.status}; only an open or answered ask can be "
                                "cancelled")
        with self.store.transaction():
            if self.store.fulfillment.protected(row.ask_id):
                return asks.refused("already fulfilled; original answer retained; see fulfillment get")
            current = self.store.ask(row.ask_id)
            if current is None or current.status not in ("open", "answered"):
                return asks.refused("ask state changed; read it again")
            self.store.set_ask(row.ask_id, "cancelled")
            self.ask_notice(row.ask_id, "cancelled", "0", ASK_CANCELLED_NOTICE.format(ask_id=row.ask_id))
        self.audit.write("ask", action="cancelled", ask_id=row.ask_id)
        return asks.AskReply("ok", f"ask {row.ask_id} cancelled")

    def ask_notice(self, ask_id: str, what: str, ref: str, text: str, attempt: str | None = None) -> None:
        """A notice about an ask. One that answers an attempt is threaded to that attempt's reply target (the
        reply itself, or the chunk a reaction reacted to; delta §5); any other to the card's first sent chunk.
        Unthreaded if there is neither."""
        if attempt is not None:
            thread = self.attempt_thread(ask_id, attempt)
        else:
            thread = self.store.first_card_message(ask_id)
        for i, part in enumerate(chunk.split(redact(text), self.s.chunk_chars)):
            self.post(f"asknote:{ask_id}:{what}:{ref}:{i}", part, thread,
                      owner=self.request_owners(attempt) if attempt is not None else None)

    def attempt_thread(self, ask_id: str, attempt: str) -> str | None:
        """What a reply derived from attempt `attempt` threads to: its stored reply target, or, for a reaction
        stored without one, the card's first sent chunk. Never an `r:` key."""
        return self.store.reply_target(attempt) or self.store.first_card_message(ask_id)

    def ask_summary(self, row: AskRow) -> asks.AskSummary:
        count, _ = self.store.answer_totals(row.ask_id)
        return asks.summary(row, self.store.card_delivered(row.ask_id), count)

    def ask_view(self, row: AskRow) -> asks.AskView:
        return asks.view(row, self.store.card_delivered(row.ask_id), self.store.answers(row.ask_id))

    def operator_name(self, mid: str) -> str:
        return self.operators.get(self.store.inbound_sender(mid) or "", "?")

    async def ask_reply(self, mid: str, ask_id: str, text: str, reaction: bool = False) -> None:
        """A plain reply (or a reaction) to a card (R13, R14), never pasted to the agent: on an approval card,
        an approve or a deny when R28 reads it as one; otherwise stored as the answer or a note."""
        self.store.set_inbound(mid, "executing")
        if not self.authorised(mid):
            self.deny(mid, "ask")
            return
        row = self.store.ask(ask_id)
        reading = verbs.read_reply(text) if row is not None and row.kind == "approval" else None
        if row is not None and reading is not None:
            reason = redact(reading.reason)
            if reading.action == "deny" and len(reason) > commands.MAX_REASON:
                self.decide_refused(mid, REASON_TOO_LONG, "reason too long", ask_id)
                return
            await self.decide_card(mid, row, reading.action, reason)
            return
        self.answer_ask(mid, ask_id, text, reaction)

    def answer_ask(self, mid: str, ask_id: str, text: str, reaction: bool = False) -> None:
        """Store an operator's answer to a question or merge ask and reply in thread, as one step. On a
        question with a form, the reaction or reply is mapped to a choice (K17q, K18q) and the reply says so
        (K19); the answer text is stored verbatim either way."""
        choice: str | None = None
        with self.store.transaction():
            row = self.store.ask(ask_id)
            if row is None:
                self.finish(mid, "done", f"No ask {ask_id}.", "ask")
                return
            refusal = self.answer_refusal(row, text)
            if refusal is not None:
                action = "answer-refused"
                self.touch(ask_id)
                self.finish(mid, "done", refusal, "ask")
            elif row.kind == "approval":
                action = "noted"
                self.store.add_answer(ask_id, "note", self.operator_name(mid), mid, text)
                self.touch(ask_id)
                self.finish(mid, "done", NOTED.format(ask_id=ask_id), "ask")
            else:
                action = "answered"
                choice = asks.map_reaction(row, text) if reaction else asks.map_reply(row, text)
                if choice is not None:
                    said = asks.recorded(row, choice, text)
                elif row.status == "answered":
                    said = f"Added to ask {ask_id}; it was already answered, and the poster sees both."
                else:
                    said = f"Answer recorded for ask {ask_id}."
                self.store.add_answer(ask_id, "answer", self.operator_name(mid), mid, text, choice)
                self.store.set_ask(ask_id, "answered")
                self.touch(ask_id)
                self.finish(mid, "done", said, "ask")
        self.audit.write("ask", action=action, message_id=mid, ask_id=ask_id, chars=len(text), choice=choice)

    def answer_refusal(self, row: AskRow, text: str) -> str | None:
        """Why an answer is not stored (R15): fixed wording, or None."""
        try:
            asks.management.stored(row)
        except asks.management.Refusal:
            return asks.management.REFUSED
        if self.store.fulfillment.protected(row.ask_id):
            return "Already fulfilled; original answer retained."
        if row.status not in ("open", "answered"):
            return self.not_open(row)
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

    async def list_asks(self, mid: str, sub: str | None = None) -> None:
        """`!asks`: first the backstop (spec §8): in the worker, under `work_lock`, no decision is in
        flight, so every `deciding` or `uncertain` ask is stranded and is reconciled as at startup. Then
        the active asks, one line each; or, for `!asks bump` and `!asks repeat`, the reminders (B1, B11)."""
        for ask_id, attempt, status in self.store.recovery_snapshot():
            await self.reconcile_quietly(ask_id, attempt, status, restarted=False)
        if not self.authorised(mid):        # the read-backs awaited
            self.deny(mid, "command")
            return
        if sub is not None:
            self.remind(mid, sub)
            return
        from heterodyne.admind.retirement import display_attempt, publish_notice
        at = asks.now()
        lines = [asks.list_line(r, at) for r in self.store.capacity_asks()]
        lines += [f"{r.ask_id} fulfilled (lifecycle); original answer retained"
                  for r in self.store.fulfilled_asks(20)]
        # One bounded eligibility snapshot, at most one new selector per inspection. No retry
        # history is allocated; later inspections can offer other current notices without fan-out.
        with self.store.transaction():
            published = False
            for ask_id, attempt_id, _ in self.store.maintenance_pending():
                attempt = self.store.current_attempt(ask_id)
                proof = None if attempt is None else self.store.retirement.eligible(attempt)
                if proof is None:
                    continue
                if not published and attempt is not None:
                    published = publish_notice(self, attempt, refresh=True, proof=proof)
                lines.append(f"{ask_id} attempt {display_attempt(attempt_id)}: "
                             "reply to its recovery notice with "
                             f"!asks retire {proof.token}")
        self.finish(mid, "done", "\n".join(lines) or NO_ACTIVE_ASKS, "cmd")
        self.audit.write("command", message_id=mid, command="asks", asks=len(lines))

    def remind(self, mid: str, sub: str) -> None:
        """`!asks bump` (B2-B9) or `!asks repeat` (B11) for every `open` ask, oldest first, with the clock
        moves (B14), the summary and `done` in one transaction. The keys hold the command's ID, so a replay
        queues nothing new (B7)."""
        at = asks.now()
        done: list[str] = []
        skipped: list[tuple[str, str]] = []
        with self.store.transaction():
            for row in self.store.capacity_asks():
                try:
                    asks.management.stored(row)
                except asks.management.Refusal:
                    skipped.append((row.ask_id, "management card refused"))
                    continue
                if row.status != "open":
                    skipped.append((row.ask_id, row.status))
                    continue
                if sub == "repeat":
                    try:
                        self.repeat_card(mid, row, at)
                    except asks.management.Refusal:
                        skipped.append((row.ask_id, "management card refused"))
                        continue
                    except chunk.TooWide:      # nothing queued: a link is never cut (K23, r1 B3)
                        skipped.append((row.ask_id, "link too long"))
                        continue
                else:
                    target = self.store.bump_target(row.ask_id)
                    if target is None:
                        skipped.append((row.ask_id, "card not delivered"))
                        continue
                    self.bump(f"asknote:{row.ask_id}:bump:{mid}", asks.reminder(row, at), target,
                              owner=self.request_owners(mid))
                self.store.touch_ask(row.ask_id, asks.stamp(at))
                done.append(row.ask_id)
            verb = "Repeated" if sub == "repeat" else "Bumped"
            self.finish(mid, "done", asks.remind_summary(verb, done, skipped), "cmd")
        self.audit.write("command", message_id=mid, command="asks", sub=sub, bumped=done,
                         skipped=[{"ask_id": ask_id, "why": why} for ask_id, why in skipped])

    async def autobump_loop(self) -> None:
        while True:
            await asyncio.sleep(self.autobump_seconds)
            async with self.work_lock:
                self.autobump()

    def autobump(self) -> None:
        """One automatic-bump check (B14), under `work_lock` and with no await, so the gate, each ask's
        eligibility, its enqueue and its clock move cannot interleave with anything. Every `open` ask quiet
        for `ask_bump_hours` is bumped once, unless an earlier reminder or repeat of it is still pending.
        The gate closed defers the whole check and moves no clock."""
        hours = self.s.ask_bump_hours
        if hours == 0 or not self.may_post():
            return
        at = asks.now()
        due = at - timedelta(hours=hours)
        bumped: list[str] = []
        skipped: list[tuple[str, str]] = []
        for row in self.store.remindable_asks():
            if row.last_activity_at is None or datetime.fromisoformat(row.last_activity_at) > due:
                continue
            try:
                asks.management.stored(row)
            except asks.management.Refusal:
                skipped.append((row.ask_id, "management card refused"))
                continue
            if self.store.reminder_pending(row.ask_id):
                skipped.append((row.ask_id, "pending delivery"))
                continue
            target = self.store.bump_target(row.ask_id)
            if target is None:
                skipped.append((row.ask_id, "card not delivered"))
                continue
            with self.store.transaction():
                seq = self.next_seq("autobump_seq")
                self.bump(f"asknote:{row.ask_id}:autobump:{seq}", asks.reminder(row, at, hours),
                          target, owner=None)
                self.store.touch_ask(row.ask_id, asks.stamp(at))
            bumped.append(row.ask_id)
        if bumped or skipped:
            self.audit.write("ask", action="autobump", bumped=bumped,
                             skipped=[{"ask_id": ask_id, "why": why} for ask_id, why in skipped])

    def bump(self, key: str, text: str, target: str, *, owner: tuple[str, ...] | None) -> None:
        """One reminder, redacted whole and then split, every chunk threaded to `target` (B3, B4, B8)."""
        for i, part in enumerate(chunk.split(redact(text), self.s.chunk_chars)):
            self.post(f"{key}:{i}", part, target, owner=owner)

    def repeat_card(self, mid: str, row: AskRow, at: datetime) -> None:
        """The ask's whole card again, top-level (B11): an approval's stored checked chunks as they are
        (R21), a question's or merge's whole text split; recorded with its own part count (B12). Raises
        chunk.TooWide, with nothing queued, if a link no longer fits a chunk."""
        if self.store.fulfillment.protected(row.ask_id):
            # Explicit history resend, never an active card or new answer authority.
            text = "Fulfilled; no new answer or authority.\n" + asks.full_text(row)
            parts = chunk.split(text, self.s.chunk_chars)
        elif row.kind == "approval":
            parts = asks.stored_details(row)
        else:
            parts = asks.full_chunks(row, self.s.chunk_chars)
        self.post_parts(f"askr:{row.ask_id}:{mid}", parts, owner=self.request_owners(mid))
        self.store.add_repeat(row.ask_id, mid, len(parts), asks.stamp(at))

    def touch(self, ask_id: str) -> None:
        """Accepted activity on the ask, in the caller's transaction: its clock moves to now (B14)."""
        self.store.touch_ask(ask_id, asks.stamp(asks.now()))

    def touch_card(self, target: str | None) -> None:
        """`touch` the ask whose card, `!details` or repeat `target` is, if it is one (B14)."""
        ask_id = self.store.ask_for_message(target)
        if ask_id is not None:
            self.touch(ask_id)

    def bump_hint(self, mid: str, ask_id: str) -> None:
        """A reply, a card command or a decision emoji on a reminder (B6): where to decide instead, in the
        reply's thread or on the reacted reminder (delta §5). Nothing reaches the agent or the ask."""
        self.store.set_inbound(mid, "executing")
        if not self.authorised(mid):
            self.deny(mid, "ask")
            return
        self.finish(mid, "done", BUMP_HINT.format(ask_id=ask_id), "ask")
        self.audit.write("ask", action="bump-hint", message_id=mid, ask_id=ask_id)

    def bang_answer(self, mid: str, cmd: commands.Command) -> None:
        """`!answer <id> <text>`: as a reply to the card, from anywhere."""
        ask_id = asks.normalise_id(cmd.arg or "")
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None or ask_id is None:
            self.finish(mid, "done", show(f"No ask {cmd.arg}.", False), "cmd")
            return
        if row.kind == "approval":     # refused, yet accepted activity on the ask (B14)
            with self.store.transaction():
                self.touch(ask_id)
                self.finish(mid, "done", APPROVAL_NOT_ANSWERED.format(ask_id=ask_id), "cmd")
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
            try:
                parts = asks.full_chunks(row, self.s.chunk_chars, details=True)
            except asks.management.Refusal:
                with self.store.transaction():
                    self.touch(ask_id)
                    self.finish(mid, "done", asks.management.REFUSED, "cmd")
                self.audit.write("ask", action="details-refused", message_id=mid, ask_id=ask_id,
                                 reason="management-contract")
                return
            except chunk.TooWide as exc:   # a link is never cut (K23, r1 B3): refused, nothing queued
                text = asks.DETAILS_LINK_TOO_LONG.format(ask_id=ask_id, n=exc.length, usable=exc.usable,
                                                         c=self.s.chunk_chars)
                with self.store.transaction():
                    self.touch(ask_id)
                    self.finish(mid, "done", text, "cmd")
                self.audit.write("ask", action="details-refused", message_id=mid, ask_id=ask_id,
                                 reason="link too long")
                return
        if self.store.fulfillment.protected(ask_id):
            receipt = self.store.fulfillment.stored(ask_id)
            label = f"Fulfilled lifecycle; receipt {receipt['receipt_id'] if receipt else 'unavailable'}. "
            parts = [label + "Original card and answer authority retained.", *parts]
        with self.store.transaction():
            for i, part in enumerate(parts):
                self.post(f"askd:{ask_id}:{mid}:{i}", part, mid, lane=2, owner=self.request_owners(mid))
            self.store.add_ask_details(ask_id, self.operator_name(mid), mid, len(parts))
            self.touch(ask_id)
            self.store.set_inbound(mid, "done")
        self.audit.write("ask", action="details", message_id=mid, ask_id=ask_id, parts=len(parts))

    # --- approval decisions (relay spec §8 "Decide", R7-R12, R23, R26) ------------------------------
    def decide_refused(self, mid: str, text: str, why: str, ask_id: str | None = None) -> None:
        """A refusal before the attempt exists: the ask is unchanged, but for its activity clock (B14)."""
        with self.store.transaction():
            if ask_id is not None:
                self.touch(ask_id)
            self.finish(mid, "done", text, "ask")
        self.audit.write("ask", action="refused", message_id=mid, ask_id=ask_id, reason=why)

    def not_open(self, row: AskRow) -> str:
        """`Ask p4xw is already <status>[ by <name>][; see ask q9rt]. Nothing recorded.` A stale or
        superseded ask names the newest ask posted for its bead after it, if there is one (R31)."""
        by = f" by {row.decided_by}" if row.decided_by else ""
        newer = self.store.newer_ask(row.ask_id) if row.status in ("stale", "superseded") else None
        see = "" if newer is None else f"; see ask {newer.ask_id}"
        return f"Ask {row.ask_id} is already {row.status}{by}{see}. Nothing recorded."

    def approval_card_for(self, target: str | None) -> AskRow | None:
        """The approval ask whose card or `!details` the message `target` is (R7), or None."""
        ask_id = self.store.ask_for_message(target)
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None or row.kind != "approval" or row.bead is None or row.digest is None:
            return None
        return row

    async def decide(self, mid: str, cmd: commands.Command, target: str | None) -> None:
        """`!approve` / `!deny` as a reply to a card, in the worker under `work_lock` and after `handle`'s
        control-character and authorisation checks (spec §8 "Decide", steps 1-2). The bead and digest are
        the card's; arguments, when given, must be the card's (R7)."""
        row = self.approval_card_for(target)                    # step 1 (R7)
        if row is None or row.bead is None or row.digest is None:
            self.decide_refused(mid, NOT_A_CARD_REPLY, "not a card reply", self.store.ask_for_message(target))
            return
        if cmd.arg is not None and (cmd.arg != row.bead or (cmd.name == "approve" and cmd.rest is not None
                                                            and cmd.rest != row.digest[:12])):     # step 2
            self.decide_refused(mid, MISMATCH.format(bead=row.bead), "no match", row.ask_id)
            return
        await self.decide_card(mid, row, "deny" if cmd.name == "deny" else "approve",
                               (cmd.rest or "") if cmd.name == "deny" else "")

    async def decide_card(self, mid: str, row: AskRow, action: verbs.Action, note: str) -> None:
        """A decision on approval ask `row`, by reply, command or reaction (spec §8 "Decide", steps 3-8;
        R8, R31). `note` is a deny's reason, already redacted and within MAX_REASON."""
        assert row.digest is not None       # noqa: S101 - approval_card_for checked it
        if row.status != "open":
            self.decide_refused(mid, self.not_open(row), "not open", row.ask_id)
            return
        name = self.operator_name(mid)
        if action == "approve":                         # R8: the whole context was delivered
            if not self.store.card_delivered(row.ask_id):
                self.decide_refused(mid, NOT_DELIVERED.format(ask_id=row.ask_id), "not delivered", row.ask_id)
                return
            if row.truncated and not self.store.details_delivered(row.ask_id, name):    # a legacy ask
                self.decide_refused(mid, NEEDS_DETAILS.format(ask_id=row.ask_id), "needs details", row.ask_id)
                return
        if not self.authorised(mid):                    # step 3
            self.deny(mid, "decision")
            return
        if self.approve_bead is None:
            self.decide_refused(mid, ASK_NO_APPROVALS, "not configured", row.ask_id)
            return
        a = Attempt(row.ask_id, mid, action, name, "marmot:" + canonical_ref(mid), row.digest,
                    note if action == "deny" else None)
        with self.store.transaction():
            begun = self.store.begin_attempt(a)
            if begun:
                self.touch(row.ask_id)
        if not begun:                                   # step 4 (R22, R23)
            now_row = self.store.ask(row.ask_id)
            self.decide_refused(mid, self.not_open(now_row or row), "not open", row.ask_id)
            return
        await self.run_attempt(a, row)

    def preflight(self, r: Readout | Busy, a: Attempt, bead: str) -> tuple[str, str, str] | None:
        """Step 5: why the attempt stops before the decision runs, as (new ask status, reply, reason), or
        None to go on. A changed digest is ("stale", "", "stale"): `refresh` settles it, with its reply."""
        if isinstance(r, Busy):
            return "open", BEAD_BUSY.format(bead=bead), "busy"
        if r.digest != a.digest:
            return "stale", "", "stale"
        if r.decided:
            return "blocked", BLOCKED.format(bead=bead, quoted=""), "decided"
        if r.status != "open":
            return "open", NOT_DECIDABLE.format(bead=bead, why=f"{r.status}, not open"), "not open"
        if not r.kind_approval:
            return "open", NOT_DECIDABLE.format(bead=bead, why="not a kind:approval bead"), "not approval"
        if a.operator not in r.approvers:
            return "open", NOT_APPROVER.format(name=a.operator), "not an approver"
        # btq-ryfxf: an approve btq's gate would reject (the policy changed since the post, say) is not
        # written. This narrows, doesn't eliminate, the race: the policy can still change after this read.
        if a.action == "approve" and (r.gate_preview is None or not r.gate_preview.valid):
            return "open", GATE_REFUSED.format(bead=bead, why=redact(approvals.gate_reasons(r))), "gate"
        return None

    def close_refused(self, a: Attempt, new_status: str, text: str, why: str) -> None:
        """Close the attempt before anything was launched, with its reply, in one compare-and-set."""
        with self.store.transaction():
            if new_status == "open" and self.store.operation_withdrawn(a.ask_id):
                new_status, text, why = ("cancelled", "Source withdrew this approval request; "
                                         "no native decision was issued.", "source-withdrawn")
            closed = self.store.close_attempt(a.ask_id, a.message_id, expect_status="deciding",
                                              settled="refused", exit_status=None, new_status=new_status,
                                              outcome=text, decided_by=None)
            if closed:
                self.finish(a.message_id, "done", text, "ask")
        if not closed:
            self.conflict(a, "deciding")
            return
        self.audit_quietly("ask", action="refused", message_id=a.message_id, ask_id=a.ask_id, reason=why,
                         status=new_status)

    def conflict(self, a: Attempt, expected: str) -> None:
        """A compare-and-set found the ask changed: nothing was written. Fixed wording."""
        self.audit_quietly("ask", action="conflict", ask_id=a.ask_id, message_id=a.message_id,
                           expected=expected)

    def hold_maintenance(self, a: Attempt, reason: str, *,
                         hold: approvals.MaintenanceHold | None = None,
                         not_spawned: bool = False) -> None:
        """Commit proof and overlay together; notice delivery cannot erase observed evidence."""
        with self.store.transaction():
            if hold is not None:
                self.store.retirement.record_hold(a, hold)
            elif not_spawned:
                self.store.retirement.record_not_spawned(a)
            deferred = self.store.defer_maintenance(
                a, self.wallclock() + MAINTENANCE_RETRY_SECONDS, reason=reason)
            if not deferred:
                self.conflict(a, "deciding")
                row = self.store.ask(a.ask_id)
                self.finish(a.message_id, "done",
                            CONFLICT.format(ask_id=a.ask_id, bead=row.bead if row else ""), "ask")
                return
        # A separate transaction retains an all-or-nothing notice/mapping pair. Expected local
        # delivery/storage failures leave the durable proof+overlay eligible for later inspection.
        try:
            with self.store.transaction():
                decision = "denial" if a.action == "deny" else "approval"
                next_step = ("Automatic retry is suspended. "
                             "See the separate recovery notice for eligibility. "
                             if reason in MAINTENANCE_SUSPENDED else
                             "It will retry after fresh card and operator-authority checks. ")
                self.ask_notice(a.ask_id, "maintenance-" + reason, a.message_id,
                                f"Ask {a.ask_id}: {decision} received and held ({reason}). "
                                "No decision was issued by the invocation that returned maintenance HOLD. "
                                + next_step + "The original reply is preserved.", a.message_id)
                from heterodyne.admind.retirement import publish_notice
                publish_notice(self, a, refresh=reason in MAINTENANCE_SUSPENDED)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            self.audit_quietly("ask", action="maintenance-notice-unavailable", ask_id=a.ask_id,
                               error=type(exc).__name__)
        self.audit_quietly("ask", action="maintenance-held", ask_id=a.ask_id,
                           message_id=a.message_id, reason=reason)

    async def run_attempt(self, a: Attempt, row: AskRow, *, maintenance_retry: bool = False,
                          prechecked: Readout | None = None) -> None:
        """Steps 4-8 of a persisted attempt. Until the decision child exists, every failure closes the
        attempt as `refused` (r5-1). The child is reaped; ordinary results use native readback,
        while a closed pre-effect HOLD retains the attempt without native readback."""
        assert self.approve_bead is not None and row.bead is not None      # noqa: S101 - checked by decide
        ab, bead, mid = self.approve_bead, row.bead, a.message_id
        launched = False
        if not maintenance_retry and len(self.store.maintenance_pending()) >= MAINTENANCE_CAPACITY:
            self.close_refused(a, "open", "Maintenance retention capacity reached; no decision was issued. "
                               "Operator recovery is required before retrying.", "maintenance-capacity")
            return

        def on_launch() -> None:
            nonlocal launched
            launched = True

        code: int | None = None
        line = ""
        audited = unauthorised = issued = False
        authority_reason: str | None = None
        stop: tuple[str, str, str] | None = None
        r: Readout | Busy | None = None
        proven_hold: approvals.MaintenanceHold | None = None
        not_spawned = False
        latch_token = self.store.get("maintenance_latch_token")
        try:
            self.audit.write("ask", action="deciding", message_id=mid, ask_id=a.ask_id, bead=bead,
                             decision=a.action, operator=a.operator)
            audited = True
            r = prechecked if prechecked is not None else await ab.read(bead)  # step 5
            stop = self.preflight(r, a, bead)
            if stop is None:
                if maintenance_retry:
                    authority_reason = self.maintenance_authority_reason(a)
                    unauthorised = authority_reason is not None
                else:
                    unauthorised = not self.authorised(mid)
                if not unauthorised:
                    if not self.store.issue_decision(a, maintenance_retry=maintenance_retry):
                        if self.store.operation_withdrawn(a.ask_id):
                            self.close_refused(a, "cancelled", "Source withdrew this approval request; "
                                               "no native decision was issued.", "source-withdrawn")
                            return
                        self.conflict(a, "deciding")
                        self.finish(mid, "done", CONFLICT.format(ask_id=a.ask_id, bead=bead), "ask")
                        return
                    issued = True
                    result = await ab.decide(a, bead, on_launch)        # step 7
                    if isinstance(result, approvals.MaintenanceHold):
                        proven_hold = result
                    else:
                        code, line = result
        except asyncio.CancelledError:
            # Ordinary decisions retain R26 readback settlement. An interrupted maintenance retry
            # keeps its prelaunch uncertainty marker and never regains automatic replay entitlement.
            raise
        except Exception as exc:  # noqa: BLE001 - fixed words; nothing launched means nothing was written
            if not launched:
                # approve-bead's own failures carry their word (a spawn OSError is "unavailable"); the
                # audit write failing, or a bug, is "error" (spec §8's table)
                word = (exc.word if isinstance(exc, BtqError)
                        else "unavailable" if isinstance(exc, OSError) and audited else "error")
                not_spawned = maintenance_retry and issued and isinstance(exc, approvals.NotSpawned)
                stop = ("open", PREFLIGHT_FAILED.format(bead=bead, word=word), word)
        if proven_hold is not None:
            # Retain the conservative issuance distinction even if the subsequent durable proof +
            # overlay commit fails. This marker alone never grants retry/retirement entitlement.
            if not self.store.mark_maintenance_issuing(a):
                self.conflict(a, "deciding")
                self.finish(mid, "done", CONFLICT.format(ask_id=a.ask_id, bead=bead), "ask")
                return
            revoked = self.latched() or self.store.get("maintenance_latch_token") != latch_token
            self.hold_maintenance(a, "latched" if revoked else "maintenance", hold=proven_hold)
            return
        if unauthorised:
            if maintenance_retry:
                self.hold_maintenance(a, authority_reason or "authority")
                return
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
            if maintenance_retry:
                self.hold_maintenance(a, self.maintenance_stop_reason(stop), not_spawned=not_spawned)
                return
            if stop[0] == "stale" and isinstance(r, Readout):
                await self.refresh(a, row, r, "refused", None, "")
            else:
                self.close_refused(a, *stop)
            return
        latched_during = self.latched()                                 # step 8
        settled, back = await self.read_and_settle(a, bead)
        if settled == "untouched" and back is not None and back.digest != a.digest:
            await self.refresh(a, row, back, "untouched", code, line)  # --expect-digest refused (R29)
            return
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

    async def refresh(self, a: Attempt, row: AskRow, r: Readout, settled: str, code: int | None,
                      line: str) -> None:
        """R29: the card's bead changed (readout `r`), so nothing is recorded and a fresh card replaces the
        stale one, in the attempt's own settlement. In the worker under `work_lock`, take `ask_post_lock`
        (the lock order), prepare the fresh ask, then, with no await in between, one transaction closes the
        attempt (`settled`, the ask `stale`), stores and queues the fresh card, and queues the reply. If the
        fresh card is refused, the reply says why; if only btq's pin refuses it, the updated content is
        queued in the stale card's thread, marked undecidable, under the stale ask's `askd:` keys. A failed
        compare-and-set writes nothing, and queues nothing."""
        bead, mid = row.bead or "", a.message_id
        async with self.ask_post_lock:
            fresh = self.prepare_approval(row.poster, bead, r, (a.ask_id, mid))
            shown, now_ = a.digest[:12], (r.digest or "none")[:12]
            updated: list[str] = []
            if isinstance(fresh, FreshCard):
                text = STALE_REFRESHED.format(bead=bead, shown=shown, now=now_, new=fresh.row.ask_id)
            else:
                text = STALE_NOT_REFRESHED.format(bead=bead, shown=shown, now=now_, why=fresh[0].rstrip("."))
                if pinned_only(r):
                    content = asks.updated_content(bead, r, self.s.chunk_chars)
                    if isinstance(content, str):
                        text += UPDATED_NOT_SHOWN.format(why=content)
                    else:
                        updated = content
            if line:
                text += f'\napprove-bead said: "{line}"'
            thread = self.attempt_thread(a.ask_id, mid)        # the updated content goes where the reply goes
            superseded: list[str] = []
            closed = False
            with self.store.transaction():
                binding = operations.bead_binding(self.store, bead)
                if binding is not None and binding[1]:
                    # Both callers have positive evidence of no decision: preflight refusal or
                    # actual untouched readback. Withdrawal forbids replacement, not settlement.
                    text = "Source withdrew this approval request; no native decision was recorded."
                    closed = self.store.close_attempt(
                        a.ask_id, mid, expect_status="deciding", settled=settled, exit_status=code,
                        new_status="cancelled", outcome=text, decided_by=None)
                    if closed:
                        self.finish(mid, "done", text, "ask")
                    withdrawn = True
                else:
                    withdrawn = False
                if not withdrawn and isinstance(fresh, FreshCard):
                    try:
                        operations.before_commit(self.store, bead, None if binding is None else binding[0])
                    except operations.Invalid as error:
                        fresh = ("Source lineage cannot accept a replacement card.", str(error))
                        text = STALE_NOT_REFRESHED.format(bead=bead, shown=shown, now=now_,
                                                          why=fresh[0].rstrip("."))
                if not withdrawn:
                    closed = self.store.close_attempt(a.ask_id, mid, expect_status="deciding",
                                                      settled=settled, exit_status=code, new_status="stale",
                                                      outcome=text, decided_by=None)
                if closed and not withdrawn:
                    if isinstance(fresh, FreshCard):
                        superseded = self.commit_approval(
                            fresh, None, operation_key=None if binding is None else binding[0])
                    for i, part in enumerate(updated):      # the request part is the bare event ID
                        self.post(f"askd:{a.ask_id}:{mid.removeprefix(REACTION_PREFIX)}:{i}", part, thread,
                                  lane=2, owner=self.request_owners(mid))
                    self.finish(mid, "done", text, "ask")
        if not closed:
            self.conflict(a, "deciding")
            return
        self.audit.write("ask", action="refused" if settled == "refused" else "decided", message_id=mid,
                         ask_id=a.ask_id, bead=bead, reason="source-withdrawn" if withdrawn else "stale",
                         outcome=settled, status="cancelled" if withdrawn else "stale",
                         exit_status=code,
                         fresh=fresh.row.ask_id if not withdrawn and isinstance(fresh, FreshCard) else None,
                         refresh_refused=None if isinstance(fresh, FreshCard) else fresh[1],
                         updated_parts=len(updated))
        if not withdrawn and isinstance(fresh, FreshCard):
            self.audit_posted(fresh, None, superseded)

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
        if settled == "untouched" and self.store.operation_withdrawn(a.ask_id):
            return "cancelled", "Source withdrew this approval request; no native decision was recorded."
        if settled == "untouched":
            return "open", (UNTOUCHED.format(line=line, bead=bead) if line
                            else UNTOUCHED_SILENT.format(bead=bead))
        if settled == "blocked":
            return "blocked", BLOCKED.format(bead=bead, quoted=f' ("{line}")' if line else "")
        said = f'\napprove-bead said: "{line}"' if line else ""
        if settled == "recorded" and back is not None:
            others = asks.count_others(o for o in self.store.capacity_asks()
                                       if o.ask_id != a.ask_id)
            said = "\n" + asks.in_flight(*others) + said
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
        in (r3-2). Its notice is threaded to the attempt's reply target (delta §5); the attempt's inbound
        message, if it is still `executing` (or, defensively, `received`), is marked `done` in the same
        transaction (r6), so a restart does not answer it."""
        a = self.store.current_attempt(ask_id)
        row = self.store.ask(ask_id)
        if a is None or a.message_id != attempt or row is None or row.bead is None:
            self.audit_quietly("ask", action="reconcile-skipped", ask_id=ask_id)
            return
        if self.store.maintenance_retry_at(ask_id, attempt, include_suspended=True) is not None:
            # Proven pre-effect attempts keep their original inbound/provenance across restart.
            # The bounded retry loop performs fresh policy and native checks before any new child.
            return
        if row.outcome == "maintenance-issuing":
            await self.reconcile_issuing(a, row)
            return
        if self.approve_bead is None:
            self.unverified(ask_id, attempt, row.bead)
            return
        settled, back = await self.read_and_settle(a, row.bead)
        new_status, text = self.settlement(settled, back, a, row.bead, "")
        if settled == "untouched" and new_status == "open":
            text = RECONCILE_RESTARTED if restarted else RECONCILE_STRANDED.format(ask_id=ask_id)
        with self.store.transaction():
            closed = self.store.close_attempt(ask_id, attempt, expect_status=status, settled=settled,
                                              exit_status=None, new_status=new_status, outcome=text,
                                              decided_by=a.operator if settled == "recorded" else None)
            if closed:
                if self.store.inbound_status(attempt) in ("received", "executing"):
                    self.store.set_inbound(attempt, "done")
                if new_status != status:
                    self.ask_notice(ask_id, "reconciled", str(self.next_seq("reconcile_seq")), text, attempt)
        if not closed:
            self.conflict(a, status)
            return
        self.audit.write("ask", action="reconciled", ask_id=ask_id, message_id=attempt, outcome=settled,
                         status=new_status, was=status, restarted=restarted)

    async def maintenance_retry_loop(self) -> None:
        """Bounded serial cycles; overdue or failed work never creates a tight exception loop."""
        while True:
            delay = MAINTENANCE_RETRY_SECONDS
            failed = False
            try:
                for ask_id, mid, due in self.store.maintenance_retryable():
                    if self.wallclock() < due:
                        delay = min(delay, max(1.0, due - self.wallclock()))
                        continue
                    async with self.work_lock:
                        try:
                            await self.retry_maintenance(ask_id, mid)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:  # noqa: BLE001 - preserve uncertainty; bounded cycle
                            failed = True
                            self.audit_quietly("ask", action="maintenance-retry-failed", ask_id=ask_id,
                                               error=type(exc).__name__)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - DB listing failure also receives full backoff
                failed = True
                self.audit_quietly("ask", action="maintenance-list-failed", error=type(exc).__name__)
            await asyncio.sleep(MAINTENANCE_RETRY_SECONDS if failed else delay)

    def maintenance_authority_reason(self, a: Attempt) -> str | None:
        """Separate temporary observation/policy failure from a proven sender-policy mismatch."""
        if self.approve_bead is None:
            return "approvals-unconfigured"
        if not self.refresh_authority():
            return "policy-unavailable"
        if self.latched():
            return "latched"
        if not self.group_ok or not self.observing or self.changing:
            return "group-unverified"
        sender = self.store.inbound_sender(a.message_id)
        # No await after the successful refresh above: classify against that one policy snapshot.
        if sender is None or sender not in self.operators or self.operator_name(a.message_id) != a.operator:
            return "authority"
        return None

    @staticmethod
    def maintenance_stop_reason(stop: tuple[str, str, str]) -> str:
        return {"busy": "native-busy", "stale": "changed-card", "decided": "native-conflict",
                "not open": "not-open", "not approval": "not-approval",
                "not an approver": "not-approver", "gate": "gate"}.get(stop[2], "read-unavailable")

    async def reconcile_issuing(self, a: Attempt, row: AskRow) -> None:
        """Only an exact native decision settles stranded issuance; untouched proves no retry right."""
        settled, back = (await self.read_and_settle(a, row.bead or "")
                         if self.approve_bead is not None else ("uncertain", None))
        if settled == "recorded" and back is not None:
            self.settle_maintenance_recorded(a, row, back)
            return
        with self.store.transaction():
            self.ask_notice(a.ask_id, "issuance-unverified", a.message_id,
                            f"Ask {a.ask_id}: decision issuance remains unverified. The original reply "
                            "is preserved; no automatic replay or untouched settlement is permitted. "
                            "Authenticated recovery is not available in this version.", a.message_id)
        self.audit_quietly("ask", action="issuance-held", ask_id=a.ask_id, message_id=a.message_id,
                           outcome=settled)

    def settle_maintenance_recorded(self, a: Attempt, row: AskRow, back: Readout) -> None:
        new_status, text = self.settlement("recorded", back, a, row.bead or "", "")
        with self.store.transaction():
            closed = self.store.close_attempt(a.ask_id, a.message_id,
                                              expect_status="deciding", settled="recorded",
                                              exit_status=None, new_status=new_status, outcome=text,
                                              decided_by=a.operator)
            if closed:
                self.finish(a.message_id, "done", text, "ask")
        if not closed:
            self.conflict(a, "deciding")
            return
        self.audit_quietly("ask", action="reconciled", ask_id=a.ask_id, message_id=a.message_id,
                           outcome="recorded", status=new_status, was="pending-maintenance")

    async def retry_maintenance(self, ask_id: str, mid: str) -> None:
        """Read native first: another valid decision wins; only an untouched exact card can retry."""
        pending = self.store.maintenance_retry_at(ask_id, mid)
        if pending is None or self.wallclock() < pending:
            return
        a, row = self.store.current_attempt(ask_id), self.store.ask(ask_id)
        if a is None or a.message_id != mid or row is None or row.bead is None or row.status != "deciding":
            return
        # Backoff persists even on failed policy/readback, including across daemon restarts.
        if not self.store.defer_maintenance(a, self.wallclock() + MAINTENANCE_RETRY_SECONDS):
            return
        if not operations.decision_allowed(self.store, ask_id):
            # Withdrawal stops future issuance, but cannot attest what a previous child did.
            # Read-only native evidence must still distinguish untouched from recorded/unknown.
            settled, back = await self.read_and_settle(a, row.bead)
            if settled == "recorded" and back is not None:
                self.settle_maintenance_recorded(a, row, back)
            elif settled == "untouched":
                self.close_refused(a, "cancelled", "Source withdrew this approval request; "
                                   "no native decision was recorded.", "source-withdrawn")
            else:
                self.hold_maintenance(a, "native-conflict" if settled == "blocked" else "read-unavailable")
            return
        reason = self.maintenance_authority_reason(a)
        if reason is not None:
            self.hold_maintenance(a, reason)
            return
        settled, back = await self.read_and_settle(a, row.bead)
        if settled == "recorded" and back is not None:
            self.settle_maintenance_recorded(a, row, back)
            return
        if settled != "untouched" or back is None:
            self.hold_maintenance(a, "native-conflict" if settled == "blocked" else "read-unavailable")
            return
        stop = self.preflight(back, a, row.bead)
        if stop is not None:
            self.hold_maintenance(a, self.maintenance_stop_reason(stop))
            return
        await self.run_attempt(a, row, maintenance_retry=True, prechecked=back)

    def unverified(self, ask_id: str, attempt: str, bead: str) -> None:
        """A stranded attempt that cannot be read back: approve_bead was removed (say, for a rollback).
        recover() left its message to the reconcile, so it is answered here, once: the notice and `done`
        commit together, and a later run finds the message done. It says the decision may be recorded, never
        "decide again". The ask and its attempt stay as they are, for a reconcile with approve_bead set."""
        with self.store.transaction():
            answered = self.store.inbound_status(attempt) in ("received", "executing")
            if answered:
                self.store.set_inbound(attempt, "done")
                self.ask_notice(ask_id, "unverified", attempt, RECONCILE_UNVERIFIED.format(bead=bead),
                                attempt)
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
        if startup and not self.agent.login_matches() and await asyncio.to_thread(self.agent.alive):
            # A live pane launched on another login (or on an unknown one: a store from before AU-11) is
            # never adopted. Its replacement intent and the notice's outbox key are committed with the
            # abandonment, so a crash at any later point neither adopts it nor loses or repeats the notice.
            # An unfinished replacement already pending (a !new, say) keeps its own word.
            replace = "login-changed" if self.agent.replace_pending() is None else None
            self.abandon_in_flight(LOGIN_CHANGED_WHY, idle=True, replace=replace, notice=True)
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
            self.contained("login-notice-failed", self.flush_pending_notice)
            return
        self.contained("login-notice-failed", self.flush_pending_notice)
        self.stuck = None
        self.audit_quietly("agent", action=mode, session=self.agent.session_id)
        if mode == "adopted":
            if startup:
                self.hold_adopted()
            self.mark_ready()
        else:
            self.is_ready()     # an event from a launch that has since been replaced is not readiness

    def flush_pending_notice(self) -> None:
        """Post the login-change notice an earlier replacement allocated, and forget it, in one transaction.
        Run after every start, whatever its outcome: the key is fixed and the outbox ignores a repeat, so it
        is posted exactly once even if the pane it was about has since been replaced and adopted."""
        with self.store.transaction():
            key = self.store.get("pending_notice")
            if key is not None:
                self.post(key, LOGIN_CHANGED_NOTICE, None, owner=None)
                self.store.delete("pending_notice")

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
            self.post(f"adopt-hold:{self.next_seq('adopt_seq')}", ADOPT_HOLD_NOTICE, None, owner=None)

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
                          AGENT_STUCK_NOTICE.format(why=self.stuck), None, owner=None)

    def next_seq(self, key: str) -> int:
        n = int(self.store.get(key) or "0") + 1
        self.store.set(key, str(n))
        return n

    def observe_membership(self, *, count: int | None = None, kind: str | None = None) -> None:
        with self.store.transaction():
            state = guard_state.notice_state(self.store.get("membership_notice"))
            if state["last"] is None:
                state["first"] = state["last"] = self.expected_members()
            guard_state.observe(state, count=count, kind=kind)
            self.store.set("membership_notice", json.dumps(state, sort_keys=True))
        self.wake.set()

    def flush_membership_notice(self) -> None:
        """Synchronous Store transaction: observation, enqueue and send bookkeeping cannot interleave."""
        at = self.wallclock()
        with self.store.transaction():
            state = guard_state.notice_state(self.store.get("membership_notice"))
            last = state["last_sent"]
            if last is not None and last > at:
                state["last_sent"] = last = at       # bound clock rollback delay to one cadence
                if state["unresolved"] is not None:
                    self.store.limit_delay(state["unresolved"], at + self.s.group_check_seconds)
                self.store.set("membership_notice", json.dumps(state, sort_keys=True))
            if (not state["pending"] or state["unresolved"] is not None
                    or (last is not None and at < last + self.s.group_check_seconds)):
                return
            state["sequence"] += 1
            group = f"membership-event:{state['sequence']}"
            kinds = ", ".join(f"{key}={value}" for key, value in sorted(state["kinds"].items()))
            text = (f"Group membership observations: count {state['first']} to {state['last']}; "
                    f"{kinds or 'count change'}. Identities unavailable; operator authority is unchanged.")
            self.post(group, text, None, owner=None)
            state["unresolved"] = group
            state["pending"] = False
            state["first"] = state["last"]
            state["kinds"] = {}
            self.store.set("membership_notice", json.dumps(state, sort_keys=True))

    def request_group_check(self) -> None:
        """One running check and one requested followup; event replay cannot grow a task queue."""
        self.check_requested = True
        if self.check_task is None or self.check_task.done():
            self.check_task = asyncio.create_task(self.coalesced_group_check())
            self._idle_tasks.add(self.check_task)
            self.check_task.add_done_callback(self._idle_tasks.discard)

    async def coalesced_group_check(self) -> None:
        try:
            while self.check_requested:
                self.check_requested = False
                await self.check_group()
        except Exception as exc:  # noqa: BLE001 - existing cadence retries a failed check
            self.audit_quietly("guard", action="group-check-failed", error=type(exc).__name__)

    async def read_member_count(self) -> int:
        # Lock order: transition/work/dispatch/send -> count; count never acquires any of those locks.
        async with self.count_lock:
            return (await self.client.group_info(self.account, self.group)).member_count

    async def check_group(self) -> bool:
        """Adopt only a stable current subscription/epoch count. Observation never grants authority."""
        async with self.count_lock:
            generation, acked, epoch = self.sub_gen, self.acked, self.membership_epoch
            if self.changing:
                self.audit_quietly("guard", action="group-check-deferred")
                return False
            try:
                count = (await self.client.group_info(self.account, self.group)).member_count
            except ControlError:
                if generation != self.sub_gen or epoch != self.membership_epoch or self.changing:
                    return False
                self.group_ok = False
                self.observing = False
                self.audit_quietly("guard", action="group-check-unavailable")
                return False
            if (generation != self.sub_gen or epoch != self.membership_epoch or self.changing
                    or not acked or not self.acked):
                self.audit_quietly("guard", action="group-check-superseded")
                return False
            if count < 1:
                self.group_ok = False
                self.observing = False
                self.audit_quietly("guard", action="group-check-invalid-count")
                return False
            self.observe_membership(count=count)
            with self.store.transaction():
                self.store.set("expected_members", str(count))
                self.store.delete("expected_members_provisional")
            self.group_ok = self.observing = True
            self.flush_membership_notice()
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

    def removes_last_operator(self, member_hex: str, policy: Iterable[Operator]) -> bool:
        """Whether removing this key would leave no confirmed policy operator."""
        confirmed = self.confirmed_operators() or set()
        return member_hex in confirmed and not ((confirmed - {member_hex}) & {o.hex for o in policy})

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
                member_hex = target.hex
            else:
                target = loaded.get(name)
                if target is None:
                    return "refused", f"{name} is not an operator in policy.toml; nothing changed."
                member_hex = target.hex
                if self.removes_last_operator(member_hex, loaded.values()):
                    return "refused", "the last confirmed policy operator can't be removed."
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
                        count = await self.read_member_count()
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
                    if count < 1:
                        settled = True
                        self.group_ok = False
                        return "refused", "the group's member count is unavailable; nothing changed."
                    expected = count
                    self.observe_membership(count=count)
                    self.store.set("expected_members", str(count))
                    self.store.delete("expected_members_provisional")
                    to = expected + 1 if op == "add" else expected - 1
                    if to < 2:
                        settled = True
                        return "refused", "the last operator can't be removed."
                    # Baseline/lock awaits may span a policy edit. Validate immediately before RPC journaling.
                    try:
                        fresh = {o.name: o for o in self.load_operators()}
                    except Exception:  # noqa: BLE001 - local policy read fails closed
                        settled = True
                        return "refused", "policy.toml could not be read; nothing changed."
                    target = fresh.get(name)
                    if target is None or target.hex != member_hex:
                        settled = True
                        return "refused", "operator policy changed; nothing changed."
                    if op == "remove" and self.removes_last_operator(member_hex, fresh.values()):
                        settled = True
                        return "refused", "the last confirmed policy operator can't be removed."
                    policy = tuple(fresh.values())
                    pending = membership.Pending("add" if op == "add" else "remove", name, member_hex,
                                                 expected, to, now(), uuid.uuid4().hex)
                    journaled = True        # exceptions retain the durable membership hold
                    self.store.set("membership_pending", pending.dump())
                    self.audit.write("membership", action="pending", op=pending.op, operator=name,
                                     from_count=expected, to_count=to)
                    reported = await self.member_call(pending)
                    try:
                        after: int | None = await self.read_member_count()
                        if generation != self.sub_gen or not self.acked or after < 1:
                            after = None
                    except ControlError:
                        after = None
                    outcome = membership.settle(reported, after, pending)
                    result = self.settle_membership(pending, outcome, reported, after, policy)
                    settled = True
                    return result
                except BaseException:
                    if journaled and not settled:
                        self.audit_quietly("membership", action="held-unsettled")
                    raise
                finally:
                    # Unknown keeps only the durable membership hold, not ordinary-work/global latch.
                    self.changing = False
                    self.request_group_check()
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
                self.store.set("expected_members", str(after if after is not None else pending.to_count))
                if after is None:
                    self.store.set("expected_members_provisional", "1")
                else:
                    self.store.delete("expected_members_provisional")
                self.store.set("group_operators", json.dumps(sorted(confirmed)))
                self.store.delete("membership_pending")
                # The key holds the change's own ID, not its start time: two changes in one second differ.
                self.post(f"membership:{pending.change_id}",
                          f"Operator {pending.name} was {verb} the group.", None, owner=None)
            self.policy_operators = policy
            self.authorise(confirmed)
            self.audit_quietly("membership", action="committed", op=pending.op, operator=pending.name,
                               member_count=pending.to_count)
            return "committed", f"Operator {pending.name} was {verb} the group ({pending.to_count} members)."
        if outcome == "abort":
            self.store.delete("membership_pending")
            self.audit_quietly("membership", action="aborted", op=pending.op, operator=pending.name)
            return "aborted", "wn-agent refused the change; operator authority is unchanged."
        self.audit_quietly("membership", action="held-unknown", reported=reported)
        return "unknown", ("the membership response is unknown; further membership changes are held. "
                           "Check the group's members, then run `admind rearm`. "
                           "Operator authority is unchanged.")

    async def rearm(self) -> tuple[str, str]:
        """Clear recovery holds after a current subscribed count and fresh policy check.

        Preserve the confirmed operator set, intersected with policy. Count observations
        never settle a pending RPC or grant operator authority.
        """
        async with self.transition_lock:
            if not self.reading:
                # Acknowledged is not enough: until confirm_observing returns, events wait unread in the
                # stream, and a resubscription would drop them (Codex r3 finding 3).
                return "refused", ("admind is not reading the group's events right now, so a change during "
                                   "rearm could be missed; nothing changed. Try again shortly.")
            generation, events, latched_before = self.sub_gen, self.group_events, self.store.get("latched")
            try:
                count = await self.read_member_count()
            except ControlError as exc:
                return "refused", f"could not read the group's member count ({exc.code}); nothing changed."
            if (not self.reading or generation != self.sub_gen or events != self.group_events
                    or self.store.get("latched") != latched_before):
                self.audit_quietly("guard", action="rearm-refused", why="changed-meanwhile")
                return "refused", ("the group changed while rearm read it (a membership event, a new "
                                   "latch or a resubscription); nothing changed. Check the group's "
                                   "members, then run `admind rearm` again.")
            if count < 1:
                return "refused", "the group member count is invalid; nothing changed."
            # The count RPC can wait while policy changes. Read authority after that await,
            # immediately before reconciling and persisting the confirmed intersection.
            try:
                policy = self.load_operators()
            except Exception as exc:  # noqa: BLE001 - ConfigError, or a policy file that can't be read
                self.audit.write("guard", action="rearm-refused", why="policy-unreadable",
                                 error=type(exc).__name__)
                return "refused", "policy.toml could not be read; nothing changed."
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
                # A previous suspension write may have failed after the safety latch committed.
                # Rearm must finish that fence before clearing the latch; failure keeps it set.
                if previous is not None:
                    for ask_id, mid, due in self.store.maintenance_pending():
                        attempt = self.store.current_attempt(ask_id)
                        if attempt is not None and attempt.message_id == mid:
                            self.store.defer_maintenance(attempt, due, reason="latched")
                self.store.set("expected_members", str(count))
                self.store.delete("expected_members_provisional")
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
        if isinstance(event, InboundMessage) and gateway_custody.command(event.message.text) is not None:
            if (event.account_id_hex.lower() != self.account or event.group_id_hex.lower() != self.group
                    or not self.refresh_authority()
                    or event.message.sender.account_id_hex.lower() not in self.operators
                    or not MESSAGE_ID.fullmatch(event.message.message_id_hex.lower())):
                self.audit.write("gateway-custody", action="refused", reason="command-auth")
                return
            try:
                self.custody_work.put_nowait(event)
            except asyncio.QueueFull:
                # No acknowledgment, consent or permit is manufactured at this
                # boundary. Production enrollment must qualify native redelivery.
                self.audit.write("gateway-custody", action="refused", reason="command-capacity")
        elif isinstance(event, InboundMessage | ReactionAdded):    # one worker, one order (R27)
            self.work.put_nowait(event)
        elif isinstance(event, GroupStateChanged):
            if event.group_id_hex.lower() != self.group or event.change not in guard.MEMBERSHIP_CHANGES:
                self.audit.write("event", action="ignored", what="group-change")
                return
            self.group_events += 1
            self.observe_membership(kind=event.change)
            self.audit.write("guard", action="membership-observed", change=event.change)
            self.request_group_check()
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
                    if isinstance(event, InboundMessage):
                        await self.on_message(event)
                    else:
                        await self.on_reaction(event)
            except Exception as exc:  # noqa: BLE001 - one bad message must not end the worker
                self.audit.write("handler", action="failed", error=type(exc).__name__)

    async def on_message(self, ev: InboundMessage) -> None:
        self.refresh_authority()
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
        if gateway_custody.command(ev.message.text) is not None:
            try:
                self.custody_work.put_nowait(ev)
            except asyncio.QueueFull:
                self.audit.write("gateway-custody", action="refused", reason="command-capacity")
            return
        if (recovery.COMMAND.fullmatch(ev.message.text) is not None
                and MESSAGE_ID.fullmatch(mid)):
            await self.recovery_message(ev)
            return
        if not self.store.claim_inbound(mid, sender_key=ev.message.sender.account_id_hex.lower()):
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
                self.post("ready", READY_NOTICE, None, owner=None)
        target = ev.reply_to.message_id_hex.lower() if ev.reply_to is not None else None
        await self.handle(mid, text, target)

    async def on_reaction(self, ev: ReactionAdded) -> None:
        """A reaction (R27): the same guard, claim and checks as a message, judged on its actor, under the
        key `r:<event id>` with the reacted message as its reply target. Never pasted to the agent."""
        self.refresh_authority()
        verdict = guard.judge_message(ev, group_id=self.group, operators=self.operators,
                                      latched=self.latched())
        if verdict.action == "ignore":
            return
        if verdict.action == "drop":
            if verdict.operator is not None:    # an operator, while latched: logged in full, never acted on
                self.audit.write("drop", operator=verdict.operator, what="reaction", reason=verdict.reason,
                                 emoji=own_text(ev.emoji), target=ev.target_message_id_hex.lower())
                return
            self.audit.write("drop", sender_prefix=ev.actor.account_id_hex[:8], what="reaction",
                             reason=verdict.reason)
            return
        event_id = (ev.event_id_hex or "").lower()
        if not MESSAGE_ID.fullmatch(event_id):
            self.audit.write("drop", operator=verdict.operator, what="reaction",
                             reason="malformed reaction id", emoji=own_text(ev.emoji))
            return
        mid = REACTION_PREFIX + event_id
        target = ev.target_message_id_hex.lower()
        target = target if MESSAGE_ID.fullmatch(target) else None
        if not self.store.claim_inbound(mid, target, sender_key=ev.actor.account_id_hex.lower()):
            self.audit.write("drop", operator=verdict.operator, message_id=mid, ref=canonical_ref(mid),
                             what="reaction", reason="replayed reaction id", emoji=own_text(ev.emoji))
            return
        self.remember_sender(mid, ev.actor.account_id_hex.lower())
        self.audit.write("inbound", operator=verdict.operator, message_id=mid, ref=canonical_ref(mid),
                         what="reaction", emoji=own_text(ev.emoji), target=target)
        if not await self.check_group() or not self.authorised(mid):   # the latch may have come meanwhile
            self.deny(mid, "reaction")
            return
        if self.store.get("operator_seen_at") is None:
            with self.store.transaction():      # the marker and the notice: both or neither
                self.store.set("operator_seen_at", now())
                self.post("ready", READY_NOTICE, None, owner=None)
        await self.react(mid, ev.emoji, target)

    async def react(self, mid: str, emoji: str, target: str | None) -> None:
        """What a claimed reaction does (R27): on an approval card an approve or deny emoji decides and any
        other is ignored; on a question or merge card the emoji is the answer; on anything else it is
        ignored. Ignored reactions get no reply. On a bump an approve or deny emoji gets B6's hint."""
        bump = self.store.bump_for_message(target)
        if bump is not None and verbs.emoji_action(emoji) is not None:
            self.bump_hint(mid, bump)
            return
        ask_id = self.store.ask_for_message(target)
        row = None if ask_id is None else self.store.ask(ask_id)
        if row is None or commands.has_control_chars(emoji):
            with self.store.transaction():
                self.store.set_inbound(mid, "done")
                if row is not None:             # accepted on a card, though ignored (B14)
                    self.touch(row.ask_id)
            self.audit.write("event", action="ignored", what="reaction", message_id=mid, ask_id=ask_id)
            return
        if row.kind != "approval":
            await self.ask_reply(mid, row.ask_id, emoji, reaction=True)
            return
        action = verbs.emoji_action(emoji)
        card = self.approval_card_for(target)
        if action is None or card is None:
            with self.store.transaction():
                self.store.set_inbound(mid, "done")
                self.touch(row.ask_id)
            self.audit.write("ask", action="reaction-ignored", message_id=mid, ask_id=row.ask_id,
                             emoji=own_text(emoji))
            return
        self.store.set_inbound(mid, "executing")
        if not self.authorised(mid):
            self.deny(mid, "decision")
            return
        await self.decide_card(mid, card, action, "")

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
            with self.store.transaction():      # refused, yet accepted activity on a card it replies to (B14)
                self.touch_card(target)
                self.finish(mid, "dropped", CONTROL_REFUSED, "refused")
            return
        if self.s.fulfillment_enabled and text.strip() == "!fulfill":
            try:
                if not self.authorised(mid) or not self.store.inbound_sender(mid):
                    with self.store.transaction():
                        self.deny_rows(mid)
                    return
                try:
                    with self.store.transaction():
                        result = self.store.fulfillment.confirm(
                            target, principal=self.store.inbound_sender(mid) or "", message_id=mid)
                        self.finish(mid, "done", result.message, "cmd")
                except fulfillment.Invalid as exc:
                    self.finish(mid, "done", str(exc), "cmd")
                return
            except sqlite3.Error as exc:
                reason = "store unavailable"
                self.audit_quietly("fulfillment", action="store-unavailable", error=type(exc).__name__)
            except Exception as exc:  # noqa: BLE001 - rollback; fixed public text, audit error type only
                reason = "internal error"
                self.audit_quietly("fulfillment", action="handler-failed", error=type(exc).__name__)
            # A persistent outage leaves the original inbound recoverable. Refusal delivery and
            # successful confirmation settlement both receive the same bounded database protection.
            try:
                self.finish(mid, "done", "Fulfillment unavailable: " + reason + ".", "cmd")
            except sqlite3.Error:
                self.audit_quietly("fulfillment", action="store-unavailable")
            return
        bump = self.store.bump_for_message(target)
        if bump is not None and (not text.startswith("!") or text[1:].split(maxsplit=1)[:1] in CARD_COMMANDS):
            self.bump_hint(mid, bump)                       # B6: never decided, answered or pasted
            return
        ask_id = self.store.ask_for_message(target)
        if ask_id is not None and not text.startswith("!"):
            await self.ask_reply(mid, ask_id, text)        # R13/R14: never pasted to the agent
            return
        try:
            cmd = commands.parse(text)
        except commands.CommandError as exc:
            with self.store.transaction():      # e.g. an oversized `!deny` on a card: its clock moves (B14)
                self.touch_card(target)
                self.finish(mid, "done", show(str(exc), False), "cmd")   # may echo the mistyped word
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
                if cmd.arg == "retire":
                    from heterodyne.admind.retirement import retire
                    await retire(self, mid, cmd.rest or "", target)
                    return
                await self.list_asks(mid, cmd.arg)
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
        source_owners: set[str] = set()
        batch_details = post is not None and post.kind == "batch"
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
            if not self.authorised(mid):  # refresh policy before reading each contribution
                self.deny(mid, "command")
                return
            owners = self.request_owners(row.reply_to)
            if ((batch_details and row.status != "batched") or not owners
                    or any(k not in self.operators for k in owners)):
                continue
            source_owners.update(owners)
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
                self.store.publish_details(mid, f"details:{mid}", mid, 2, tuple(sorted(source_owners)))
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
        # Recovery, subscription, and active membership-transition gates hold dispatch.
        # Requester authority is refreshed below before any prompt reaches the agent.
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
                          replace: str | None = None, notice: bool = False) -> None:
        """Release the reservation and its anchor. Hooks accepted so far (a hook releasing the turn passes
        its own index instead: what was accepted behind it is newer) can no longer start or end it.
        Three steps, in this order. (a) One transaction holds only the safety and state change: the anchor
        and reservation cleared, and the idle state if `idle`. It commits on its own, so call this outside
        any transaction a later fallible step could roll back. (b) The operator's notice is queued in its
        own transaction; a failure there is contained and undoes nothing from (a). (c) A quiet audit.
        `replace` (a fixed word) is the caller's intent to replace the pane: it is stored in (a), so no
        restart can see the turn idle without also seeing that the old pane must not be adopted. `notice`
        also allocates the login-change notice's outbox key in (a), as `pending_notice` (one per replacement:
        an unposted one is kept); `flush_pending_notice` posts it."""
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
            if notice and self.store.get("pending_notice") is None:
                self.store.set("pending_notice", f"login-changed:{self.next_seq('login_seq')}")
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
            self.post(key, HOOK_LOST_NOTICE, None, owner=None)

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
            self.post(f"long-turn:{self.store.get('busy')}", LONG_TURN, anchor,
                      owner=self.request_owners(anchor or self.store.get("in_flight")))
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
        before the turn was released) changes no turn state. Its own text may be staged,
        but delivery still requires durable requester provenance; unknown output is held.
        """
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
                            self.post(f"{key}:{i}", part, reply_to, owner=self.request_owners(reply_to))
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

    def hold_untrusted_turn(self, row: TurnRow) -> bool:
        """Retain an ineligible contribution separately, so it cannot poison another reply's batch.

        The caller has refreshed policy. Reuse terminal turn status and durable outbox dispositions;
        the retained payload is never automatically released or added to a later batch.
        """
        owners = self.request_owners(row.reply_to)
        if owners and all(key in self.operators for key in owners):
            return False
        status = "suppressed" if owners else "quarantined"
        reason = "requester-revoked" if owners else "unproven-owner"
        key = f"held-turn:{row.turn_id}"
        with self.store.transaction():
            self.post(key, row.text, row.reply_to, owner=owners)
            self.store.hold_group(key, status, reason)
            self.store.set_turn_status(row.turn_id, "summarized", detach_batch=True)
        self.audit_quietly("reply", action=status, reason=reason, turn=row.turn_id)
        return True

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
        if not self.refresh_authority():
            return  # unreadable policy is temporary; keep the summary recoverable
        if self.hold_untrusted_turn(row):
            return
        if not self.authorised(row.reply_to):
            return  # subscription/startup/recovery holds must not discard pending summary work
        try:
            text = await summarize.summarize(self.summarizer_argv, self.s.state_dir / "summarizer", row.text,
                                             self.summary_timeout, account=self.s.summarizer_account)
            with self.store.transaction():
                for i, part in enumerate(chunk.split(text, self.s.chunk_chars)):
                    self.post(f"{row.key}:s{i}", part, row.reply_to, owner=self.request_owners(row.reply_to))
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
            if not self.refresh_authority():
                return  # unreadable policy leaves every contribution recoverable
            rows = [row for row in self.store.batch_turns(batch)
                    if row.status == "batched" and not self.hold_untrusted_turn(row)]
            key = f"batch:{batch}.{attempt}"        # a new key per attempt: the outbox ignores a known key
            text = await asyncio.to_thread(render_batch, rows)
            if not self.refresh_authority():
                return
            # Rendering yields: exclude newly revoked contributions before the next render pass.
            current = [row for row in rows if not self.hold_untrusted_turn(row)]
            if len(current) != len(rows):
                continue
            with self.store.transaction():
                if rows:
                    self.post(key, text, None, owner=self.batch_owners(rows))
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

    def prepare_delivery(self, row: OutboxRow) -> bool:
        """Classify durable ownership/cadence without blocking unrelated selectable rows."""
        owners = self.store.row_owners(row.seq)
        if row.ownership_kind == "unknown" or (row.ownership_kind == "request" and not owners):
            self.store.hold_group(row.delivery_group, "quarantined", "unproven-owner")
            self.audit_quietly("send", action="quarantined", reason="unproven-owner")
            return False
        if row.ownership_kind == "request" and not all(key in self.operators for key in owners):
            self.store.hold_group(row.delivery_group, "suppressed", "requester-revoked")
            self.audit_quietly("send", action="suppressed", reason="requester-revoked")
            return False
        if re.fullmatch(r"membership-event:[0-9]+", row.key):
            state = guard_state.notice_state(self.store.get("membership_notice"))
            last = state["last_sent"]
            at = self.wallclock()
            if last is not None:
                if last > at:
                    state["last_sent"] = last = at
                    self.store.set("membership_notice", json.dumps(state, sort_keys=True))
                due = last + self.s.group_check_seconds
                if at < due:
                    self.store.hold_group(row.delivery_group, "delayed", "notice-cadence", due)
                    return False
        return True

    def sent_row(self, row: OutboxRow, message_id: str | None) -> None:
        with self.store.transaction():
            self.store.mark_sent(row.seq, message_id)
            if re.fullmatch(r"membership-event:[0-9]+", row.key):
                state = guard_state.notice_state(self.store.get("membership_notice"))
                if state["unresolved"] == row.delivery_group:
                    state["unresolved"] = None
                    state["last_sent"] = self.wallclock()
                    self.store.set("membership_notice", json.dumps(state, sort_keys=True))

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
                self.flush_membership_notice()
                self.store.release_delayed(self.wallclock())
                row = self.store.next_pending()     # re-read each time: a new lane-1 row goes next (B14)
                if row is None:
                    return
                if not self.prepare_delivery(row):
                    continue
                timer = self._backoff.get(row.seq)
                if timer is not None and not timer.done():
                    return                  # its retry is not due; its timer or a new row wakes the loop
                reply_to = row.reply_to
                if row.thread_key is not None:      # resolved and stored before the send, never awaited (K22)
                    reply_to, why, parent = self.store.resolve_thread(row.seq)
                    if why == "parent-held":
                        continue
                    if why is not None:
                        self.audit.write("thread_fallback", key=row.key, parent=parent, why=why)
                try:
                    sent = await self.client.send_final(self.account, self.group, redact(row.text),
                                                        reply_to, row.key)
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
                    self.sent_row(row, sent.message_ids_hex[0] if sent.message_ids_hex else None)
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
            if re.fullmatch(r"membership-event:[0-9]+", key):
                state = guard_state.notice_state(self.store.get("membership_notice"))
                if state["unresolved"] == key:
                    state["unresolved"] = None
                    state["last_sent"] = self.wallclock()
                    state["pending"] = True
                    self.store.set("membership_notice", json.dumps(state, sort_keys=True))
            part_parent = self.report_part(key)
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
        if part_parent is not None:
            self.audit_quietly("part_failed", key=key, parent=part_parent)

    def report_part(self, key: str) -> str | None:
        """K22f, in the caller's transaction: a continuation part that failed for good gets one fixed reply
        under part 1 (top-level if part 1 has no message ID), so the operator knows the post is incomplete.
        Returns part 1's key when it queued one. Part 1 itself and `!details` parts are not reported."""
        m = _CONTINUATION.fullmatch(key)
        text = None if m is None else self.store.outbox_text(key)
        part = None if text is None else _PART.match(text)
        if m is None or part is None:
            return None
        parent = f"{m.group(1)}:0"
        self.post(f"partfail:{key}", PART_FAILED.format(k=part.group(1), n=part.group(2)),
                  self.store.sent_message_id(parent), owner=self.ownership_for_key(key))
        return parent

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
