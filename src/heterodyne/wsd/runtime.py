"""Seams to later plans: the agent runtime (plan 4) and action reconciliation (plan 5).

Plan 3 never launches an agent itself. It calls `AgentRuntime`, which plan 4 implements (adapters and
the sandbox backend chosen in ADR revision 14). Nothing here names an adapter or a sandbox. Until a real
runtime is wired in, `NoRuntime` reports itself unavailable, and wsd holds every workstream rather than
claim work it can't run.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.usage import Observation

ACTION_STATE = "action_state"
# The action states that say nothing is in flight (ADR §3.3: pending, executing, then succeeded, failed
# or uncertain). A pending action has not started. Every other value, including one wsd doesn't know,
# is unsettled.
SETTLED_ACTIONS = frozenset({"pending", "succeeded", "failed"})

# The LaunchFailed texts the guard acts on, compared by equality (plan 4b E1, E3, E9). A runtime that
# refuses for these reasons raises exactly these texts and nothing else with them.
LOGIN_STALE = "the login expires within the session's maximum lifetime; refresh it on the host"
KEY_CHANGED = "the account's credential key changed after the launch was pinned"
# Plan 4c (E23): defined by T7, acted on from T8. The LaunchFailed text for an entry with an empty install
# pin, and the guard's escalation detail when the profile's adapter has no selected install.
INSTALL_UNPINNED = "the launch entry pins no install"
NOT_SELECTED = "no install is selected for the profile's adapter; choose one with `wsctl harness use`"
# Plan 4c T9: one text for a tainted install: `harness.verify`'s refusal and the park's `why` (Task 10).
INSTALL_TAINTED = ("the install is tainted: a verification of it failed; check it with "
                   "`wsctl harness verify` or remove it")


class Liveness(StrEnum):
    LIVE = "live"
    UNKNOWN = "unknown"     # listed, but the runtime can't tell whether it runs: treated as live


@dataclass(frozen=True)
class Session:
    """A session the runtime may still be running. Ended sessions are not listed."""
    key: str
    ws: str
    bead: str
    role: str
    liveness: Liveness


@dataclass(frozen=True)
class LaunchSpec:
    ws: str
    bead: str
    role: str
    profile: str
    session_key: str        # ids.role_session(bead, role, profile), as recorded on the bead
    label: str              # "<bead> · <role> · <title>" (§4.1)
    worktree: Path
    resume: bool            # a resume operation: the record names the key, not a session that ran (launch)
    ref: str | None = None  # the message or event that caused the launch, for progress reactions
    # The launch entry (AU-3 §2.4). The runtime tags the launch with (session key, generation): its tmux
    # options, the hook-spool launch tag and Claude's native ID. `native_id` is the session key for a
    # first Claude launch, the latest launched entry's native ID for a resume, and None for Codex before
    # its thread ID is known. `account` and `model` are the entry's pinned values, never re-read.
    generation: int = 1
    native_id: str | None = None
    account: str = "default"
    model: str = ""
    repo: Path | None = None    # the bead's repository, as recorded: the only trusted source of its git dirs
    # The entry's pinned credential key (plan 4b E1). The runtime recomputes the account's key under the
    # D8 lock and refuses with KEY_CHANGED unless it equals this; an empty key is refused the same way.
    credential_key: str = ""
    # A cross-account relaunch without (b): a fresh native session, never a resume (D7, plan 4b E10).
    handoff: bool = False
    # The entry's pinned install and its manifest digest (plan 4c T7, D2); empty when the adapter had no
    # selection; the runtime verifies exactly this pin, and refuses an empty one (INSTALL_UNPINNED).
    install: str = ""
    install_digest: str = ""
    brief: str = ""         # plan 5i: pasted once the self-test's turn has ended


@dataclass(frozen=True)
class Started:
    """What `launch` created on wsd's own tmux server. Journaled as the generation's `started` receipt."""
    tmux_session: str
    tmux_pane: str
    pane_pid: int
    native_id: str | None       # the session's native ID, when the runtime knows it


@dataclass(frozen=True)
class UsageSeen:
    """A session's own usage report (untrusted, D3). The runtime attributes it to the launch whose channel
    carried it, never to anything the payload says."""
    session_key: str
    generation: int
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class LimitSeen:
    """A session reported reaching its usage limit (untrusted, D6). `reset_hint` is the payload's own
    reset time, if it gave one; wsd clamps it."""
    session_key: str
    generation: int
    reset_hint: int | None


@dataclass(frozen=True)
class InstallTainted:
    """A launch found the install of a live session tainted (plan 4c E17): its taint marker is written, and
    one of these is reported for each live session on the install. wsd acts on it from Task 10."""
    session_key: str
    generation: int
    adapter: str
    install: str


@dataclass(frozen=True)
class ExecDirCleared:
    """A launch found leftover content in one of the adapter's empty read-only exec directories (plan 4c E25)
    and cleared it: Amendment 1 §4.2's bounded summary, counts and at most 16 sanitized names, never any
    contents. wsd journals it as `exec_dir_cleared`, once per session, generation and path."""
    session_key: str
    generation: int
    adapter: str
    path: str                   # relative to the session's home
    files: int
    links: int
    dirs: int
    bytes: int
    names: tuple[str, ...]


type Signal = UsageSeen | LimitSeen | InstallTainted | ExecDirCleared


SessionEventKind = Literal["stop", "close", "ask", "question", "damaged"]


@dataclass(frozen=True)
class SessionEvent:
    """A Stop or a `ws-request` a session sent through its own socket (plan 5i part 1). The runtime
    attributes it to the launch whose socket carried it (`ws`, `bead`, `session_key`, `generation`); every
    other field is untrusted. `event_id` comes from the generation's event log on the host
    (`<key>:g<gen>:e<n>`, `n` from 1, with no gaps but a damaged run's), so it is the same after a wsd restart
    and wsd dedupes on it. `turn` is durable too: a Stop's ordinal among the generation's forwarded Stops
    (from 1), or for a request the turn it was sent in. `text` is a Stop's last message (cut, None when
    absent) or a close's evidence; `body` is an ask's or a question's JSON object. `close_requested` marks a
    Stop after a close accepted in the same generation. A `damaged` event marks log lines lost after they were
    accepted: every number after the event before it, up to its own; it carries nothing else."""
    event_id: str
    kind: SessionEventKind
    ws: str
    bead: str
    session_key: str
    generation: int
    turn: int
    text: str | None = None
    body: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}))
    close_requested: bool = False

    @property
    def seq(self) -> int:
        """`n`: the event's place in its generation's log."""
        return int(self.event_id.rsplit(":e", 1)[1])


@dataclass(frozen=True)
class EventBatch:
    """What `session_events` read: events ordered by (key, generation, n), with no gap after each key's
    cursor. `ends[(key, g)] = n`: generation g's log is final at n (nothing will follow it). `more`: a
    key had more events than the limit."""
    events: tuple[SessionEvent, ...] = ()
    ends: Mapping[tuple[str, int], int] = field(
        default_factory=lambda: cast(Mapping[tuple[str, int], int], {}))
    more: bool = False


class SteerRefused(Exception):
    """Nothing was delivered: the session isn't running that generation, is mid-turn, or the paste failed
    before anything reached its pane. Safe to retry later or to deliver another way."""


class SteerUncertain(Exception):
    """The text may have reached the session (a paste that failed part way). Never retried blindly."""


Action = Literal["land", "keep", "discard"]


@dataclass(frozen=True)
class UnlandedView:
    """One unlanded session (btq-xb56t), for `wsctl unlanded`. `error` says, in fixed wording, why the
    tip or its comparison can't be read; `subjects` are the agent's, made printable, and untrusted."""
    short_id: str
    generation: int
    bead: str
    branch: str
    running: bool = False
    landed: str = ""
    tip: str | None = None
    error: str = ""
    ahead: int | None = None
    behind: int | None = None
    fast_forward: bool = False
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    """A resolve whose side effects are all done, read from its durable intent. Its journal entry and
    `finish_resolution` are still owed; the workstream stays held until both are done."""
    ws: str
    bead: str
    short_id: str
    generation: int
    action: Action
    tip: str | None
    ref: str            # the keep ref, or ""
    key: str
    end: "Ended | None"     # wpyv5: the record's end, carried to the journal; None from an older intent


class ResolveRefused(Exception):
    """The resolve can't be done as asked, and nothing was changed by this request. Fixed wording."""


