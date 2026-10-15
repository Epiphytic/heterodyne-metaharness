"""`wsd.relay`, wsd's `ask.sock` client (plan 5i part 2, Task 1). Offline: admind is `fake_ask_sock`.

This is the only module that imports both `wsd.relay` and `admind`: it proves the mirrored wire structs
encode exactly as admind's, and that admind's replies and refusal texts map as the client expects.
"""

import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import msgspec
import pytest
from fake_ask_sock import FakeAskSock
from hypothesis import given
from hypothesis import strategies as st
from sandbox_env import short_dir

from heterodyne.admind import asks, daemon, links, notices
from heterodyne.wsd import relay

TEXT = st.text(max_size=40)
OPT_TEXT = st.none() | TEXT
LINK_ARGS = st.tuples(TEXT, TEXT)
BODY = "context for the operators, long enough to pass admind's minimum body check. " * 2


@pytest.fixture
def fake() -> Iterator[FakeAskSock]:
    with short_dir() as d:
        f = FakeAskSock(d / "ask.sock").start()
        try:
            yield f
        finally:
            f.stop()


def question(**kw: object) -> relay.AskPost:
    fields: dict[str, object] = {"kind": "question", "title": "Which way?", "body": BODY, "poster": "wsd-ws1"}
    fields.update(kw)
    return relay.AskPost(**fields)  # type: ignore[arg-type]


# --- the wire structs ---------------------------------------------------------------------------
@given(kind=st.sampled_from(["question", "merge", "approval"]), title=TEXT, body=TEXT, pr_url=OPT_TEXT,
       head_sha=OPT_TEXT, bead=OPT_TEXT, poster=TEXT, form=st.none() | st.sampled_from(["choices", "yes-no"]),
       choices=st.lists(TEXT, max_size=3), recommended=OPT_TEXT, link_args=st.lists(LINK_ARGS, max_size=2))
def test_ask_post_encodes_exactly_as_admind(kind: str, title: str, body: str, pr_url: str | None,
                                            head_sha: str | None, bead: str | None, poster: str,
                                            form: str | None, choices: list[str], recommended: str | None,
                                            link_args: list[tuple[str, str]]) -> None:
    common: dict[str, object] = {"kind": kind, "title": title, "body": body, "pr_url": pr_url,
                                 "head_sha": head_sha, "bead": bead, "poster": poster, "form": form,
                                 "choices": choices, "recommended": recommended}
    ours = relay.AskPost(**common, links=[relay.LinkSpec(*a) for a in link_args])  # type: ignore[arg-type]
    theirs = asks.AskPost(**common, links=[links.LinkSpec(*a) for a in link_args])  # type: ignore[arg-type]
    assert msgspec.json.encode(ours) == msgspec.json.encode(theirs)


def test_a_default_ask_post_omits_its_defaults_as_admind_does() -> None:
    assert msgspec.json.encode(relay.AskPost("approval", bead="ws-1")) == msgspec.json.encode(
        asks.AskPost("approval", bead="ws-1"))


@given(ask_id=TEXT)
def test_get_list_and_cancel_encode_exactly_as_admind(ask_id: str) -> None:
    assert msgspec.json.encode(relay.AskGet(ask_id)) == msgspec.json.encode(asks.AskGet(ask_id))
    assert msgspec.json.encode(relay.AskCancel(ask_id)) == msgspec.json.encode(asks.AskCancel(ask_id))
    assert msgspec.json.encode(relay.AskList()) == msgspec.json.encode(asks.AskList())


@given(title=TEXT, body=TEXT, poster=TEXT, link_args=st.lists(LINK_ARGS, max_size=2))
def test_notice_post_encodes_exactly_as_admind(title: str, body: str, poster: str,
                                               link_args: list[tuple[str, str]]) -> None:
    ours = relay.NoticePost(title, body, [relay.LinkSpec(*a) for a in link_args], poster)
    theirs = notices.NoticePost(title, body, [links.LinkSpec(*a) for a in link_args], poster)
    assert msgspec.json.encode(ours) == msgspec.json.encode(theirs)
    assert msgspec.json.encode(relay.NoticePost(title)) == msgspec.json.encode(notices.NoticePost(title))


