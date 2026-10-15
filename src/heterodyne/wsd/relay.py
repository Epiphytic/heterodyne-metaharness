"""wsd's only `ask.sock` client: questions, approval asks and notices through admind's interim relay
(ADR 0001 §8.2; docs/admind.md §10).

admind shares no dependency with wsd (§8), so the wire structs are mirrored here field for field and tag for
tag from `admind/asks.py` and `admind/notices.py`, never imported; a contract test proves the encodings are
identical. So are the refusal texts the client sorts into retryable and permanent. The framing is admind's:
one JSON line out, one line back, per connection.

Each call is synchronous (it runs its own event loop), because the callers run on a workstream's lane
thread. Three outcomes besides success: unavailable (nothing was sent), uncertain (the request was written
and the reply was lost, timed out, or was admind's internal error), and refused (admind's own message). The
errors name the check, never the socket path.
"""

import asyncio
import re
from pathlib import Path
from typing import Literal

import msgspec

MAX_REPLY = 1_048_576           # admind's asks.MAX_REPLY
CONNECT_SECONDS = 5.0
CLOSE_SECONDS = 2.0
POSTER_PREFIX = "wsd-"
POSTER_MAX = 32                 # admind's asks.POSTER: [a-z0-9][a-z0-9._-]{0,31}
_NOT_POSTER = re.compile(r"[^a-z0-9._-]")

AskKind = Literal["question", "merge", "approval"]
FormKind = Literal["choices", "yes-no"]

# admind's refusal texts for a post (admind/daemon.py), mirrored: these are the retryable ones.
ASK_BUSY = "admind is already handling two posts; try again shortly."
ASK_LATCHED = "admind is latched and posts nothing; the ask was not posted."
ASK_TOO_MANY = "there are already 20 active asks; cancel some or wait for answers."
ASK_TOO_OFTEN = "30 asks were posted in the last hour; try again later."
ASK_BEAD_BUSY = "ask {ask_id} for {bead} is being decided or awaits a read-back; post again once it settles."
BEAD_BUSY = "{bead} is being decided elsewhere right now. Nothing recorded; try again in a minute."

# admind's K9 notice codes and their texts (admind/notices.py MESSAGES, with admind/links.py's).
NOTICE_MESSAGES = {
    "latched": "admind is latched and posts nothing; the notice was not posted.",
    "title": "--title must be one line of 1-200 characters",
    "body-too-long": "the body is over 3,500 characters or 40 lines",
    "notice-too-long": "the notice is over 6,000 characters",
    "too-many-parts": "the notice needs more than 4 messages at this group's message size",
    "link-line-too-long": "a link line is longer than this group's message size allows; shorten it",
    "rate-hour": "12 notices were posted in the last hour; try again later.",
    "undelivered": "10 notices are still waiting to be delivered; try again later.",
    "link-label": "a --link label must be one line of 1-40 characters",
    "link-too-long": "a --link URL is over 400 characters",
    "link-format": "--link must be a GitHub URL pinned to a 40-hex commit, or a PR, run or issue",
    "link-redacted": "a --link would be redacted; remove it",
    "too-many-links": "at most 8 --link and --ref in all",
}
NOTICE_RETRYABLE = frozenset({"latched", "rate-hour", "undelivered"})
# Refusals from admind's request check, before any notice code: wsd's own names for them.
NOTICE_POSTER, NOTICE_MALFORMED, NOTICE_OTHER = "poster", "malformed", "other"


def _template(text: str) -> re.Pattern[str]:
    """A refusal text with `{field}` placeholders as a pattern matching any one-line value for each."""
    parts = re.split(r"\{[a-z_]+\}", text)
    return re.compile("[^\n]+".join(map(re.escape, parts)))


_RETRYABLE_ASK = (*map(re.compile, map(re.escape, (ASK_BUSY, ASK_LATCHED, ASK_TOO_MANY, ASK_TOO_OFTEN))),
                  _template(ASK_BEAD_BUSY), _template(BEAD_BUSY))
_NOTICE_CODES = {text: code for code, text in NOTICE_MESSAGES.items()}