class ResolveFailed(Exception):
    """A resolve step failed; its intent stays at its last checkpoint, and asking again goes on from
    there. Fixed wording."""


class EndCause(StrEnum):
    """How a generation ended (g08sd). Only a stop the runtime began on a generation it confirmed live,
    by wsd or by the deadline's reaper, is evidence of anything but a death."""
    DIED = "died"
    STOPPED = "stopped"                  # wsd stopped it, confirmed live when the stop began
    LIFETIME = "lifetime"                # the lifetime stop, confirmed live when it began
    REFUSED = "refused"
    LEGACY_LIFETIME = "legacy_lifetime"  # a pre-g08sd lifetime end: liveness was never checked


@dataclass(frozen=True)
class Ended:
    generation: int
    cause: EndCause
    reached_running: bool               # this generation's start was confirmed
    ran_seconds: int | None             # for the operator's detail only; None when either time is missing


class RuntimeUnavailable(Exception):
    """No runtime can act right now (none configured, or its backend is down). Nothing was attempted."""


class LaunchFailed(Exception):
    """The runtime confirms this launch started nothing. The reason is the exception text (fixed
    wording, no secrets)."""


class LaunchUncertain(Exception):
    """The launch was attempted and the runtime can't say whether a session is running. Any exception
    from `launch` other than LaunchFailed and RuntimeUnavailable is treated the same way."""


