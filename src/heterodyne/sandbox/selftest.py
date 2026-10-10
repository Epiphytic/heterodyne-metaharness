"""The launch self-test's contract (ADR 0001 §7). A backend's self-test runs the probes on two paths:
by a separate exec (`exec_path`, before the agent starts) and by the agent through its own tool
(`agent_path`, after its pane starts, before normal work). Either raises SelfTestFailed naming the check,
and the runtime then deletes the sandbox and fails the launch."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from heterodyne.agents.base import Adapter, Cli
from heterodyne.sandbox.backend import Backend
from heterodyne.sandbox.settings import SandboxSettings
from heterodyne.sandbox.spec import SandboxSpec, SessionLayout
from heterodyne.session.server import TurnState
from heterodyne.tmux import Tmux


class SelfTestFailed(Exception):
    """A self-test check failed. The text names the check (fixed wording, no paths or output)."""


@dataclass(frozen=True)
class ProbeContext:
    backend: Backend
    tmux: Tmux
    tmux_session: str
    spec: SandboxSpec
    layout: SessionLayout
    generation: int
    adapter: Adapter
    cli: Cli
    token: str                                   # never sent to the sandbox by the self-test
    others: tuple[tuple[str, Path], ...]         # (account, login file as configured), other accounts
    real_home_canary: Path
    wsd_socket: Path
    settings: SandboxSettings
    turns: Callable[[], TurnState]


class SelfTest(Protocol):
    def files(self) -> Mapping[str, str]:
        """File name -> text, written into each run directory (read-only at /run/hz inside)."""
        ...

    def exec_path(self, ctx: ProbeContext) -> None: ...

    def agent_path(self, ctx: ProbeContext) -> None: ...
