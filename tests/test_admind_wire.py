"""cxmkf mixed versions on ask.sock (design §2.1): old clients against the new daemon, new clients against
an old daemon, and new replies read by an old client.

The `Old*` structs are frozen copies of the request and reply types at 5a9ec58 (before cxmkf); they must
not follow later changes.
"""

import asyncio
from pathlib import Path
from typing import Literal

import msgspec
import pytest
from test_admind_approvals import BEAD, go
from test_admind_asks import BODY, HEAD, PR, records
from test_admind_daemon import Harness, needs_tmux

from heterodyne.admind import asks, ctl
from heterodyne.admind.audit import Audit
from heterodyne.admind.links import LinkSpec
from heterodyne.admind.notices import NoticePost

OldAskKind = Literal["question", "merge", "approval"]


# --- frozen at 5a9ec58 ---------------------------------------------------------------------------
class OldAskPost(msgspec.Struct, frozen=True, tag="post", forbid_unknown_fields=True):
    kind: OldAskKind
    title: str = ""
    body: str = ""
    pr_url: str | None = None
    head_sha: str | None = None
    bead: str | None = None
    poster: str = "local"


class OldAskGet(msgspec.Struct, frozen=True, tag="get", forbid_unknown_fields=True):
    ask_id: str


class OldAskList(msgspec.Struct, frozen=True, tag="list", forbid_unknown_fields=True):
    pass


class OldAskCancel(msgspec.Struct, frozen=True, tag="cancel", forbid_unknown_fields=True):
    ask_id: str


OldAskRequest = OldAskPost | OldAskGet | OldAskList | OldAskCancel


class OldAnswerView(msgspec.Struct, frozen=True):
    kind: str
    operator: str
    text: str
    at: str


class OldAskSummary(msgspec.Struct, frozen=True):
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


class OldAskView(msgspec.Struct, frozen=True):
    summary: OldAskSummary
    answers: list[OldAnswerView]


class OldAskReply(msgspec.Struct, frozen=True):
    result: Literal["posted", "ok", "refused", "failed"]
    message: str
    ask: OldAskView | None = None
    asks: list[OldAskSummary] | None = None
# -------------------------------------------------------------------------------------------------


SHA = "0123456789abcdef" * 2 + "01234567"
LINK = LinkSpec("design", f"https://github.com/owner/repo/blob/{SHA}/docs/design.md")
OLD_QUESTION = OldAskPost("question", "Which relay?", BODY, poster="controller")
OLD_MERGE = OldAskPost("merge", "Merge plan 3 (wsd intake)", BODY, PR, HEAD, poster="controller")
OLD_APPROVAL = OldAskPost("approval", bead=BEAD, poster="controller")


async def old_post(h: Harness, req: OldAskPost) -> OldAskReply:
    server = h.daemon.ask_server
    assert server is not None
    return await ctl.request(server.path, req, reply_type=OldAskReply, max_reply=asks.MAX_REPLY)


# --- an old client, the new daemon ---------------------------------------------------------------
def test_an_old_post_encodes_as_the_new_one_without_the_new_fields() -> None:
    new_approval = msgspec.json.encode(asks.AskPost("approval", bead=BEAD, poster="controller"))
    assert b"form" not in new_approval and b"links" not in new_approval      # omit_defaults
    for old in (OLD_QUESTION, OLD_MERGE, OLD_APPROVAL):
        new = msgspec.json.decode(msgspec.json.encode(old), type=asks.AskRequest)
        assert isinstance(new, asks.AskPost) and new.form is None and new.choices == [] and new.links == []
        assert msgspec.json.decode(msgspec.json.encode(new), type=OldAskRequest) == old   # omit_defaults


@needs_tmux
def test_old_posts_to_the_new_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hello")
        await h.until(lambda: "echo: hello" in h.texts())
        for req, kind in ((OLD_APPROVAL, "approval"), (OLD_MERGE, "merge")):
            reply = await old_post(h, req)
            assert reply.result == "posted" and reply.ask is not None, reply
            assert reply.ask.summary.kind == kind and asks.FORM_WARNING not in reply.message
        reply = await old_post(h, OLD_QUESTION)
        assert reply.result == "posted" and reply.ask is not None, reply
        assert reply.message == f"ask {reply.ask.summary.ask_id} posted\n{asks.FORM_WARNING}"
        before = h.store.db.execute("SELECT COUNT(*) FROM asks").fetchone()[0]
        monkeypatch.setattr(asks, "REQUIRE_QUESTION_FORM", True)
        assert await old_post(h, OLD_QUESTION) == OldAskReply("refused", asks.FORM_NEEDED)
        assert h.store.db.execute("SELECT COUNT(*) FROM asks").fetchone()[0] == before
        reply = await old_post(h, OLD_MERGE)               # merges have no form
        assert reply.result == "posted", reply
    go(tmp_path, scenario)


