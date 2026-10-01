"""Client for the wn-agent control socket: `marmot.agent-control.v2`, NDJSON over a Unix socket.

Request and event shapes are the ones spike S4 confirmed (docs/spikes/S4-marmot.md). Every frame is
decoded strictly with msgspec (ADR 0001 §16): a known event type with a missing or mistyped field is a
ProtocolError, never a partially trusted dict. Unknown event types become `OtherEvent` so callers can
log and ignore them. The sender of a message is taken only from its MLS-authenticated `sender`
metadata, never from the text (§3.4).
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec

PROTOCOL = "marmot.agent-control.v2"
MAX_FRAME = 1024 * 1024
# Peer error codes admind echoes. Anything else, which could be token material, becomes "unrecognised";
# the peer's free text stays in `detail` only.
KNOWN_ERROR_CODES = frozenset({
    "unauthorized", "unavailable", "unsupported", "relay_unavailable", "not_group_admin",
    "not_found", "rate_limited", "auth_failed", "message_send_failed", "group_create_failed",
})


class ControlError(Exception):
    """`str(exc)` is always admind's own wording. A peer's free-text error message may echo keys or IDs,
    so it is kept in `detail`, which goes only to the local audit log, never to stderr or the group."""

    def __init__(self, message: str, code: str = "agent_control_error", retryable: bool = False,
                 detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.detail = detail


class ProtocolError(ControlError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="protocol_error", retryable=False)


class _Head(msgspec.Struct):
    marmot_agent_control: str
    id: str
    type: str


class _Error(msgspec.Struct):
    code: str = "agent_control_error"
    message: str = "agent control error"
    retryable: bool = False


class Account(msgspec.Struct, frozen=True):
    account_id_hex: str
    local_signing: bool = False


class AccountList(msgspec.Struct, frozen=True):
    accounts: list[Account]


class GroupInfo(msgspec.Struct, frozen=True):
    group_id_hex: str
    member_count: int


class GroupCreated(msgspec.Struct, frozen=True):
    group_id_hex: str


class FinalSent(msgspec.Struct, frozen=True):
    message_ids_hex: list[str]


class _Ack(msgspec.Struct, frozen=True):
    type: str


class Sender(msgspec.Struct, frozen=True):
    account_id_hex: str
    is_self: bool
    display_name: str | None = None


class Message(msgspec.Struct, frozen=True):
    message_id_hex: str
    sender: Sender
    text: str
    recorded_at: int


class ReplyTo(msgspec.Struct, frozen=True):
    message_id_hex: str


class InboundMessage(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    message: Message
    reply_to: ReplyTo | None = None


class ReactionAdded(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    target_message_id_hex: str
    actor: Sender
    emoji: str


class GroupStateChanged(msgspec.Struct, frozen=True):
    account_id_hex: str
    group_id_hex: str
    change: str
    detail: str | None = None


class OtherEvent(msgspec.Struct, frozen=True):
    type: str


Event = InboundMessage | ReactionAdded | GroupStateChanged | OtherEvent
_EVENT_TYPES: dict[str, type[InboundMessage] | type[ReactionAdded] | type[GroupStateChanged]] = {
    "inbound_message": InboundMessage,
    "reaction_added": ReactionAdded,
    "group_state_changed": GroupStateChanged,
}


def decode_head(line: bytes, request_id: str) -> str:
    """Validate protocol, correlation ID and error frames; return the frame's `type`."""
    try:
        head = msgspec.json.decode(line, type=_Head)
    except msgspec.DecodeError as exc:
        raise ProtocolError(f"malformed control frame: {exc}") from exc
    if head.marmot_agent_control != PROTOCOL:
        raise ProtocolError("wrong control protocol")
    if head.id != request_id:
        raise ProtocolError("response id does not match the request id")
    if head.type == "error":
        try:
            err = msgspec.json.decode(line, type=_Error)
        except msgspec.DecodeError as exc:
            raise ProtocolError("malformed error frame") from exc
        # The code is peer-supplied too: only allowlisted codes are echoed, and the free text only in detail.
        code = err.code if err.code in KNOWN_ERROR_CODES else "unrecognised"
        raise ControlError(f"wn-agent returned error {code}", code, err.retryable, detail=err.message)
    return head.type


