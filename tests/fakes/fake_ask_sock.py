"""A stand-in for admind's `ask.sock` (plan 5i part 2): an in-process asyncio server on a temp socket.

It runs on its own thread and event loop, so the synchronous `wsd.relay` calls can talk to it from the
test's thread. It frames as admind does (one JSON line in, one out, admind's request limit), checks each
request with admind's own `asks.check` and each notice with `notices.admit`, and replies with admind's own
structs and refusal texts, so a refusal the client sees is admind's wording. The limits (latch, in flight,
open, per hour, undelivered notices) are flags and counters the test sets.

`script` scripts the next connections, one entry each: "drop" closes after reading without a reply,
"lost" handles the request as usual and then closes without its reply, "hang" never replies, "garbage"
replies with a line that is not JSON, "failed" replies with admind's internal error, any other string is a
refusal with that message, and bytes are written as the raw reply.
`frames` holds every raw request line. A list holds the active asks, then only the RECENT_TERMINAL newest
finished ones, as admind's does.
"""

import asyncio
import contextlib
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from heterodyne.admind import asks, daemon, notices
from heterodyne.admind.notices import NoticePost

CHUNK_CHARS = 2000


RECENT_TERMINAL = 20     # admind's list: its active asks, then only the 20 newest finished ones


@dataclass
class FakeAsk:
    post: asks.AskPost
    ask_id: str
    status: str = "open"
    answers: list[asks.AnswerView] = field(default_factory=list[asks.AnswerView])
    outcome: str | None = None
    decided_by: str | None = None

    def summary(self) -> asks.AskSummary:
        return asks.AskSummary(self.ask_id, self.post.kind, self.status, self.post.title, self.post.bead,
                               None, True, len(self.answers), self.outcome, self.decided_by)

    def view(self) -> asks.AskView:
        return asks.AskView(self.summary(), list(self.answers))


class FakeAskSock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.frames: list[bytes] = []
        self.script: deque[str | bytes] = deque()
        self.asks: dict[str, FakeAsk] = {}
        self.notices: list[NoticePost] = []
        self.latched = False
        self.busy = False               # MAX_IN_FLIGHT posts are in flight
        self.approvals = True           # [admind] approve_bead is configured
        self.posted_this_hour = 0
        self.notices_this_hour = 0
        self.undelivered = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._release: asyncio.Event | None = None

    # --- the test's side ------------------------------------------------------------------------
    def start(self) -> "FakeAskSock":
        self._thread = threading.Thread(target=self._run, name="fake-ask-sock", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("fake ask.sock did not start")
        return self

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None and thread is not None:
            loop.call_soon_threadsafe(self._shutdown)
            thread.join(10)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def answer(self, ask_id: str, text: str, operator: str = "op", choice: str | None = None) -> None:
        ask = self.asks[ask_id]
        ask.answers.append(asks.AnswerView("answer", operator, text, asks.stamp(asks.now()), choice))
        ask.status = "answered"

    def decide(self, ask_id: str, outcome: str, decided_by: str = "op") -> None:
        ask = self.asks[ask_id]
        ask.status, ask.outcome, ask.decided_by = outcome, outcome, decided_by

    # --- the server -----------------------------------------------------------------------------
    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._release = asyncio.Event()
        self._server = await asyncio.start_unix_server(self._handle, str(self.path),
                                                       limit=asks.MAX_REQUEST + 2)
        self._ready.set()
        async with self._server:
            await self._release.wait()
        self._server.close()

    def _shutdown(self) -> None:
        if self._release is not None:
            self._release.set()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await reader.readline()
            except ValueError:
                line = b""
            self.frames.append(line)
            step = self.script.popleft() if self.script else None
            if step == "drop":
                return
            if step == "lost":
                self._reply(line)
                return
            if step == "hang":
                assert self._release is not None
                await self._release.wait()
                return
            if isinstance(step, bytes):
                writer.write(step)
            elif step == "garbage":
                writer.write(b"not json\n")
            else:
                reply = (asks.failed() if step == "failed" else asks.refused(step) if step is not None
                         else self._reply(line))
                writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _reply(self, line: bytes) -> asks.AskReply:
        if len(line.rstrip(b"\n")) > asks.MAX_REQUEST:
            return asks.refused("malformed request")
        try:
            req: asks.AskRequest = msgspec.json.decode(line, type=asks.AskRequest)
        except (ValueError, msgspec.DecodeError):
            return asks.refused("malformed request")
        refusal = asks.check(req)
        if refusal is not None:
            return asks.refused(refusal)
        if isinstance(req, NoticePost):
            return self._notice(req)
        if isinstance(req, asks.AskPost):
            return self._post(req)
        if isinstance(req, asks.AskList):
            active = [a for a in self.asks.values() if a.status in asks.ACTIVE]
            done = [a for a in reversed(self.asks.values()) if a.status not in asks.ACTIVE][:RECENT_TERMINAL]
            listed = [a.summary() for a in active + done]
            return asks.AskReply("ok", f"{len(listed)} asks", asks=listed)
        ask = self.asks.get(asks.normalise_id(req.ask_id) or "")
        if ask is None:
            return asks.refused(f"no ask {req.ask_id}")
        if isinstance(req, asks.AskGet):
            return asks.AskReply("ok", ask.status, ask=ask.view())
        if ask.status not in ("open", "answered"):
            return asks.refused(f"ask {ask.ask_id} is {ask.status}; only an open or answered ask can be "
                                "cancelled")
        ask.status = "cancelled"
        return asks.AskReply("ok", f"ask {ask.ask_id} cancelled")

    def _post(self, req: asks.AskPost) -> asks.AskReply:
        if self.busy:
            return asks.refused(daemon.ASK_BUSY)
        if self.latched:
            return asks.refused(daemon.ASK_LATCHED)
        if req.kind == "approval" and not self.approvals:
            return asks.refused(daemon.ASK_NO_APPROVALS)
        if sum(a.status in asks.ACTIVE for a in self.asks.values()) >= asks.MAX_OPEN:
            return asks.refused(daemon.ASK_TOO_MANY)
        if self.posted_this_hour >= asks.MAX_PER_HOUR:
            return asks.refused(daemon.ASK_TOO_OFTEN)
        ask = FakeAsk(req, asks.new_id(lambda i: i in self.asks))
        self.asks[ask.ask_id] = ask
        self.posted_this_hour += 1
        return asks.AskReply("posted", f"ask {ask.ask_id} posted", ask=ask.view())

    def _notice(self, req: NoticePost) -> asks.AskReply:
        if self.latched:
            return asks.refused(notices.MESSAGES["latched"])
        admitted = notices.admit(req, CHUNK_CHARS)
        if isinstance(admitted, str):
            return asks.refused(notices.MESSAGES[admitted])
        if self.notices_this_hour >= notices.NOTICE_PER_HOUR:
            return asks.refused(notices.MESSAGES["rate-hour"])
        if self.undelivered >= notices.NOTICE_UNDELIVERED:
            return asks.refused(notices.MESSAGES["undelivered"])
        self.notices.append(req)
        self.notices_this_hour += 1
        notice_id = notices.new_id(lambda _i: False)
        return asks.AskReply("posted", f"posted {notice_id}")
