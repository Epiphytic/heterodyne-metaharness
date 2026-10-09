"""The bead and workstream state model (ADR 0001 §4.3, §5.2) as data plus pure functions.

The journal records one `BeadState` per bead with a reason from a fixed vocabulary, so the Marmot
surface (plan 6) always has a concrete answer to "what is this bead doing, and why". Beads stay the
source of truth (§3.3): these states are rebuilt from beads when the journal is lost.
"""

from collections.abc import Iterable
from enum import StrEnum


class BeadState(StrEnum):
    CLAIMING = "claiming"            # a claim is in flight; never executed until read back as ours
    STARTING = "starting"            # claimed; worktree and launch in progress
    RUNNING = "running"              # an agent session is working on it
    PARKING = "parking"              # the park journal is in progress
    PARKED = "parked"                # waits on blocking beads (claimed, in_progress, v2:parked)
    WAITING_INPUT = "waiting_input"  # parked on an operator question, picker or permission prompt
    HELD = "held"                    # parked by the operator (/stop); resumes only when they release it
    STUCK = "stuck"                  # needs a human; the reason says why
    RESUMING = "resuming"            # the resume journal is in progress
    DEFERRED = "deferred"            # claimed, `v2:deferred`, no session: waits on quota or an account (D5)
    CLOSED = "closed"
    DROPPED = "dropped"              # not ours: the claim was lost or abandoned


class Reason(StrEnum):
    """Why a bead or a workstream is in its state. Fixed wording lives with the renderer (plan 6)."""
    CLAIM_UNCERTAIN = "claim_uncertain"
    CLAIM_LOST = "claim_lost"
    CLAIM_ABANDONED = "claim_abandoned"
    UNCLAIMABLE = "unclaimable"                # listed as ready, but btq's claim can never take it
    ROUTING_CHANGED = "routing_changed"
    WORKTREE_FAILED = "worktree_failed"
    LAUNCH_FAILED = "launch_failed"            # the runtime confirmed nothing is running
    LAUNCH_UNCERTAIN = "launch_uncertain"      # the runtime can't say whether the launch started
    LAUNCH_UNRECORDED = "launch_unrecorded"    # no readable launched-session record on the bead
    STOP_UNCONFIRMED = "stop_unconfirmed"      # a session that must end could not be confirmed ended
    JOURNAL_LOST = "journal_lost"              # beads show work wsd's journal has no record of
    SESSION_DEAD = "session_dead"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    PARK_FAILED = "park_failed"
    BLOCKED_ON_BEAD = "blocked_on_bead"
    WAITING_ON_OPERATOR = "waiting_on_operator"
    HELD_BY_OPERATOR = "held_by_operator"
    NEEDS_HUMAN = "needs_human"
    BEADS_UNREACHABLE = "beads_unreachable"
    ACTIONS_UNRECONCILED = "actions_unreconciled"
    CONFIG_INVALID = "config_invalid"
    UNEXPECTED_STATE = "unexpected_state"
    # A DEFERRED bead's `account_changed` deferral: its session's account would change, and the adapter
    # can't switch (D7). It waits for a re-gate (process start, `wsctl reload` or release), never a timer.
    ACCOUNT_CHANGED = "account_changed"
    # A DEFERRED bead's quota deferral: no eligible account until its `defer_until`. A PARKED/QUOTA row
    # is only AU-5's interim shelve, which recovery converts (AU-4 §6).
    QUOTA = "quota"
    # Only on the unclaimed row of a ready bead whose gate gives `account_changed` (never claimed): the
    # claimed deferral keeps ACCOUNT_CHANGED, so cleanup never confuses the two.
    ACCOUNT_REPOINTED = "account_repointed"


class WsState(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    ALL_BLOCKED = "all_blocked"
    PAUSED = "paused"
    HELD = "held"      # pickup is held for a workstream-level reason (see `holds`)
    STUCK = "stuck"    # nothing runs and at least one bead needs a human
    DEFERRED = "deferred"      # waiting only on deferrals, or with a quota wake armed (§5.2): never idle


TERMINAL = frozenset({BeadState.CLOSED, BeadState.DROPPED})
ACTIVE = frozenset({BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.PARKING,
                    BeadState.RESUMING})
WAITING = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD})
_PARKED_LIKE = WAITING | {BeadState.STUCK}

