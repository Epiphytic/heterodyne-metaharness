"""What SandboxRuntime needs from a sandbox backend (ADR 0001 §7 Runtime). OpenShell implements it
(openshell.py); the tests' FakeBackend runs commands on the host."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from heterodyne.sandbox.spec import SandboxSpec


class BackendUnavailable(Exception):
    """The backend could not be asked (missing, down, timed out). Nothing is known."""


class BackendError(Exception):
    """A backend step failed. Fixed wording, no paths or output."""


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class Backend(Protocol):
    def available(self) -> bool:
        """Whether the backend answers at all (checked before every claim)."""
        ...

    def names(self) -> set[str]:
        """The names of every sandbox the backend lists. Raises BackendUnavailable; never partial."""
        ...

    def create(self, spec: SandboxSpec, scratch: Path) -> None:
        """Create the sandbox and return once commands can run in it. `scratch` is a host directory
        outside every bind, for the backend's own files. Raises BackendError or BackendUnavailable;
        either way the sandbox may exist and is deleted by the caller."""
        ...

    def exec(self, name: str, workdir: Path, argv: Sequence[str], *, input: bytes | None = None,
             timeout: float) -> ExecResult:
        """Run `argv` inside without a tty, stdin from `input` (else empty). Raises BackendError on a
        timeout and BackendUnavailable when the backend can't be run."""
        ...

    def tty_argv(self, name: str, workdir: Path, argv: Sequence[str]) -> list[str]:
        """A host command that runs `argv` inside with a tty, for a tmux pane. It carries its own
        environment (`env -i ...`), so the pane inherits nothing from the tmux server."""
        ...

    def delete(self, name: str) -> bool:
        """Delete the sandbox, ending every process in it, and wait until the backend no longer lists it.
        True once confirmed; False if it can't confirm (still listed, or the backend didn't answer)."""
        ...

    def logs(self, name: str, since: float) -> list[str]:
        """The sandbox supervisor's log lines stamped at or after `since` - 2 (epoch seconds)."""
        ...

    def network_mode(self, name: str) -> str:
        """The workload container's network mode, as the container runtime reports it from outside."""
        ...

    def workload_pid(self, name: str) -> int:
        """The host PID of the workload container's first process (its netns is the sandbox's)."""
        ...

    def pane_env(self) -> Mapping[str, str]:
        """The environment `tty_argv` gives the host command."""
        ...

    def reaper_argv(self, name: str, deadline: int) -> list[str]:
        """A host command, run outside wsd (D13's backstop), that waits until `deadline` (UTC epoch
        seconds) and then deletes the sandbox until the backend no longer lists it. It carries its own
        environment, as `tty_argv` does."""
        ...
