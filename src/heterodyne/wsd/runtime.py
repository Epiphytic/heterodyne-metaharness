"""Seams to later plans: the agent runtime (plan 4) and action reconciliation (plan 5).

Plan 3 never launches an agent itself. It calls `AgentRuntime`, which plan 4 implements (adapters and
the sandbox backend chosen in ADR revision 14). Nothing here names an adapter or a sandbox. Until a real
runtime is wired in, `NoRuntime` reports itself unavailable, and wsd holds every workstream rather than
claim work it can't run.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from heterodyne.wsd.beads import BeadsAdapter

ACTION_STATE = "action_state"
# The action states that say nothing is in flight (ADR §3.3: pending, executing, then succeeded, failed
# or uncertain). A pending action has not started. Every other value, including one wsd doesn't know,
# is unsettled.
SETTLED_ACTIONS = frozenset({"pending", "succeeded", "failed"})


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

    def launch(self, spec: LaunchSpec) -> None:
        """Start (or, with `spec.resume`, resume) the session. Idempotent on `spec.session_key`: a live
        session is left alone. Returns once the session is live. Raises LaunchFailed when nothing
        started, RuntimeUnavailable when nothing was attempted, and LaunchUncertain otherwise.

        `spec.resume` is prepared identity, not launch evidence: the bead's record names this key, but a
        first pickup shelved after writing the record never launched. The runtime resumes the session
        if it holds state for the key, creates it if it can establish that none ever existed, and
        raises RuntimeUnavailable if it can't tell (plan 4's contract, tested there)."""
        ...

    def stop(self, session_key: str) -> None:
        """Interrupt the session and wait until it has ended. Idempotent: an ended session is fine.
        Raises RuntimeUnavailable if it can't confirm the session has ended."""
        ...


class NoRuntime:
    """The runtime until plan 4: it launches nothing, and since it can't list sessions it never lets wsd
    conclude that none are running."""

    def available(self) -> bool:
        return False

    def sessions(self, ws: str) -> list[Session]:
        raise RuntimeUnavailable("no agent runtime is configured")

    def launch(self, spec: LaunchSpec) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")

    def stop(self, session_key: str) -> None:
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
