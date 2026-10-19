"""Independent local custody control transport; no source enrollment or activation.

Blocking source/filesystem observations stay off the subscription loop. Cancellation
waits for the admitted local worker to finish; it never abandons an issuance worker.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal, cast

import msgspec

from heterodyne.admind import gateway_custody as custody

SOCKET = "gateway-custody.sock"


class Reply(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    result: Literal["ok", "registered", "issued", "refused", "unavailable"]
    message: str
    data: dict[str, object] | None = None


async def worker[T](perform: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(perform))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # worker failure must not replace caller cancellation
                break
        if not task.cancelled():
            task.exception()
        raise


def independent(
    service: custody.Service,
    authority: Callable[[str, str, str], Awaitable[str | None]],
) -> custody.Service:
    """Bind the daemon-owned current policy reader, not a local caller callback."""
    loop = asyncio.get_running_loop()

    async def current(account: str, group: str, sender: str) -> str | None:
        return await authority(account, group, sender)

    def observe(account: str, group: str, sender: str) -> str | None:
        pending = asyncio.run_coroutine_threadsafe(current(account, group, sender), loop)
        try:
            return pending.result(timeout=5)
        except TimeoutError:
            pending.cancel()
            raise custody.Invalid("authority-unavailable") from None

    return custody.Service(
        service.store, source=service.source, publication=service.publication, authority=observe
    )


def perform(service: custody.Service, req: custody.Request, *, uid: int | None, max_chars: int) -> Reply:
    if custody.check(req) is not None:
        raise custody.Invalid("invalid-custody-request")
    if isinstance(req, custody.GatewayCustodyRegister):
        key = service.register(req.descriptor, uid=uid, max_chars=max_chars)
        return Reply(
            "registered", "custody proposal retained; registration is not consent", {"proposal_sha256": key}
        )
    if isinstance(req, custody.GatewayCustodyStatus):
        return Reply(
            "ok",
            "retained custody status; no new authority",
            cast(dict[str, object], msgspec.to_builtins(service.get(req.proposal_sha256, uid=uid))),
        )
    encoded = service.issue_permit(req, uid=uid)
    return Reply(
        "issued",
        "retained generation; never blindly reissue",
        cast(dict[str, object], msgspec.json.decode(encoded)),
    )
