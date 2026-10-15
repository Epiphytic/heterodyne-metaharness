"""btq's Queue library, loaded in-process from the configured checkout (ADR 0001 §4.3, §16).

btq is a script without a `.py` suffix, so it is loaded by file. Only the `Queue` class is used, always
as agent `wsd`; `QueueLike` is the slice of it wsd relies on, which the test fake implements too.

An approval's decision is read with `approve-bead <bead> --json` only (plan 4c E13): read only, under btq's
per-bead lock, with a short deadline because every pickup runs it (`approval_readout`).
"""

import contextlib
import importlib.machinery
import importlib.util
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

import msgspec

AGENT = "wsd"
MODULE_NAME = "_heterodyne_btq"


class BtqUnavailable(Exception):
    """The btq checkout is missing, unloadable, or predates the `wsd` agent (plan 1's btq change)."""


class QueueLike(Protocol):
    worker: str
    state: Path

    def bd(self, *args: str) -> Any: ...
    def show(self, issue_id: str) -> Any: ...
    def ready(self) -> Any: ...
    def claim(self, issue_id: str) -> Any: ...
    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any: ...
    def matches(self, issue: Any) -> bool: ...
    def design_allowed(self, issue: Any) -> bool: ...
    def worktree(self, issue_id: str, repository: str) -> Any: ...
    def exclusive(self) -> AbstractContextManager[None]: ...
    policy: Any

    def ask_digest(self, issue: Any) -> tuple[str | None, list[str]]: ...


# (workstream, btq session) -> Queue("wsd", workstream, session, **locations)
QueueFactory = Callable[[str, str], QueueLike]


def load(checkout: Path) -> ModuleType:
    path = checkout / "bin" / "btq"
    try:
        loader = importlib.machinery.SourceFileLoader(MODULE_NAME, str(path))
        spec = importlib.util.spec_from_loader(MODULE_NAME, loader)
        if spec is None:
            raise BtqUnavailable("btq could not be loaded")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as exc:
        raise BtqUnavailable(f"btq could not be loaded ({type(exc).__name__})") from None
    if AGENT not in getattr(module, "AGENTS", ()) or not hasattr(module, "Queue"):
        raise BtqUnavailable("this btq has no wsd agent; install plan 1's btq change")
    return module


def factory(module: ModuleType, locations: Mapping[str, str]) -> QueueFactory:
    """Queues of `module.Queue`, with btq's module-level `ask_digest` as a method: the digest an approval's
    ask has, which btq computes with the queue's `show`."""
    digest = module.ask_digest

    class _Queue(module.Queue):  # type: ignore[misc, name-defined]
        def ask_digest(self, issue: Any) -> tuple[str | None, list[str]]:
            return cast(tuple[str | None, list[str]], digest(issue, self.show))

    queue_class = cast(Callable[..., QueueLike], _Queue)
    overrides = dict(locations)

    def make(ws: str, session: str) -> QueueLike:
        return queue_class(AGENT, ws, session, **overrides)

    return make


# --- the approval readout (plan 4c E13) ---

READOUT_SECONDS = 5.0       # one read's deadline: `advance` runs one per posted install from every pickup
REAP_SECONDS = 5.0          # after a kill, how long the child has to be reaped
MAX_READOUT = 1 << 20       # a readout line's bytes


class Readout(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """approve-bead's `--json` line. Every field is kept as it came, missing (UNSET), null or of any type:
    `approvals.verdict` checks each one, so a malformed field fails the predicate that reads it."""
    format: Any = msgspec.UNSET
    busy: Any = msgspec.UNSET
    id: Any = msgspec.UNSET
    status: Any = msgspec.UNSET
    labels: Any = msgspec.UNSET
    kind_approval: Any = msgspec.UNSET
    digest: Any = msgspec.UNSET
    posted_digest: Any = msgspec.UNSET
    gaps: Any = msgspec.UNSET
    design_review_valid: Any = msgspec.UNSET
    adr_revision: Any = msgspec.UNSET
    approvers: Any = msgspec.UNSET
    decision: Any = msgspec.UNSET
    approved_by: Any = msgspec.UNSET
    approved_digest: Any = msgspec.UNSET
    denied_by: Any = msgspec.UNSET
    denied_digest: Any = msgspec.UNSET
    via: Any = msgspec.UNSET
    via_ref: Any = msgspec.UNSET
    decided: Any = msgspec.UNSET
    gate_valid: Any = msgspec.UNSET
    gate_reasons: Any = msgspec.UNSET
    links: Any = msgspec.UNSET
    readout: Any = msgspec.UNSET
    gate_preview: Any = msgspec.UNSET
    purpose: Any = msgspec.UNSET


class Busy(msgspec.Struct, frozen=True):
    """The bead's lock is held by another approve-bead run, or the read passed its deadline: try later."""


class ReadoutFailed(Exception):
    """approve-bead could not be read: it failed, or its output is too large. Fixed wording."""


_READOUT = msgspec.json.Decoder(Readout)


def parse_readout(data: bytes) -> Readout | Busy:
    """One `--json` line. ValueError (msgspec's DecodeError is one) for anything not a JSON object of the
    readout's fields; `{"format": 1, "busy": true}` is Busy."""
    readout = _READOUT.decode(data)
    if readout.busy is True:
        return Busy()
    return readout


def _killed(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the child's process group (it may have left a descendant in it), then reap the child."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(REAP_SECONDS)


def approval_readout(bead: str, *, binary: Path) -> Readout | Busy:
    """`approve-bead <bead> --json`, in its own process group, its stdout capped at MAX_READOUT and its
    whole run bounded by READOUT_SECONDS (read when called). Past the deadline it is Busy; exit 4 is Busy;
    any other failure raises (ReadoutFailed, or the parse's ValueError). The group is killed and the child
    reaped on every path."""
    deadline = time.monotonic() + READOUT_SECONDS
    proc = subprocess.Popen([str(binary), bead, "--json"], start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        stdout = proc.stdout
        if stdout is None:
            raise ReadoutFailed("approve-bead has no output pipe")
        out = bytearray()
        with selectors.DefaultSelector() as sel:
            sel.register(stdout, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    return Busy()
                if not sel.select(left):
                    continue
                chunk = os.read(stdout.fileno(), 1 << 16)
                if not chunk:
                    break
                out += chunk
                if len(out) > MAX_READOUT:
                    raise ReadoutFailed("approve-bead's output is too large")
        try:
            code = proc.wait(max(deadline - time.monotonic(), 0.0))
        except subprocess.TimeoutExpired:
            return Busy()
    finally:
        _killed(proc)
        if proc.stdout is not None:
            proc.stdout.close()
    if code == 4:
        return Busy()
    if code != 0:
        raise ReadoutFailed("approve-bead could not read the approval")
    return parse_readout(bytes(out))