# --- a new client, an old daemon -----------------------------------------------------------------
NEW_POSTS = [
    asks.AskPost("question", "Which relay?", BODY, poster="controller", form="choices", choices=["a", "b"],
                 recommended="A"),
    asks.AskPost("question", "Which relay?", BODY, poster="controller", form="yes-no"),
    asks.AskPost("question", "Which relay?", BODY, poster="controller", links=[LINK]),
    asks.AskPost("merge", "Merge plan 3", BODY, PR, HEAD, poster="controller", links=[LINK]),
]
NEW_REQUESTS: list[msgspec.Struct] = [*NEW_POSTS, NoticePost("Plan 4 merged", "In.", [LINK], "controller")]


@pytest.mark.parametrize("req", NEW_REQUESTS)
def test_the_old_request_type_refuses_new_requests(req: msgspec.Struct) -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(msgspec.json.encode(req), type=OldAskRequest)


def test_an_old_daemon_refuses_new_requests_cleanly_and_stores_nothing(tmp_path: Path) -> None:
    seen: list[OldAskRequest] = []

    async def handler(req: OldAskRequest, peer: ctl.Peer) -> OldAskReply:
        seen.append(req)
        return OldAskReply("posted", "ask k7m2 posted")

    async def body() -> None:
        server = ctl.JsonSocketServer[OldAskRequest, OldAskReply](
            tmp_path / "s" / asks.ASK_SOCKET, handler, Audit(tmp_path / "s" / "audit.jsonl"), kind="ask",
            max_request=asks.MAX_REQUEST, max_reply=asks.MAX_REPLY, request_type=OldAskRequest,
            check=lambda _req: None, refused=lambda m: OldAskReply("refused", m),
            failed=lambda: OldAskReply("failed", asks.INTERNAL_ERROR), describe=lambda _req: {"op": "old"})
        await server.start()
        try:
            for req in NEW_REQUESTS:
                reply = await ctl.request(server.path, req, reply_type=asks.AskReply,
                                          max_reply=asks.MAX_REPLY)
                assert reply == asks.refused("malformed request"), req
            plain = asks.AskPost("question", "Which relay?", BODY, poster="controller")   # no new fields
            reply = await ctl.request(server.path, plain, reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
            assert reply.result == "posted"
        finally:
            await server.close()

    asyncio.run(body())
    assert seen == [OLD_QUESTION]                     # only the post without the new fields reached it
    assert all(r["kind"] == "ask" for r in records(tmp_path / "s" / "audit.jsonl"))


# --- new replies, an old client ------------------------------------------------------------------
def test_an_old_client_reads_a_new_reply_and_ignores_choice() -> None:
    summary = asks.AskSummary("k7m2", "question", "answered", "Which relay?", None, None, True, 2, None, None)
    answers = [asks.AnswerView("answer", "op", "👍", "2026-10-14T10:00:00+00:00", "A"),
               asks.AnswerView("answer", "op", "port 8000", "2026-10-14T10:01:00+00:00")]
    new = asks.AskReply("ok", "answered", ask=asks.AskView(summary, answers), asks=[summary])
    encoded = msgspec.json.encode(new)
    assert b'"choice":"A"' in encoded
    old = msgspec.json.decode(encoded, type=OldAskReply)
    assert old.ask is not None and old.asks is not None
    assert old.ask.answers == [OldAnswerView("answer", "op", "👍", "2026-10-14T10:00:00+00:00"),
                               OldAnswerView("answer", "op", "port 8000", "2026-10-14T10:01:00+00:00")]
    assert old.ask.summary.ask_id == "k7m2" and old.asks[0].answer_count == 2
    posted = msgspec.json.decode(msgspec.json.encode(
        asks.AskReply("posted", f"ask k7m2 posted\n{asks.FORM_WARNING}")), type=OldAskReply)
    assert posted.message.split("\n")[0] == "ask k7m2 posted"
