"""One workstream's settings, and where a bead runs: repository, worktree, profile and session key.

`place` is used only where wsd starts something new: a pickup's worktree and record, and a release that
must record a bead whose launch was never recorded. Everything that continues existing work (resume,
park, recovery) reads the launched-session record on the bead instead, so a configuration change never
redirects a running or parked bead to another session or worktree.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from heterodyne.harness.selection import InstallPin
from heterodyne.wsd import ids
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.beads import Bead, BeadsAdapter, SessionRecord
from heterodyne.wsd.brief import NoNotes, ResumeNotes
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.headroom import UsageSettings
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime, SessionEvent
from heterodyne.wsd.usage import NoUsageReader, UsageReader

if TYPE_CHECKING:
    from heterodyne.wsd.park import Parker

REPO_KEY = "repo"            # bead metadata naming one of the workstream's [repos]
DEFAULT_REPO = "default"
LABEL_TITLE_CHARS = 40


class ConfigInvalid(Exception):
    """The bead can't be placed: no such repository, or an unknown or ambiguous profile."""


@dataclass(frozen=True)
class Limits:
    launch_failures_before_human: int = 2      # §9 reconcile, §10 agent crash
    park_attempts_before_human: int = 3


@dataclass(frozen=True)
class WorkstreamSettings:
    name: str
    repos: dict[str, Path]
    coder_role: str
    coder_profile: str
    profiles: frozenset[str]
    limits: Limits = field(default_factory=Limits)
    # Each profile's `model` ("" when none is passed), journaled at a launch entry's pin (AU-3 §4.2).
    models: Mapping[str, str] = field(default_factory=dict[str, str])
    # The launch guard's accounts (AU-3 §2.1). None: no launch can pin an account, and the guard
    # escalates CONFIG_INVALID rather than guess.
    accounts: Accounts | None = None
    usage: UsageSettings = field(default_factory=UsageSettings)     # the host's [usage] (AU-5 §2.1)


@dataclass(frozen=True)
class Placement:
    repo: Path
    worktree: Path
    profile: str
    session_key: str
    label: str


def place(ws: WorkstreamSettings, bead: Bead) -> Placement:
    name = bead.metadata.get(REPO_KEY, DEFAULT_REPO)
    if not isinstance(name, str) or name not in ws.repos:
        raise ConfigInvalid("the bead names no repository of this workstream")
    try:
        profile = ids.profile_for(bead.labels, ws.coder_role, ws.coder_profile)
        session_key = ids.role_session(bead.id, ws.coder_role, profile)
    except ids.BadName as exc:
        raise ConfigInvalid(str(exc)) from None
    if profile not in ws.profiles:
        raise ConfigInvalid("the bead names an unknown profile")
    repo = ws.repos[name].resolve()
    return Placement(repo, repo.parent / f"{repo.name}-btq-{bead.id}", profile, session_key,
                     label(bead, ws.coder_role))


def label(bead: Bead, role: str) -> str:
    """The session label (§4.1): "<bead> · <role> · <title>"."""
    title = " ".join(bead.title.split())[:LABEL_TITLE_CHARS]
    return f"{bead.id} · {role} · {title}"


def record(ws: WorkstreamSettings, spot: Placement) -> SessionRecord:
    return SessionRecord(ws.coder_role, spot.profile, spot.session_key, str(spot.repo), str(spot.worktree))


def utc_now() -> int:
    return int(time.time())


def no_selection(_adapter: str) -> InstallPin | None:
    """No adapter has a selected install: the default outside production (plan 4c T7)."""
    return None


class SessionHandler(Protocol):
    def __call__(self, parker: "Parker", event: SessionEvent) -> None:
        """Handle one session event (plan 5i): idempotent by `event.event_id`. Raise OpConflict to wait
        for the bead's open operation; anything else counts a failed attempt."""
        ...


NoticeKind = Literal["stuck", "waiting", "closed", "review"]     # part 2's, verbatim


class NoticeSink(Protocol):
    """What wsd needs of part 2's notice client (`heterodyne.wsd.notices.Notices`): journal by `ref`
    (posting each ref once), then try; never raise on transport."""

    def send(self, ws: str, bead: str | None, kind: NoticeKind, title: str, body: str, ref: str) -> None: ...


@dataclass(frozen=True)
class Deps:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    runtime: AgentRuntime
    reconciler: ActionReconciler
    cp: Checkpoint = nothing
    # Every AU-5 scheduling decision takes `now` from here (UTC epoch seconds), so tests drive one clock.
    clock: Callable[[], int] = utc_now
    # The trusted host-side read (D9 (d), plan 4b Task 8). The default confirms nothing.
    usage_reader: UsageReader = field(default_factory=NoUsageReader)
    # The adapter's selected install, read afresh on each call (plan 4c T7): the guard pins it in each new
    # launch entry. ConfigError when the selection can't be read.
    selections: Callable[[str], InstallPin | None] = no_selection
    # Plan 5i: resume notes for the brief (part 2's AskBridge), and the handlers of session events by kind
    # (parts 2 and 3). The ws-request ops wsd accepts are the handlers' kinds.
    notes: ResumeNotes = field(default_factory=NoNotes)
    session_handlers: Mapping[str, SessionHandler] = field(default_factory=dict[str, SessionHandler])
    inbox_attempts: int = 3         # wsd.inbox_attempts_before_human; assemble passes the setting
    notices: NoticeSink | None = None   # plan 5i part 2's client; None until it is wired
