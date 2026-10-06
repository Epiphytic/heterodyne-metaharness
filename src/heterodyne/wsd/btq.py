"""btq's Queue library, loaded in-process from the configured checkout (ADR 0001 §4.3, §16).

btq is a script without a `.py` suffix, so it is loaded by file. Only the `Queue` class is used, always
as agent `wsd`; `QueueLike` is the slice of it wsd relies on, which the test fake implements too.
"""

import importlib.machinery
import importlib.util
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

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
    queue_class = cast(Callable[..., QueueLike], module.Queue)
    overrides = dict(locations)

    def make(ws: str, session: str) -> QueueLike:
        return queue_class(AGENT, ws, session, **overrides)

    return make