class AgentRuntime(Protocol):
    def available(self) -> bool:
        """Whether launches can be attempted at all. Checked before every claim."""
        ...

    def sessions(self, ws: str) -> list[Session]:
        """Every session of the workstream that may be running: each key `launch` was called with, until
        the runtime has confirmed that session ended. This includes sessions started before a wsd
        restart and sessions whose bead wsd no longer knows. Raises RuntimeUnavailable if it can't list
        them; a partial list is never returned."""
        ...

    def launch(self, spec: LaunchSpec) -> Started:
        """Start (or, with `spec.resume`, resume) the session. Idempotent on `spec.session_key`: a live
        session is left alone. Returns once the session is live, with what it created. Raises
        LaunchFailed when nothing started and RuntimeUnavailable when nothing was attempted; any other
        exception, LaunchUncertain included, means the runtime can't say.

        `spec.resume` is prepared identity, not launch evidence: the bead's record names this key, but a
        first pickup shelved after writing the record never launched. The runtime resumes the session
        if it holds state for the key, creates it if it can establish that none ever existed, and
        raises RuntimeUnavailable if it can't tell (plan 4's contract, tested there)."""
        ...

    def stop(self, session_key: str) -> None:
        """Interrupt the session and wait until it has ended. Idempotent: an ended session is fine.
        Raises RuntimeUnavailable if it can't confirm the session has ended."""
        ...

    def expire(self, ws: str, now: int) -> None:
        """Stop each of the workstream's sessions whose maximum lifetime is up (§7): at a turn boundary
        once the stop window opens, by a hard interrupt at the deadline. `now` is the scheduler's clock.
        Raises RuntimeUnavailable if a stop can't be confirmed. The sweep then resumes the bead."""
        ...

    def signals(self, ws: str) -> list[Signal]:
        """Drain the reports the workstream's sessions have sent since the last call, oldest first. Each
        is attributed to the (session key, generation) whose channel carried it. Never raises: a report
        the runtime can't attribute is never queued."""
        ...

    def session_events(self, ws: str, since: Mapping[str, tuple[int, int]], limit: int) -> EventBatch:
        """Plan 5i: the accepted events of the workstream's sessions after each key's cursor
        `since[key] = (generation, n)` (default `(1, 0)`), from the cursor's generation up to the session's
        current one, at most `limit` per key. Raises RuntimeUnavailable when the sessions can't be read;
        never drops or reorders an event."""
        ...

    def steer(self, session_key: str, generation: int, text: str) -> None:
        """Paste `text` into the session, only at a turn boundary its socket has seen (plan 5i, ADR
        §5.6). Raises SteerRefused when nothing was delivered and SteerUncertain when it may have been."""
        ...

    def subscribe(self, wake: Callable[[str], None], ops: Mapping[str, frozenset[str]]) -> None:
        """Called once by wsd before its first pickup: `wake(ws)` is called, from any thread, after a
        session event is logged, and a session of workstream `ws` may use only the `ws-request` ops in
        `ops[ws]` (the rest stay unsupported; a workstream not in `ops` has none). `wake` must not block."""
        ...

    def unlanded(self, ws: str) -> list[UnlandedView]:
        """Each of the workstream's sessions whose commits were not landed (docs/wsd.md §5). A failure on
        one is shown on it. Raises RuntimeUnavailable if the sessions can't be listed."""
        ...

    def resolve_unlanded(self, ws: str, sid: str, generation: int, action: Action,
                         tip: str | None) -> Resolution:
        """Land, keep or discard one unlanded session's commits and remove its directory, through a durable
        intent that a repeat of the same request goes on from. Raises ResolveRefused (nothing changed),
        ResolveFailed, or RuntimeUnavailable when what runs can't be listed."""
        ...

    def finish_resolution(self, ws: str, sid: str, generation: int) -> None:
        """Remove a finished resolve's intent, once its journal entry is committed: the hold ends here."""
        ...

    def ended(self, session_key: str) -> Ended | None:
        """The latest generation's end, once the runtime has confirmed it, or None when the runtime has
        no ended record for the key. Raises RuntimeUnavailable if the record can't be read."""
        ...