# --- the wire structs (mirrors of admind's) -----------------------------------------------------
class LinkSpec(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    label: str
    url: str


class AskPost(msgspec.Struct, frozen=True, tag="post", forbid_unknown_fields=True, omit_defaults=True):
    kind: AskKind
    title: str = ""
    body: str = ""
    pr_url: str | None = None
    head_sha: str | None = None
    bead: str | None = None
    poster: str = "local"
    form: FormKind | None = None
    choices: list[str] = []
    recommended: str | None = None
    links: list[LinkSpec] = []


class AskGet(msgspec.Struct, frozen=True, tag="get", forbid_unknown_fields=True):
    ask_id: str


class AskList(msgspec.Struct, frozen=True, tag="list", forbid_unknown_fields=True):
    pass


class AskCancel(msgspec.Struct, frozen=True, tag="cancel", forbid_unknown_fields=True):
    ask_id: str


class NoticePost(msgspec.Struct, frozen=True, tag="notice", forbid_unknown_fields=True):
    title: str
    body: str = ""
    links: list[LinkSpec] = []
    poster: str = "local"


AskRequest = AskPost | AskGet | AskList | AskCancel | NoticePost


class AnswerView(msgspec.Struct, frozen=True):
    kind: str
    operator: str
    text: str
    at: str
    choice: str | None = None


class AskSummary(msgspec.Struct, frozen=True):
    ask_id: str
    kind: str
    status: str
    title: str
    bead: str | None
    digest12: str | None
    delivered: bool
    answer_count: int
    outcome: str | None
    decided_by: str | None


class AskView(msgspec.Struct, frozen=True):
    summary: AskSummary
    answers: list[AnswerView]


class AskReply(msgspec.Struct, frozen=True):
    result: Literal["posted", "ok", "refused", "failed"]
    message: str
    ask: AskView | None = None
    asks: list[AskSummary] | None = None


# --- errors -------------------------------------------------------------------------------------
class RelayUnavailable(Exception):
    """No connection to ask.sock (missing, not a socket, or refused): nothing was sent."""

    def __init__(self, why: str) -> None:
        super().__init__(f"ask.sock unavailable: {why}")
        self.why = why


class RelayUncertain(Exception):
    """The request was written, and its reply was lost, timed out, unreadable or admind's internal error:
    it may or may not have taken effect."""

    def __init__(self, why: str) -> None:
        super().__init__(f"ask.sock reply uncertain: {why}")
        self.why = why


class RelayRefused(Exception):
    """admind refused the request with `message`. `retryable`: only the latch, a busy post or bead, and
    the active-ask limits (open, per hour, in flight), which pass on their own."""

    def __init__(self, message: str) -> None:
        super().__init__(f"ask.sock refused: {message}")
        self.message = message
        self.retryable = any(p.fullmatch(message) for p in _RETRYABLE_ASK)


class NoticeUnavailable(RelayUnavailable):
    """As RelayUnavailable, for a notice."""


class NoticeUncertain(RelayUncertain):
    """As RelayUncertain, for a notice."""


class NoticeRefused(Exception):
    """admind refused a notice. `code` is admind's K9 code, or `poster`, `malformed` or `other` for a
    refusal before one; `retryable` for `latched`, `rate-hour` and `undelivered`."""

    def __init__(self, code: str) -> None:
        super().__init__(f"notice refused: {code}")
        self.code = code
        self.retryable = code in NOTICE_RETRYABLE


def notice_code(message: str) -> str:
    """The K9 code of a notice refusal's text."""
    if message in _NOTICE_CODES:
        return _NOTICE_CODES[message]
    if message.startswith("--from must match"):
        return NOTICE_POSTER
    return NOTICE_MALFORMED if message == "malformed request" else NOTICE_OTHER


# --- the transport ------------------------------------------------------------------------------
async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), CLOSE_SECONDS)
    except Exception:  # noqa: BLE001 - timed out or failed: either way the connection is dropped
        writer.transport.abort()


async def _exchange(sock: Path, req: AskRequest, timeout: float) -> AskReply:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(sock), limit=MAX_REPLY + 2), min(CONNECT_SECONDS, timeout))
    except (OSError, TimeoutError) as exc:
        raise RelayUnavailable(type(exc).__name__) from None
    try:
        async with asyncio.timeout(timeout):
            writer.write(msgspec.json.encode(req) + b"\n")
            await writer.drain()
            line = await reader.readline()
    except (OSError, TimeoutError, ValueError) as exc:      # ValueError: a reply over the limit
        raise RelayUncertain(type(exc).__name__) from None
    finally:
        await _close(writer)
    if not line.endswith(b"\n"):
        raise RelayUncertain("no reply")
    try:
        reply = msgspec.json.decode(line, type=AskReply)
    except msgspec.DecodeError:
        raise RelayUncertain("unreadable reply") from None
    if reply.result == "failed":
        raise RelayUncertain("admind internal error")
    if reply.result == "refused":
        raise RelayRefused(reply.message)
    return reply


def _call(sock: Path, req: AskRequest, timeout: float) -> AskReply:
    return asyncio.run(_exchange(sock, req, timeout))


def _view(reply: AskReply, result: str) -> AskView:
    if reply.result != result or reply.ask is None:
        raise RelayUncertain("unexpected reply")
    return reply.ask


def post_ask(sock: Path, req: AskPost, timeout: float = 160.0) -> str:
    """Post a question, merge or approval ask; its ask ID. 160 seconds is admind's own budget for a post
    (docs/admind.md, Limits)."""
    return _view(_call(sock, req, timeout), "posted").summary.ask_id


def get_ask(sock: Path, ask_id: str, timeout: float = 30.0) -> AskView:
    return _view(_call(sock, AskGet(ask_id), timeout), "ok")


def cancel_ask(sock: Path, ask_id: str, timeout: float = 30.0) -> None:
    if _call(sock, AskCancel(ask_id), timeout).result != "ok":
        raise RelayUncertain("unexpected reply")


def list_asks(sock: Path, timeout: float = 30.0) -> list[AskSummary]:
    """admind's active asks and its 20 most recent: used to find an uncertain post before posting again."""
    reply = _call(sock, AskList(), timeout)
    if reply.result != "ok" or reply.asks is None:
        raise RelayUncertain("unexpected reply")
    return reply.asks


def post_notice(sock: Path, *, poster: str, title: str, body: str = "", timeout: float = 30.0) -> str:
    """Post an FYI notice; its notice ID. Raises NoticeUnavailable, NoticeUncertain or NoticeRefused."""
    try:
        reply = _call(sock, NoticePost(title, body, poster=poster), timeout)
    except RelayUnavailable as exc:
        raise NoticeUnavailable(exc.why) from None
    except RelayUncertain as exc:
        raise NoticeUncertain(exc.why) from None
    except RelayRefused as exc:
        raise NoticeRefused(notice_code(exc.message)) from None
    notice_id = reply.message.removeprefix("posted ")
    if reply.result != "posted" or notice_id == reply.message or not notice_id:
        raise NoticeUncertain("unexpected reply")
    return notice_id


def poster_for(ws: str) -> str:
    """The `--from` name for workstream `ws`: `wsd-` and the name, lowercased, with anything outside
    admind's poster rule replaced by `-`, cut to its 32 characters."""
    return (POSTER_PREFIX + _NOT_POSTER.sub("-", ws.lower()))[:POSTER_MAX]