# Every non-terminal state may also go to CLOSED: anyone with the right can close a bead at any time.
# Recovery and the per-pickup observation rebuild rows from beads with `Journal.adopt`, which skips this
# table: these are the transitions wsd's own operations make.
ALLOWED: dict[BeadState | None, frozenset[BeadState]] = {
    None: frozenset({BeadState.CLAIMING}),      # wsd's own operations start every bead with a claim
    BeadState.CLAIMING: frozenset({BeadState.STARTING, BeadState.DROPPED, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.STARTING: frozenset({BeadState.RUNNING, BeadState.PARKING, BeadState.STUCK, BeadState.CLOSED,
                                   BeadState.DEFERRED}   # DEFERRED: a guard defer (AU-4)
                                  | WAITING),    # WAITING: shelved, not runnable when its launch came
    BeadState.RUNNING: frozenset({BeadState.PARKING, BeadState.RESUMING, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.PARKING: frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD,
                                  BeadState.STUCK, BeadState.CLOSED,
                                  BeadState.DEFERRED}),   # the end of every defer tail (AU-4)
    # PARKING: a due resume, or a PARKED/QUOTA row's conversion, defers (AU-4)
    BeadState.PARKED: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED, BeadState.PARKING},
    BeadState.WAITING_INPUT: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},
    # RESUMING: only by release; DEFERRED: the release of a bead that still carries `v2:deferred` (AU-4)
    BeadState.HELD: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED, BeadState.DEFERRED},
    BeadState.RESUMING: frozenset({BeadState.RUNNING, BeadState.PARKING, BeadState.PARKED,
                                   BeadState.WAITING_INPUT, BeadState.HELD, BeadState.STUCK,
                                   BeadState.CLOSED,
                                   BeadState.DEFERRED}),  # a guard defer or an abandoned undefer (AU-4)
    BeadState.STUCK: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED, BeadState.DEFERRED},  # as HELD
    # PARKING: a re-gate gives a deadline; RESUMING: undefer; the rest: precedence and escalation (AU-4)
    BeadState.DEFERRED: frozenset({BeadState.PARKING, BeadState.RESUMING, BeadState.HELD, BeadState.STUCK,
                                   BeadState.CLOSED}),
    BeadState.CLOSED: frozenset({BeadState.CLAIMING}),   # a reopened bead can be claimed again
    BeadState.DROPPED: frozenset({BeadState.CLAIMING}),
}


class IllegalTransition(Exception):
    pass


def allowed(src: BeadState | None, dst: BeadState) -> bool:
    """A same-state "transition" only updates the reason, and is always allowed."""
    return src == dst or dst in ALLOWED[src]


def check(src: BeadState | None, dst: BeadState) -> None:
    if not allowed(src, dst):
        raise IllegalTransition(f"{src} -> {dst}")


def ws_state(paused: bool, holds: Iterable[Reason], beads: Iterable[BeadState],
             wake: bool = False) -> WsState:
    """The one-word workstream state for /workstreams (§6.3). Pickup has already run, so "idle" really
    means nothing is ready and nothing is in progress (§5.2). `wake`: pickup armed a quota wake time, which
    makes a workstream that would otherwise be idle DEFERRED (AU-5 §3.6). Every non-terminal row being
    DEFERRED, whatever the reason, makes one that would otherwise be all-blocked DEFERRED (AU-4 §2.2)."""
    states = set(beads) - TERMINAL
    if set(holds):
        return WsState.HELD
    if paused:
        return WsState.PAUSED
    if states & ACTIVE:
        return WsState.RUNNING
    if BeadState.STUCK in states:
        return WsState.STUCK
    if states:
        return WsState.DEFERRED if states == {BeadState.DEFERRED} else WsState.ALL_BLOCKED
    return WsState.DEFERRED if wake else WsState.IDLE