class NoRuntime:
    """The runtime until plan 4: it launches nothing, and since it can't list sessions it never lets wsd
    conclude that none are running."""

    def available(self) -> bool:
        return False

    def sessions(self, ws: str) -> list[Session]:
        raise RuntimeUnavailable("no agent runtime is configured")

    def launch(self, spec: LaunchSpec) -> Started:
        raise RuntimeUnavailable("no agent runtime is configured")

    def stop(self, session_key: str) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")

    def expire(self, ws: str, now: int) -> None:
        return None                  # it runs nothing

    def signals(self, ws: str) -> list[Signal]:
        return []                    # it runs nothing

    def session_events(self, ws: str, since: Mapping[str, tuple[int, int]], limit: int) -> EventBatch:
        return EventBatch()          # it runs nothing

    def steer(self, session_key: str, generation: int, text: str) -> None:
        raise SteerRefused("no agent runtime is configured")

    def subscribe(self, wake: Callable[[str], None], ops: Mapping[str, frozenset[str]]) -> None:
        return None

    def unlanded(self, ws: str) -> list[UnlandedView]:
        return []

    def resolve_unlanded(self, ws: str, sid: str, generation: int, action: Action,
                         tip: str | None) -> Resolution:
        raise ResolveRefused("no runtime holds sessions")

    def finish_resolution(self, ws: str, sid: str, generation: int) -> None:
        return None
    def ended(self, session_key: str) -> Ended | None:
        raise RuntimeUnavailable("no agent runtime is configured")


class ActionReconciler(Protocol):
    def unresolved(self, ws: str) -> list[str]:
        """IDs of beads, closed ones included, whose action is in any state but pending, succeeded or
        failed and not yet reconciled against its target (§5.4). Raises BeadsUnavailable if that can't
        be established."""
        ...


class HoldingReconciler:
    """Plan 3's reconciler: it can't check targets (plan 5 can), so it only finds unsettled actions and
    reports every one as unresolved. wsd holds the workstream until they are settled; it never assumes
    there are none."""

    def __init__(self, beads: BeadsAdapter) -> None:
        self.beads = beads

    def unresolved(self, ws: str) -> list[str]:
        return self.beads.with_metadata(ws, ACTION_STATE, SETTLED_ACTIONS)