def summary(**kw: object) -> asks.AskSummary:
    fields: dict[str, object] = {"ask_id": "abcd", "kind": "question", "status": "answered", "title": "t",
                                 "bead": "ws-1", "digest12": None, "delivered": True, "answer_count": 1,
                                 "outcome": None, "decided_by": None}
    fields.update(kw)
    return asks.AskSummary(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("reply", [
    asks.AskReply("posted", "ask abcd posted", ask=asks.AskView(summary(status="open", answer_count=0), [])),
    asks.AskReply("ok", "answered", ask=asks.AskView(summary(), [
        asks.AnswerView("answer", "liam", "go left", "2026-10-14T00:00:00+00:00", "A"),
        asks.AnswerView("answer", "liam", "free text", "2026-10-14T00:00:01+00:00")])),
    asks.AskReply("ok", "approved", ask=asks.AskView(summary(kind="approval", status="approved",
                                                             digest12="0123456789ab", outcome="approved",
                                                             decided_by="liam"), [])),
    asks.AskReply("ok", "2 asks", asks=[summary(), summary(ask_id="efgh", bead=None)]),
    asks.AskReply("ok", "ask abcd cancelled"),
    asks.refused(daemon.ASK_BUSY),
    asks.failed(),
])
def test_admind_replies_decode_into_the_mirrors(reply: asks.AskReply) -> None:
    data = msgspec.json.encode(reply)
    assert msgspec.json.encode(msgspec.json.decode(data, type=relay.AskReply)) == data


def test_a_reply_with_extra_fields_still_decodes() -> None:
    data = msgspec.json.encode({**msgspec.to_builtins(asks.AskReply(
        "ok", "answered", ask=asks.AskView(summary(), []))), "newer": 1})
    data = data.replace(b'"answer_count":1', b'"answer_count":1,"newer_too":"x"')
    got = msgspec.json.decode(data, type=relay.AskReply)
    assert got.ask is not None and got.ask.summary.answer_count == 1


def test_the_mirrored_refusal_texts_are_admind_s() -> None:
    assert relay.ASK_BUSY == daemon.ASK_BUSY
    assert relay.ASK_LATCHED == daemon.ASK_LATCHED
    assert relay.ASK_TOO_MANY == daemon.ASK_TOO_MANY
    assert relay.ASK_TOO_OFTEN == daemon.ASK_TOO_OFTEN
    assert relay.ASK_BEAD_BUSY == daemon.ASK_BEAD_BUSY
    assert relay.BEAD_BUSY == daemon.BEAD_BUSY
    assert relay.NOTICE_MESSAGES == notices.MESSAGES
    assert relay.MAX_REPLY == asks.MAX_REPLY


def test_relay_does_not_import_admind() -> None:
    code = ("import sys, heterodyne.wsd.relay\n"
            "bad = [m for m in sys.modules if m.startswith('heterodyne.admind')]\n"
            "assert not bad, bad\n")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


# --- the client ---------------------------------------------------------------------------------
def test_post_ask_returns_the_id_and_sends_one_line(fake: FakeAskSock) -> None:
    ask_id = relay.post_ask(fake.path, question())
    assert ask_id in fake.asks and fake.asks[ask_id].post.title == "Which way?"
    assert len(fake.frames) == 1
    frame = fake.frames[0]
    assert frame.endswith(b"\n") and frame.count(b"\n") == 1
    assert frame == msgspec.json.encode(question()) + b"\n"


def test_get_list_and_cancel_round_trip(fake: FakeAskSock) -> None:
    ask_id = relay.post_ask(fake.path, question())
    fake.answer(ask_id, "go left", choice=None)
    view = relay.get_ask(fake.path, ask_id)
    assert view.summary.status == "answered" and [a.text for a in view.answers] == ["go left"]
    assert [s.ask_id for s in relay.list_asks(fake.path)] == [ask_id]
    relay.cancel_ask(fake.path, ask_id)
    assert fake.asks[ask_id].status == "cancelled"


def test_post_notice_returns_the_notice_id(fake: FakeAskSock) -> None:
    notice_id = relay.post_notice(fake.path, poster="wsd-ws1", title="ws1-1 is stuck", body="why")
    assert len(notice_id) == notices.ID_LENGTH
    assert [(n.poster, n.title, n.body) for n in fake.notices] == [("wsd-ws1", "ws1-1 is stuck", "why")]


def refusing_socket(d: Path) -> Path:
    """A socket path with nothing listening: bound, then closed, so a connect is refused."""
    path = d / "dead.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(path))
    s.close()
    return path


@pytest.mark.parametrize("make", ["missing", "regular-file", "refused"])
def test_connect_failure_is_unavailable_and_sends_nothing(make: str) -> None:
    with short_dir() as d:
        path = d / "ask.sock"
        if make == "regular-file":
            path.write_text("")
        elif make == "refused":
            path = refusing_socket(d)
        with pytest.raises(relay.RelayUnavailable):
            relay.post_ask(path, question())
        with pytest.raises(relay.RelayUnavailable):
            relay.get_ask(path, "abcd")
        with pytest.raises(relay.NoticeUnavailable):
            relay.post_notice(path, poster="wsd-ws1", title="t")
        if make == "regular-file":
            assert path.read_text() == ""


@pytest.mark.parametrize("step", ["drop", "hang", "garbage", "failed"])
def test_a_lost_reply_after_the_write_is_uncertain(fake: FakeAskSock, step: str) -> None:
    fake.script.extend([step, step])
    with pytest.raises(relay.RelayUncertain):
        relay.post_ask(fake.path, question(), timeout=0.3)
    with pytest.raises(relay.NoticeUncertain):
        relay.post_notice(fake.path, poster="wsd-ws1", title="t", timeout=0.3)
    assert len(fake.frames) == 2        # both requests were written before the reply was lost


RETRYABLE_ASK = [daemon.ASK_BUSY, daemon.ASK_LATCHED, daemon.ASK_TOO_MANY, daemon.ASK_TOO_OFTEN,
                 daemon.ASK_BEAD_BUSY.format(ask_id="abcd", bead="ws-1"),
                 daemon.BEAD_BUSY.format(bead="ws-1")]
PERMANENT_ASK = [daemon.ASK_NO_APPROVALS, asks.REDACTED, asks.TOO_LONG, "malformed request",
                 "no ask abcd", f"--title must be one line of 1-{asks.MAX_TITLE} characters"]


@pytest.mark.parametrize(("message", "retryable"),
                         [(m, True) for m in RETRYABLE_ASK] + [(m, False) for m in PERMANENT_ASK])
def test_scripted_ask_refusals_map_to_retryable_or_permanent(fake: FakeAskSock, message: str,
                                                             retryable: bool) -> None:
    fake.script.append(message)
    with pytest.raises(relay.RelayRefused) as info:
        relay.post_ask(fake.path, question())
    assert info.value.message == message and info.value.retryable is retryable


def test_admind_s_own_ask_refusals_map_as_scripted(fake: FakeAskSock) -> None:
    def refusal(req: relay.AskPost) -> relay.RelayRefused:
        with pytest.raises(relay.RelayRefused) as info:
            relay.post_ask(fake.path, req)
        return info.value

    bad = refusal(question(title=""))           # asks.check
    assert not bad.retryable and bad.message == f"--title must be one line of 1-{asks.MAX_TITLE} characters"
    fake.latched = True
    assert refusal(question()).retryable
    fake.latched, fake.busy = False, True
    assert refusal(question()).retryable
    fake.busy, fake.posted_this_hour = False, asks.MAX_PER_HOUR
    assert refusal(question()).retryable
    fake.posted_this_hour, fake.approvals = 0, False
    assert not refusal(relay.AskPost("approval", bead="ws-1", poster="wsd-ws1")).retryable


@pytest.mark.parametrize(("code", "retryable"), [("latched", True), ("rate-hour", True),
                                                 ("undelivered", True), ("title", False),
                                                 ("body-too-long", False), ("notice-too-long", False)])
def test_notice_refusals_carry_admind_s_code(fake: FakeAskSock, code: str, retryable: bool) -> None:
    fake.script.append(notices.MESSAGES[code])
    with pytest.raises(relay.NoticeRefused) as info:
        relay.post_notice(fake.path, poster="wsd-ws1", title="t")
    assert info.value.code == code and info.value.retryable is retryable


def test_admind_s_own_notice_refusals_carry_their_code(fake: FakeAskSock) -> None:
    def code(poster: str = "wsd-ws1", title: str = "t", body: str = "") -> str:
        with pytest.raises(relay.NoticeRefused) as info:
            relay.post_notice(fake.path, poster=poster, title=title, body=body)
        return info.value.code

    assert code(title="two\nlines") == "title"
    assert code(body="x\n" * (notices.MAX_BODY_LINES + 1)) == "body-too-long"
    assert code(poster="Not A Poster") == "poster"
    fake.undelivered = notices.NOTICE_UNDELIVERED
    assert code() == "undelivered"
    fake.undelivered, fake.notices_this_hour = 0, notices.NOTICE_PER_HOUR
    assert code() == "rate-hour"
    fake.latched = True
    assert code() == "latched"


def test_errors_never_carry_the_socket_path() -> None:
    with short_dir() as d:
        path = d / "ask.sock"
        with pytest.raises(relay.RelayUnavailable) as info:
            relay.post_ask(path, question())
        assert str(d) not in str(info.value) and os.fspath(path) not in repr(info.value)


@given(ws=st.text(min_size=1, max_size=200))
def test_poster_for_always_matches_admind_poster_rule(ws: str) -> None:
    poster = relay.poster_for(ws)
    assert asks.POSTER.fullmatch(poster) and poster.startswith("wsd-")


def test_poster_for_keeps_a_slug() -> None:
    assert relay.poster_for("Hermes-Main") == "wsd-hermes-main"
    assert relay.poster_for("a" * 100) == "wsd-" + "a" * 28
