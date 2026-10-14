"""The store's clients (ADR r16 §7 Managed installs; plan 4c E3, E21): `HelperStore` sends each request to the
root-owned store helper over its Unix socket, and `LocalStore`, the fallback for a host without the helper,
runs the same `store_core.handle` in this process. Both send the same encoded requests, so both answer alike.
A refusal is a `StoreRefused` whose message is the store's constant alone.
"""

import dataclasses
import json
import socket
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol, cast

from heterodyne.harness.resources import store_core as sc
from heterodyne.harness.settings import HarnessSettings

MAX_REPLY = 64 << 20            # a reply's bytes: a list of every install is the largest
HELPER_TIMEOUT = 30.0           # seconds a request to the helper may take, the lock wait included


class StoreRefused(Exception):
    """A store refusal: `.constant` is one of `store_core`'s constants, and the whole message."""

    def __init__(self, constant: str) -> None:
        super().__init__(constant)
        self.constant = constant


@dataclasses.dataclass(frozen=True)
class InstallInfo:
    name: str
    adapter: str
    version: str
    digest: str
    entries: int
    size: int
    state: Literal["published", "publishing", "broken"]


class Store(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def fallback(self) -> bool: ...

    def stage(self, adapter: str, layout: sc.Layout, parts: Sequence[sc.Part], fds: Sequence[int],
              login_names: frozenset[str], *, verity: bool, target: str) -> sc.Staged: ...

    def staged_manifest(self, staging_id: str) -> sc.Manifest: ...

    def publish(self, staging_id: str, approved_digest: str) -> str: ...

    def outcome(self, staging_id: str) -> str | None: ...

    def state(self, adapter: str, install: str) -> str: ...

    def verify(self, adapter: str, install: str) -> None: ...

    def remove(self, adapter: str, install: str) -> None: ...

    def recover(self) -> list[tuple[str, str]]: ...

    def list(self) -> list[InstallInfo]: ...


class _Client:
    """The operations, as requests through `_call`."""

    root: Path

    def _call(self, op: str, args: dict[str, object], fds: Sequence[int] = ()) -> object:
        raise NotImplementedError

    def stage(self, adapter: str, layout: sc.Layout, parts: Sequence[sc.Part], fds: Sequence[int],
              login_names: frozenset[str], *, verity: bool, target: str) -> sc.Staged:
        args = sc.stage_args(adapter, layout, parts, login_names, verity=verity, target=target)
        result = self._call("stage", args, fds)
        try:
            return sc.Staged(**cast(dict[str, object], result))      # pyright: ignore[reportArgumentType]
        except TypeError:
            raise StoreRefused(sc.HELPER_FAILED) from None

    def staged_manifest(self, staging_id: str) -> sc.Manifest:
        """Read from the store directly: a staged manifest is written once, and the store is readable."""
        try:
            return sc.read_staged(self.root, staging_id)
        except sc.Refused as exc:
            raise StoreRefused(exc.constant) from None

    def publish(self, staging_id: str, approved_digest: str) -> str:
        return _text(self._call("publish", {"id": staging_id, "digest": approved_digest}))

    def outcome(self, staging_id: str) -> str | None:
        result = self._call("outcome", {"id": staging_id})
        return None if result is None else _text(result)

    def state(self, adapter: str, install: str) -> str:
        return _text(self._call("state", {"adapter": adapter, "install": install}))

    def verify(self, adapter: str, install: str) -> None:
        self._call("verify", {"adapter": adapter, "install": install})

    def remove(self, adapter: str, install: str) -> None:
        self._call("remove", {"adapter": adapter, "install": install})

    def recover(self) -> list[tuple[str, str]]:
        result = self._call("recover", {})
        if not isinstance(result, list):
            raise StoreRefused(sc.HELPER_FAILED)
        return [(_text(i[0]), _text(i[1])) for i in cast(list[list[object]], result)]

    def list(self) -> list[InstallInfo]:
        result = self._call("list", {})
        if not isinstance(result, list):
            raise StoreRefused(sc.HELPER_FAILED)
        try:
            return [InstallInfo(**i) for i in cast(list[dict[str, object]], result)]  # pyright: ignore
        except TypeError:
            raise StoreRefused(sc.HELPER_FAILED) from None


def _text(v: object) -> str:
    if not isinstance(v, str):
        raise StoreRefused(sc.HELPER_FAILED)
    return v


class LocalStore(_Client):
    """The store run in this process, as the service user: the fallback (plan 4c E3)."""

    fallback = True

    def __init__(self, root: Path, *, lock_seconds: float = sc.LOCK_SECONDS,
                 staging_hours: float = sc.STAGING_HOURS, clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self._lock_seconds = lock_seconds
        self._hours = staging_hours
        self._clock = clock

    def _call(self, op: str, args: dict[str, object], fds: Sequence[int] = ()) -> object:
        try:
            request = sc.decode_request(sc.encode_request(op, self.root, args))
            return sc.handle(self.root, request, fds, now=self._clock(), hours=self._hours,
                             deadline=self._lock_seconds)
        except sc.Refused as exc:
            raise StoreRefused(exc.constant) from None


class HelperStore(_Client):
    """The store helper over its socket: one connection a request, its artifacts passed as fds."""

    fallback = False

    def __init__(self, socket_path: Path, root: Path, *, timeout: float) -> None:
        self.root = root
        self._path = socket_path
        self._timeout = timeout

    def _call(self, op: str, args: dict[str, object], fds: Sequence[int] = ()) -> object:
        request = sc.encode_request(op, self.root, args)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self._timeout)
                conn.connect(str(self._path))
                socket.send_fds(conn, [request], list(fds))
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(1 << 16)
                    if not chunk or len(data) + len(chunk) > MAX_REPLY:
                        raise StoreRefused(sc.HELPER_FAILED)
                    data += chunk
        except OSError:
            raise StoreRefused(sc.HELPER_FAILED) from None
        try:
            reply: object = json.loads(data)
        except (ValueError, RecursionError):
            raise StoreRefused(sc.HELPER_FAILED) from None
        if not isinstance(reply, dict):
            raise StoreRefused(sc.HELPER_FAILED)
        r = cast(dict[str, object], reply)
        if r.get("ok") is True and "result" in r:
            return r["result"]
        refused = r.get("refused")
        raise StoreRefused(refused if r.get("ok") is False and isinstance(refused, str) else sc.HELPER_FAILED)


def open_store(settings: HarnessSettings, *,
               helper_timeout: float = HELPER_TIMEOUT) -> LocalStore | HelperStore:
    """The local store only when the settings turn the fallback on; the helper otherwise (plan 4c E3)."""
    if settings.fallback:
        return LocalStore(settings.store, staging_hours=settings.staging_hours)
    return HelperStore(settings.helper_socket, settings.store, timeout=helper_timeout)
