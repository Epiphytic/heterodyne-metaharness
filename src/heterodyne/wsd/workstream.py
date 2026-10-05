"""One workstream's settings, and where a bead runs: repository, worktree, profile and session key.

`place` is used only where wsd starts something new: a pickup's worktree and record, and a release that
must record a bead whose launch was never recorded. Everything that continues existing work (resume,
park, recovery) reads the launched-session record on the bead instead, so a configuration change never
redirects a running or parked bead to another session or worktree.
"""

from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter, SessionRecord
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime

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


@dataclass(frozen=True)
class Deps:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    runtime: AgentRuntime
    reconciler: ActionReconciler
    cp: Checkpoint = nothing
