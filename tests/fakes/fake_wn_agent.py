"""An in-process fake of the wn-agent control socket (marmot.agent-control.v2), per spike S4.

It serves one NDJSON request per connection, except `subscribe_inbound`, which acks and then
streams whatever the test pushes with `push_event`. It checks the bearer token, dedups
`send_final` by idempotency key, and records every request.
"""

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROTOCOL = "marmot.agent-control.v2"
ACCOUNT = "a1" * 32
GROUP = "b2" * 32


class FakeWnAgent:
    def __init__(self, socket_path: Path, token: str | None = "test-token",  # noqa: S107 (test value)
                 member_count: int = 2,
                 bootstrapped: bool = True) -> None:
        self.socket_path = socket_path
        self.token = token
        self.member_count = member_count
        self.accounts: list[dict[str, Any]] = []
        if bootstrapped:
            self.accounts.append({"account_id_hex": ACCOUNT, "local_signing": True})
        self.group_id = GROUP
        self.requests: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.fail_sends = 0          # the next N send_final calls fail with a retryable error
        self.fail_group_info = False
        self.membership_mode = "ok"         # ok, fail, ok-no-count, fail-count, hang, lost, gated
        self.membership_gate = asyncio.Event()      # "gated": the request waits until the test sets it
        self.info_gate: asyncio.Event | None = None  # when set to an Event, group_info waits for it
        self.send_gate: asyncio.Event | None = None  # when set to an Event, send_final waits for it
        self.on_send: Callable[[dict[str, Any]], None] | None = None   # called after each new send
        self._keys: dict[str, str] = {}
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))

    async def stop(self) -> None:
        """Stop listening and end every open connection. A subscription stream never ends by itself and
        `wait_closed` waits for it, so without the cancel every stop would sit out the 2-second bound."""
        if self._server is not None:
            self._server.close()
            for task in list(self._handlers):
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2)

    async def push_event(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            await queue.put(event)

    async def wait_subscribed(self, timeout: float = 5.0) -> None:
        async def poll() -> None:
            while not self._subscribers:
                await asyncio.sleep(0.02)
        await asyncio.wait_for(poll(), timeout)

    def message_event(self, text: str, sender: str, message_id: str, *, is_self: bool = False,
                      group: str | None = None, reply_to: str | None = None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "inbound_message", "account_id_hex": ACCOUNT, "group_id_hex": group or self.group_id,
            "message": {"message_id_hex": message_id, "text": text, "recorded_at": 1790738645,
                        "sender": {"account_id_hex": sender, "display_name": None, "is_self": is_self}},
            "mentions_self": False,
        }
        if reply_to:
            event["reply_to"] = {"message_id_hex": reply_to, "availability": "available"}
        return event

    def reaction_event(self, emoji: str, actor: str, event_id: str | None, target: str, *,
                       is_self: bool = False, group: str | None = None) -> dict[str, Any]:
        """A `reaction_added` frame in the shape spike S4 captured. `event_id=None` leaves the ID out."""
        event: dict[str, Any] = {
            "type": "reaction_added", "account_id_hex": ACCOUNT, "group_id_hex": group or self.group_id,
            "target_message_id_hex": target,
            "actor": {"account_id_hex": actor, "display_name": None, "is_self": is_self},
            "emoji": emoji, "recorded_at": 1790738646,
            "target": {"message_id_hex": target, "availability": "available"},
        }
        if event_id is not None:
            event["event_id_hex"] = event_id
        return event

    async def _reply(self, writer: asyncio.StreamWriter, request_id: str, body: dict[str, Any]) -> None:
        frame = {"marmot_agent_control": PROTOCOL, "id": request_id, **body}
        writer.write(json.dumps(frame).encode() + b"\n")
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._handlers.add(task)
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line)
            self.requests.append(req)
            rid = req.get("id", "")
            if self.token is not None and req.get("auth_token") != self.token:
                await self._reply(writer, rid, {"type": "error", "code": "unauthorized",
                                                "message": "bad token", "retryable": False})
                return
            kind = req.get("type")
            if kind == "account_list":
                await self._reply(writer, rid, {"type": "account_list", "accounts": self.accounts})
            elif kind == "fake_bootstrap":   # test-only stand-in for what `wn-agent bootstrap` does
                if not self.accounts:
                    self.accounts.append({"account_id_hex": ACCOUNT, "local_signing": True})
                await self._reply(writer, rid, {"type": "ack"})
            elif kind == "group_info":
                count = self.member_count       # as of the request, even if a gate holds the answer back
                if self.info_gate is not None:
                    await self.info_gate.wait()
                if self.fail_group_info:
                    await self._reply(writer, rid, {"type": "error", "code": "unavailable",
                                                    "message": "down", "retryable": True})
                    return
                await self._reply(writer, rid, {
                    "type": "group_info", "account_id_hex": req["account_id_hex"],
                    "group_id_hex": req["group_id_hex"], "agent_created": True,
                    "member_count": count, "is_direct": True})
            elif kind == "group_create":
                await self._reply(writer, rid, {"type": "group_created", "group_id_hex": self.group_id,
                                                "agent_created": True, "pending_welcome_count": 0})
            elif kind in ("group_member_add", "group_member_remove"):
                await self._membership(writer, rid, kind, req)
            elif kind == "send_final":
                await self._send_final(writer, rid, req)
            elif kind == "subscribe_inbound":
                await self._subscribe(writer, rid)
            else:
                await self._reply(writer, rid, {"type": "error", "code": "unsupported",
                                                "message": str(kind), "retryable": False})
        finally:
            self._handlers.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _membership(self, writer: asyncio.StreamWriter, rid: str, kind: str,
                          req: dict[str, Any]) -> None:
        mode = self.membership_mode
        delta = len(req["members"]) * (1 if kind == "group_member_add" else -1)
        if mode == "hang":
            await asyncio.sleep(3600)
        if mode == "lost":                  # the change happens; the reply never comes
            self.member_count += delta
            await asyncio.sleep(3600)
        if mode == "gated":
            await self.membership_gate.wait()
        if mode in ("ok", "gated", "fail-count"):
            self.member_count += delta
        if mode in ("fail", "fail-count"):
            await self._reply(writer, rid, {"type": "error", "code": "not_group_admin",
                                            "message": "refused", "retryable": False})
            return
        await self._reply(writer, rid, {"type": "group_membership_updated", "group_id_hex": self.group_id,
                                        "pending_welcome_count": 0})

    async def drop_subscriptions(self) -> None:
        """End every open subscription stream, as a wn-agent restart would: the daemon resubscribes."""
        for queue in list(self._subscribers):
            await queue.put({"type": "_close"})

    async def _send_final(self, writer: asyncio.StreamWriter, rid: str, req: dict[str, Any]) -> None:
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.fail_sends > 0:
            self.fail_sends -= 1
            await self._reply(writer, rid, {"type": "error", "code": "relay_unavailable",
                                            "message": "try later", "retryable": True})
            return
        key = req.get("idempotency_key")
        if key is not None and key in self._keys:
            message_id = self._keys[key]
        else:
            message_id = hashlib.sha256(f"{len(self.sent)}:{req['text']}".encode()).hexdigest()
            req["_message_id"] = message_id     # the ID this send returned; existing tests ignore it
            self.sent.append(req)
            if key is not None:
                self._keys[key] = message_id
            if self.on_send is not None:
                self.on_send(req)
        await self._reply(writer, rid, {"type": "final_sent", "message_ids_hex": [message_id],
                                        "maintenance_disposition": "ready"})

    async def _subscribe(self, writer: asyncio.StreamWriter, rid: str) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            await self._reply(writer, rid, {"type": "ack"})
            while True:
                event = await queue.get()
                if event.get("type") == "_close":
                    return
                await self._reply(writer, rid, event)
        finally:
            self._subscribers.remove(queue)