def decode_event(line: bytes, request_id: str) -> Event:
    kind = decode_head(line, request_id)
    struct = _EVENT_TYPES.get(kind)
    if struct is None:
        return OtherEvent(type=kind)
    try:
        return msgspec.json.decode(line, type=struct)
    except msgspec.DecodeError as exc:
        raise ProtocolError(f"{kind}: {exc}") from exc


class ControlClient:
    def __init__(self, socket_path: Path, token: str | None, timeout: float = 30.0) -> None:
        self.socket_path = socket_path
        self.token = token
        self.timeout = timeout

    def _frame(self, payload: dict[str, Any], request_id: str) -> bytes:
        envelope: dict[str, Any] = {"marmot_agent_control": PROTOCOL, "id": request_id, **payload}
        if self.token:
            envelope["auth_token"] = self.token
        frame = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        if len(frame) > MAX_FRAME:
            raise ControlError("control frame too large", "frame_too_large")
        return frame

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path), limit=MAX_FRAME + 1), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise ControlError(f"cannot connect to wn-agent ({type(exc).__name__})",
                               "socket_io", True) from exc

    async def _readline(self, reader: asyncio.StreamReader, timeout: float | None) -> bytes:
        try:
            if timeout is None:
                line = await reader.readline()
            else:
                line = await asyncio.wait_for(reader.readline(), timeout)
        except TimeoutError as exc:
            raise ControlError("timed out waiting for wn-agent", "timeout", True) from exc
        except ValueError as exc:  # LimitOverrunError surfaces as ValueError from readline()
            raise ProtocolError("control frame too large") from exc
        except OSError as exc:
            raise ControlError(f"wn-agent socket error ({type(exc).__name__})", "socket_io", True) from exc
        if not line:
            raise ControlError("wn-agent closed the connection", "socket_closed", True)
        return line

    async def _write(self, writer: asyncio.StreamWriter, frame: bytes) -> None:
        writer.write(frame)
        try:
            await asyncio.wait_for(writer.drain(), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise ControlError(f"cannot write to wn-agent ({type(exc).__name__})", "socket_io", True) from exc

    async def call[T](self, payload: dict[str, Any], expected_type: str, kind: type[T]) -> T:
        request_id = uuid.uuid4().hex
        frame = self._frame(payload, request_id)
        reader, writer = await self._open()
        try:
            await self._write(writer, frame)
            line = await self._readline(reader, self.timeout)
            if decode_head(line, request_id) != expected_type:
                raise ProtocolError(f"unexpected response type for {payload['type']}")
            try:
                return msgspec.json.decode(line, type=kind)
            except msgspec.DecodeError as exc:
                raise ProtocolError(f"unexpected {payload['type']} response: {exc}") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def account_list(self) -> AccountList:
        return await self.call({"type": "account_list"}, "account_list", AccountList)

    async def group_info(self, account: str, group: str) -> GroupInfo:
        return await self.call({"type": "group_info", "account_id_hex": account, "group_id_hex": group},
                               "group_info", GroupInfo)

    async def group_create(self, account: str, name: str, members: list[str]) -> GroupCreated:
        return await self.call({"type": "group_create", "account_id_hex": account, "name": name,
                                "members": members, "description": None, "relays": None},
                               "group_created", GroupCreated)

    async def send_final(self, account: str, group: str, text: str, reply_to: str | None,
                         key: str) -> FinalSent:
        return await self.call({"type": "send_final", "account_id_hex": account, "group_id_hex": group,
                                "text": text, "reply_to_message_id_hex": reply_to,
                                "idempotency_key": key}, "final_sent", FinalSent)

    async def subscribe(self, account: str, group: str) -> AsyncIterator[Event]:
        """Yield inbound events until the connection ends, which raises a retryable ControlError."""
        request_id = uuid.uuid4().hex
        frame = self._frame({"type": "subscribe_inbound", "account_id_hex": account,
                             "group_id_hex": group}, request_id)
        reader, writer = await self._open()
        try:
            await self._write(writer, frame)
            ack = await self._readline(reader, self.timeout)
            decode_head(ack, request_id)
            if msgspec.json.decode(ack, type=_Ack).type != "ack":
                raise ProtocolError("subscribe_inbound was not acknowledged")
            while True:
                yield decode_event(await self._readline(reader, None), request_id)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
