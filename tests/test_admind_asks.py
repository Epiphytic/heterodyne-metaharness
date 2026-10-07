"""Relay plan Task 2: asks over ask.sock, question and merge cards, answers by reply or `!answer`, `!asks`
(relay spec R1-R4, R7, R8, R13-R18, R24; §5, §6).

Nothing here runs the real claude or wn-agent, touches ~/.claude, bd or the network: the admin agent is the
fake claude in a private tmux server, wn-agent is the fake, and every socket is under tmp_path.
"""

import asyncio
import io
import json
import os
import stat
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import msgspec
import pytest
from admind_asks_fixture import approve_bead_wrapper, bead_db, btq_log, sent_mid
from fakes.settings import make_settings
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_r13_details import ask as say_to
from test_admind_r13_details import delivered, details_texts, summary_delivered
from test_admind_r13_replies import LONG, configure

from heterodyne import platform
from heterodyne.admind import asks, cli, commands, ctl
from heterodyne.admind.audit import Audit
from heterodyne.admind.daemon import (
    ASK_BUSY,
    ASK_LATCHED,
    ASK_NO_APPROVALS,
    ASK_TOO_MANY,
    ASK_TOO_OFTEN,
    NO_ACTIVE_ASKS,
)
from heterodyne.admind.store import AskRow, Store

BODY = ("The throwaway home needs one relay. The reference install has two, and either would work for "
        "the test operator; pick the one you would rather keep.")
PR = "https://github.com/owner/repo/pull/17"
HEAD = "0123456789abcdef0123456789abcdef01234567"
TOKEN = "ghp_" + "A" * 36
NONE = ctl.Peer(None)


def question(title: str = "Which relay?", body: str = BODY, **kw: Any) -> asks.AskPost:
    return asks.AskPost("question", title, body, poster=kw.pop("poster", "controller"), **kw)


def merge(**kw: Any) -> asks.AskPost:
    return asks.AskPost("merge", "Merge plan 3 (wsd intake)", BODY, kw.pop("pr_url", PR),
                        kw.pop("head_sha", HEAD), poster="controller", **kw)


def row(ask_id: str = "k7m2", kind: str = "question", body: str = BODY, **kw: Any) -> AskRow:
    fields: dict[str, Any] = {
        "ask_id": ask_id, "kind": kind, "poster": "controller", "title": "Which relay?", "body": body,
        "pr_url": PR if kind == "merge" else None, "head_sha": HEAD if kind == "merge" else None,
        "bead": None, "digest": None, "truncated": False, "card_parts": 1, "status": "open", "outcome": None,
        "decided_by": None, "created_at": "2026-10-05T10:00:00+00:00",
        "updated_at": "2026-10-05T10:00:00+00:00"}
    return AskRow(**{**fields, **kw})


# --- asks.check ----------------------------------------------------------------------------------
@pytest.mark.parametrize(("req", "why"), [
    (question(title=""), "--title"),
    (question(title="x" * 201), "--title"),
    (question(title="   "), "--title"),
    (question(title="two\nlines"), "--title"),
    (question(title="tab\there"), "--title"),
    (question(body="x" * 79 + " " * 50), "at least 80"),
    (question(body="x " * 40 + "\n" * 100), "at least 80"),          # 40 non-space characters
    (question(body="x" * 16_001), "16,000"),
    (merge(pr_url=None), "--pr"),
    (merge(pr_url="https://github.com/owner/repo/pull/0"), "--pr"),
    (merge(pr_url="https://github.com/owner/repo/pull/17/files"), "--pr"),
    (merge(pr_url="http://github.com/owner/repo/pull/17"), "--pr"),
    (merge(pr_url="https://github.com.evil/owner/repo/pull/17"), "--pr"),
    (merge(pr_url="https://github.com/own er/repo/pull/17"), "--pr"),
    (merge(head_sha=None), "--head"),
    (merge(head_sha=HEAD[:-1]), "--head"),
    (merge(head_sha=HEAD.upper()), "--head"),
    (merge(head_sha=HEAD + "0"), "--head"),
    (question(pr_url=PR), "merge asks only"),
    (question(head_sha=HEAD), "merge asks only"),
    (question(poster="Controller"), "--from"),
    (question(poster="-x"), "--from"),
    (question(poster="a" * 33), "--from"),
    (question(poster=""), "--from"),
    (question(bead="-btq-ab12c"), "--bead"),
    (question(bead="btq_ab12c"), "--bead"),
    (question(bead="btq-AB12C"), "--bead"),
    (asks.AskPost("approval", bead=None), "needs --bead"),
    (asks.AskPost("approval", "t", bead="btq-ab12c"), "only --bead"),
    (asks.AskPost("approval", body=BODY, bead="btq-ab12c"), "only --bead"),
    (asks.AskPost("approval", bead="btq-ab12c", pr_url=PR), "only --bead"),
    (asks.AskGet("k7m"), "ask ID"),
    (asks.AskGet("k7m1"), "ask ID"),
    (asks.AskCancel("k7mo2"), "ask ID"),
])
def test_check_refuses(req: asks.AskRequest, why: str) -> None:
    refusal = asks.check(req)
    assert refusal is not None and why in refusal


@pytest.mark.parametrize("req", [
    question(title="x"), question(title="x" * 200), question(body="y" * 80), question(body="y" * 16_000),
    question(body=" ".join("y" * 80)), merge(), merge(pr_url="https://github.com/o-1/r.e_p-o/pull/9"),
    question(poster="a" * 32), question(poster="a.b_c-1"), question(bead="btq-ab12c.1"),
    asks.AskPost("approval", bead="btq-ab12c"), asks.AskGet("K7M2"), asks.AskCancel("k7m2"), asks.AskList(),
])
def test_check_accepts(req: asks.AskRequest) -> None:
    assert asks.check(req) is None


def test_describe_never_holds_text() -> None:
    fields = asks.describe(question(title="secret title", body=BODY))
    assert "secret title" not in json.dumps(fields) and BODY not in json.dumps(fields)
    assert fields["op"] == "post" and fields["ask_kind"] == "question" and fields["body_chars"] == len(BODY)
    assert asks.describe(asks.AskGet("K7M2")) == {"op": "get", "ask_id": "k7m2"}


# --- IDs -----------------------------------------------------------------------------------------
def test_new_id_alphabet_and_retry() -> None:
    for _ in range(200):
        ask_id = asks.new_id(lambda _: False)
        assert len(ask_id) == 4 and set(ask_id) <= set(asks.ID_ALPHABET)
    tried: list[str] = []

    def taken(ask_id: str) -> bool:
        tried.append(ask_id)
        return len(tried) < 5
    assert asks.new_id(taken) == tried[-1] and len(tried) == 5
    with pytest.raises(RuntimeError):
        asks.new_id(lambda _: True)


def test_normalise_id() -> None:
    assert asks.normalise_id("K7M2") == "k7m2"
    for bad in ("k7m", "k7m22", "k7m1", "k7mo", "k7ml", "k7mi", "k7m0", "k7m!", ""):
        assert asks.normalise_id(bad) is None
    assert set(asks.ID_ALPHABET).isdisjoint("01ilo")


# --- cards ---------------------------------------------------------------------------------------
def test_question_card() -> None:
    text, truncated = asks.question_card(row())
    lines = text.split("\n")
    assert lines[0] == "❓ Ask k7m2 · question · posted by controller (a local process; unverified)"
    assert lines[1] == "Which relay?" and BODY in text and not truncated
    assert lines[-1] == "Answer by replying or reacting to this message, or send !answer k7m2 <text>"


def test_merge_card() -> None:
    text, _ = asks.question_card(row(kind="merge", title="Merge plan 3 (wsd intake)"))
    lines = text.split("\n")
    assert lines[0] == "🔀 Ask k7m2 · merge request · posted by controller (a local process; unverified)"
    assert lines[1:4] == ["Merge plan 3 (wsd intake)", f"PR: {PR}", f"Head: {HEAD}"]
    assert lines[-1] == ("Merging is yours to do in GitHub; admind never merges. React 👍 or reply when it "
                         "is merged, or reply with what to change.")


def test_card_redacts_the_body() -> None:
    text, _ = asks.question_card(row(body=BODY + " " + TOKEN))
    assert TOKEN not in text and "<redacted GitHub token>" in text


def test_card_budget_is_counted_after_redaction() -> None:
    # 5,200 characters as posted, a few hundred once each 128-hex run is redacted: it fits.
    body = " ".join(["ab" * 64] * 40)
    assert len(body) > asks.CARD_CHARS
    text, truncated = asks.question_card(row(body=body))
    assert not truncated and "!details" not in text and text.count("<redacted hex key>") == 40


def test_card_truncates_by_lines_and_characters() -> None:
    many = "\n".join(f"line {i}" for i in range(100))
    text, truncated = asks.question_card(row(body=many))
    assert truncated and "line 37" in text and "line 38" not in text      # title, blank, 38 body lines
    assert "(40 lines shown of 102; reply !details for the rest)" in text
    lines = [f"line {i}" for i in range(asks.CARD_LINES - 2)]                # the card's 40 lines exactly
    assert not asks.question_card(row(body="\n".join(lines)))[1]
    assert asks.question_card(row(body="\n".join([*lines, "one more"])))[1]
    long = "y" * 5_000
    text, truncated = asks.question_card(row(body=long))
    assert truncated and max(map(len, text.split("\n"))) == asks.CARD_CHARS - len("Which relay?\n\n")
    assert text.endswith("Answer by replying or reacting to this message, or send !answer k7m2 <text>")
    _, truncated = asks.question_card(row(body="z" * (asks.CARD_CHARS - len("Which relay?\n\n"))))
    assert not truncated


def test_full_text_is_whole_and_redacted() -> None:
    body = "\n".join(f"line {i}" for i in range(100)) + " " + TOKEN
    text = asks.full_text(row(body=body))
    assert "line 99" in text and TOKEN not in text and "!details" not in text


def test_list_line() -> None:
    r = row(title="t" * 80)
    at = datetime.fromisoformat(r.created_at) + timedelta(hours=3, minutes=5)
    assert asks.list_line(r, at) == f"k7m2 question · 3h · {'t' * 60}"
    assert asks.list_line(r, at - timedelta(hours=3)).split(" · ")[1] == "5m"
    assert asks.list_line(r, at + timedelta(days=3)).split(" · ")[1] == "3d"


# --- commands ------------------------------------------------------------------------------------
def test_parse_asks_and_answer() -> None:
    assert commands.parse("!asks") == commands.Command("asks")
    cmd = commands.parse("!answer k7m2 line1\nline2  ")
    assert cmd == commands.Command("answer", arg="k7m2", rest="line1\nline2")
    assert commands.parse("!answer\tK7M2\n  spaced  out \n") == commands.Command("answer", arg="K7M2",
                                                                               rest="spaced  out")
    for bad in ("!answer", "!answer k7m2", "!answer k7m2   \n "):
        with pytest.raises(commands.CommandError, match="Usage: !answer"):
            commands.parse(bad)
    with pytest.raises(commands.CommandError, match=r"Usage: !asks \[bump\|repeat\]\."):
        commands.parse("!asks all")
    assert "!asks" in commands.HELP and "!answer <id> <text>" in commands.HELP


# --- store ---------------------------------------------------------------------------------------
def outbox(store: Store, key: str, status: str = "sent", mid: str | None = None) -> None:
    store.enqueue(key, "x", None)
    seq = int(store.db.execute("SELECT seq FROM outbox WHERE key = ?", (key,)).fetchone()[0])
    if status == "sent":
        store.mark_sent(seq, mid)
    elif status == "failed":
        store.mark_failed(seq)


def test_ask_for_message(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    mid = "a1" * 32
    outbox(store, "ask:k7m2:0", "sent", "01" * 32)
    outbox(store, "ask:k7m2:1", "pending")
    outbox(store, f"askd:k7m2:{mid}:0", "sent", "02" * 32)
    outbox(store, "asknote:k7m2:cancelled:0:0", "sent", "03" * 32)
    outbox(store, f"reply:{mid}:0", "sent", "04" * 32)
    outbox(store, f"ask:{mid}:0", "sent", "05" * 32)            # a reply() key with the tag `ask`
    outbox(store, "ask:zzzz:0", "sent", "06" * 32)              # no such ask
    outbox(store, "ask:k7m2:2", "failed")
    assert store.ask_for_message("01" * 32) == "k7m2"
    assert store.ask_for_message("02" * 32) == "k7m2"
    for other in ("03" * 32, "04" * 32, "05" * 32, "06" * 32, "07" * 32, None):
        assert store.ask_for_message(other) is None
    # pending and failed rows have no message ID; give one to each and they still don't count
    store.db.execute("UPDATE outbox SET message_id = ? WHERE key = 'ask:k7m2:1'", ("08" * 32,))
    store.db.execute("UPDATE outbox SET message_id = ? WHERE key = 'ask:k7m2:2'", ("09" * 32,))
    assert store.ask_for_message("08" * 32) is None and store.ask_for_message("09" * 32) is None


def test_card_and_details_delivered(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    assert not store.card_delivered("k7m2")                     # no rows at all
    outbox(store, "ask:k7m2:0", "sent", "01" * 32)
    assert not store.card_delivered("k7m2")                     # a part missing
    outbox(store, "ask:k7m2:1", "pending")
    assert not store.card_delivered("k7m2")                     # the last part pending
    store.db.execute("UPDATE outbox SET status = 'failed' WHERE key = 'ask:k7m2:1'")
    assert not store.card_delivered("k7m2")                     # ... or failed
    store.db.execute("UPDATE outbox SET status = 'sent' WHERE key = 'ask:k7m2:1'")
    assert store.card_delivered("k7m2") and store.first_card_message("k7m2") == "01" * 32
    store.insert_ask(row("p4xw", card_parts=0), None)
    assert not store.card_delivered("p4xw") and not store.card_delivered("zzzz")   # an empty set never counts

    m1, m2 = "a1" * 32, "a2" * 32
    assert not store.details_delivered("k7m2", "a")
    store.add_ask_details("k7m2", "a", m1, 2)
    outbox(store, f"askd:k7m2:{m1}:0", "sent", "11" * 32)
    outbox(store, f"askd:k7m2:{m1}:1", "pending")
    assert not store.details_delivered("k7m2", "a")
    store.add_ask_details("k7m2", "b", m2, 1)
    outbox(store, f"askd:k7m2:{m2}:0", "sent", "12" * 32)
    assert store.details_delivered("k7m2", "b") and not store.details_delivered("k7m2", "a")
    store.db.execute("UPDATE outbox SET status = 'sent' WHERE key = ?", (f"askd:k7m2:{m1}:1",))
    assert store.details_delivered("k7m2", "a")
    store.add_ask_details("k7m2", "a", "a3" * 32, 0)           # an empty request never counts
    store.add_ask_details("p4xw", "a", "a4" * 32, 0)
    assert not store.details_delivered("p4xw", "a")


def test_posted_since_answers_and_lists(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row("k7m2", created_at="2026-10-05T09:00:00+00:00"), 42)
    store.insert_ask(row("p4xw", created_at="2026-10-05T10:00:00+00:00", status="cancelled"), None)
    store.insert_ask(row("q9rt", created_at="2026-10-05T11:00:00+00:00", status="approved"), None)
    store.insert_ask(row("m3qp", created_at="2026-10-05T11:30:00+00:00", status="blocked"), None)
    assert store.ask_posted_since("2026-10-05T10:00:00+00:00") == 3
    assert store.ask_posted_since("2026-10-05T12:00:00+00:00") == 0
    assert [r.ask_id for r in store.asks_with_status(*asks.ACTIVE)] == ["k7m2"]
    assert [r.ask_id for r in store.recent_asks(2)] == ["m3qp", "q9rt"]
    assert store.answer_totals("k7m2") == (0, 0)
    store.add_answer("k7m2", "answer", "a", "b1" * 32, "first")
    store.add_answer("k7m2", "note", "b", "b2" * 32, "second one")
    assert store.answer_totals("k7m2") == (2, 15)
    assert [(a.kind, a.operator, a.text) for a in store.answers("k7m2")] == [
        ("answer", "a", "first"), ("note", "b", "second one")]
    store.set_ask("k7m2", "answered")
    ask = store.ask("k7m2")
    assert ask is not None and ask.status == "answered"
    store.insert_ask(row("b2c3", bead="btq-ab12c", status="superseded"), None)
    store.insert_ask(row("b4c5", bead="btq-ab12c", created_at="2026-10-05T12:00:00+00:00"), None)
    found = store.ask_for_bead("btq-ab12c", "open", "answered")
    assert found is not None and found.ask_id == "b4c5" and store.ask_for_bead("btq-zz", "open") is None


# --- JsonSocketServer on ask.sock ------------------------------------------------------------------
async def ask_server(tmp_path: Path, handler: Any, max_reply: int = asks.MAX_REPLY,
                     check: Any = asks.check) -> ctl.JsonSocketServer[
        asks.AskRequest, asks.AskReply]:
    server = ctl.JsonSocketServer(tmp_path / "s" / asks.ASK_SOCKET, handler,
                                  Audit(tmp_path / "s" / "audit.jsonl"),
                                  kind="ask", max_request=asks.MAX_REQUEST, max_reply=max_reply,
                                  request_type=asks.AskRequest, check=check, refused=asks.refused,
                                  failed=asks.failed, describe=asks.describe)
    await server.start()
    return server


async def raw(path: Path, data: bytes) -> asks.AskReply:
    reader, writer = await asyncio.open_unix_connection(str(path), limit=asks.MAX_REPLY + 2)
    writer.write(data)
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 10)
    writer.close()
    return msgspec.json.decode(line, type=asks.AskReply)


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_ask_socket_round_trip_limits_and_mode(tmp_path: Path) -> None:
    seen: list[tuple[asks.AskRequest, ctl.Peer]] = []

    async def handler(req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        seen.append((req, peer))
        return asks.AskReply("ok", str(len(req.body)) if isinstance(req, asks.AskPost) else "ok")

    def framing_only(req: asks.AskRequest) -> str | None:     # the frame limit, not the body limit
        return "--title" if isinstance(req, asks.AskPost) and not req.title else None

    async def body() -> None:
        server = await ask_server(tmp_path, handler, check=framing_only)
        path = server.path
        try:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            big = question(body="b" * 100 * 1024)
            reply = await ctl.request(path, big, reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
            assert reply.message == str(100 * 1024)
            # exactly the limit is read; one byte over is refused unseen
            base = len(msgspec.json.encode(question(body="")))
            fits = question(body="c" * (asks.MAX_REQUEST - base))
            assert len(msgspec.json.encode(fits)) == asks.MAX_REQUEST
            assert (await raw(path, msgspec.json.encode(fits) + b"\n")).result == "ok"
            over = question(body="c" * (asks.MAX_REQUEST - base + 1))
            assert (await raw(path, msgspec.json.encode(over) + b"\n")) == asks.refused("malformed request")
            for bad in (b'{"type": "format-disk"}\n', b"not json\n", b'{"type": "get"}\n',
                        b'{"type": "get", "ask_id": "k7m2", "extra": 1}\n',
                        b'{"type": "post", "kind": "question", "title": "t", "body": 5}\n'):
                assert (await raw(path, bad)) == asks.refused("malformed request")
            refused = await raw(path, msgspec.json.encode(question(title="")) + b"\n")
            assert refused.result == "refused" and "--title" in refused.message
        finally:
            await server.close()

    asyncio.run(body())
    assert len(seen) == 2
    assert seen[0][1].pid == (os.getpid() if sys.platform.startswith("linux") else seen[0][1].pid)
    kinds = {r["kind"] for r in records(tmp_path / "s" / "audit.jsonl")}
    assert kinds == {"ask"}


def test_ask_socket_failures(tmp_path: Path) -> None:
    async def raising(req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        raise RuntimeError("detail that must not reach the poster")

    async def huge(req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        return asks.AskReply("ok", "h" * 5_000)

    async def body() -> tuple[asks.AskReply, asks.AskReply]:
        server = await ask_server(tmp_path, raising)
        try:
            failed = await ctl.request(server.path, asks.AskList(), reply_type=asks.AskReply)
        finally:
            await server.close()
        server = await ask_server(tmp_path, huge, max_reply=4_096)
        try:
            too_large = await ctl.request(server.path, asks.AskList(), reply_type=asks.AskReply)
        finally:
            await server.close()
        return failed, too_large

    failed, too_large = asyncio.run(body())
    assert failed == asks.failed()
    assert failed == asks.AskReply("failed", "admind hit an internal error; see the audit log.")
    assert too_large == asks.failed()
    audit = records(tmp_path / "s" / "audit.jsonl")
    expected = {"kind": "ask", "action": "handler-failed", "op": "list", "error": "RuntimeError"}
    assert expected.items() <= audit[1].items()
    assert any(r.get("action") == "reply-too-large" and r["kind"] == "ask" for r in audit)
    assert "must not reach" not in (tmp_path / "s" / "audit.jsonl").read_text()


def test_client_reply_limit(tmp_path: Path) -> None:
    """The client reads with the limit it is given: a reply over it is unavailable, not a hang (R24)."""
    async def handler(req: asks.AskRequest, peer: ctl.Peer) -> asks.AskReply:
        return asks.AskReply("ok", "h" * 100_000)

    async def body() -> None:
        server = await ask_server(tmp_path, handler)
        try:
            ok = await ctl.request(server.path, asks.AskList(), reply_type=asks.AskReply,
                                   max_reply=asks.MAX_REPLY)
            assert len(ok.message) == 100_000
            with pytest.raises(ctl.CtlUnavailable):
                await ctl.request(server.path, asks.AskList(), reply_type=asks.AskReply)    # 64 KiB default
        finally:
            await server.close()

    asyncio.run(body())


def test_peer_pid() -> None:
    import socket
    a, b = socket.socketpair(socket.AF_UNIX)
    try:
        if sys.platform.startswith("linux"):
            assert platform.peer_pid(a) == os.getpid()
        assert platform.peer_pid(a, "plan9") is None
        assert platform.peer_pid(None) is None
    finally:
        a.close()
        b.close()
    assert platform.peer_pid(a) is None or not sys.platform.startswith("linux")    # a closed socket: no PID


# --- the fake approve-bead (used by Task 3) --------------------------------------------------------
def test_fake_approve_bead_modes(tmp_path: Path) -> None:
    import subprocess
    wrapper = approve_bead_wrapper(tmp_path)
    digest = "d" * 64
    bead_db(tmp_path, **{"btq-ab12c": {"digest": digest, "approvers": ["a"]},
                         "btq-busy": {"busy": True},
                         "btq-part": {"digest": digest, "approvers": ["a"], "decide": "partial"}})

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(wrapper), *args], capture_output=True, text=True, timeout=30, check=False)

    out = run("btq-ab12c", "--json")
    assert out.returncode == 0 and json.loads(out.stdout)["digest"] == digest
    busy = run("btq-busy", "--json")
    assert busy.returncode == 4 and json.loads(busy.stdout) == {"format": 1, "busy": True}
    assert len(busy.stderr.splitlines()) == 1
    refused = run("btq-ab12c", "--json", "--yes")          # --json is read only (Task 1)
    assert refused.returncode == 2 and len(refused.stderr.splitlines()) == 1
    assert run("btq-ab12c", "--as=a", "--yes", "--expect-digest=" + "0" * 64).returncode == 3
    assert run("btq-ab12c", "--as=a", "--yes", f"--expect-digest={digest}", "--via=marmot",
               "--via-ref=marmot:id:0123456789ab").returncode == 0
    after = json.loads(run("btq-ab12c", "--json").stdout)
    assert after["status"] == "closed" and after["approved_by"] == "a" and after["gate_valid"] is True
    assert after["via_ref"] == "marmot:id:0123456789ab"
    assert run("btq-part", "--as=a", "--yes", f"--expect-digest={digest}").returncode == 1
    part = json.loads(run("btq-part", "--json").stdout)
    assert part["status"] == "open" and part["decision"] == "approve"
    assert sum("--yes" in argv for argv in btq_log(tmp_path)) == 4


# --- integration ---------------------------------------------------------------------------------
def pasted(h: Harness) -> str:
    """Everything the fake claude was given: its transcripts (the argv log holds no prompts)."""
    return "".join(p.read_text() for p in h.log.parent.glob("*.jsonl"))


async def joined(h: Harness) -> None:
    await h.say("hello")                                   # the join signal (D5)
    await h.until(lambda: "echo: hello" in h.texts())


async def posted(h: Harness, req: asks.AskPost) -> str:
    reply = await h.daemon.on_ask(req, NONE)
    assert reply.result == "posted" and reply.ask is not None, reply
    return reply.ask.summary.ask_id


async def card_sent(h: Harness, ask_id: str, part: int = 0) -> str:
    await h.until(lambda: sent_mid(h, f"ask:{ask_id}:{part}") is not None)
    mid = sent_mid(h, f"ask:{ask_id}:{part}")
    assert mid is not None
    return mid


async def view(h: Harness, ask_id: str) -> asks.AskView:
    reply = await h.daemon.on_ask(asks.AskGet(ask_id), NONE)
    assert reply.ask is not None
    return reply.ask


def answered_texts(h: Harness, needle: str) -> bool:
    return any(needle in t for t in h.texts())


def audited(h: Harness, **fields: Any) -> bool:
    return any(fields.items() <= r.items() for r in records(h.settings.state_dir / "audit.jsonl"))


@needs_tmux
def test_question_answered_by_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        card = await card_sent(h, ask_id)
        assert any(t.startswith(f"❓ Ask {ask_id} · question · posted by controller") for t in h.texts())
        answer = await say_to(h, "use the first one", reply_to=card)
        await h.until(lambda: answered_texts(h, f"Answer recorded for ask {ask_id}."))
        reply = next(r for r in h.fake.sent if r["idempotency_key"] == f"ask:{answer}:0")
        assert reply["reply_to_message_id_hex"] == answer
        v = await view(h, ask_id)
        assert v.summary.status == "answered" and v.summary.delivered
        assert [(a.kind, a.operator, a.text) for a in v.answers] == [("answer", "op", "use the first one")]
        second = await say_to(h, "or the second", reply_to=card)
        await h.until(lambda: sent_mid(h, f"ask:{second}:0") is not None)
        assert answered_texts(h, f"Added to ask {ask_id}; it was already answered, and the poster sees both.")
        assert len((await view(h, ask_id)).answers) == 2
        assert "use the first one" not in pasted(h) and "echo: use the first one" not in h.texts()
        assert audited(h, kind="ask", action="answered", ask_id=ask_id)
        assert audited(h, kind="ask", action="posted", ask_id=ask_id, ask_kind="question",
                       poster="controller")
    run_with(tmp_path, scenario)


@needs_tmux
def test_question_answered_by_bang_answer(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        await card_sent(h, ask_id)
        await h.say(f"!answer {ask_id.upper()}   line one\n  line two\n")
        await h.until(lambda: answered_texts(h, f"Answer recorded for ask {ask_id}."))
        assert (await view(h, ask_id)).answers[0].text == "line one\n  line two"
        blank = await say_to(h, " \n\t ", reply_to=await card_sent(h, ask_id))
        await h.until(lambda: sent_mid(h, f"ask:{blank}:0") is not None)
        assert answered_texts(h, "Not recorded: the answer is empty.")
        assert len((await view(h, ask_id)).answers) == 1
        await h.say("!answer zzzz anything")
        await h.until(lambda: "No ask zzzz." in h.texts())
        assert "line one" not in pasted(h)
    run_with(tmp_path, scenario)


@needs_tmux
def test_reply_to_non_card_still_passes_through(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        await card_sent(h, ask_id)
        echo = next(r["_message_id"] for r in h.fake.sent if r["text"] == "echo: hello")
        await say_to(h, "follow up", reply_to=echo)
        await h.until(lambda: "echo: follow up" in h.texts())
        assert (await view(h, ask_id)).answers == []
    run_with(tmp_path, scenario)


@needs_tmux
def test_card_delivery_tracking(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("ask:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        ask_id = await posted(h, question(body=BODY + "\n" + "more context. " * 60))
        ask = h.store.ask(ask_id)
        assert ask is not None and ask.card_parts == 2
        card = await card_sent(h, ask_id)
        assert not h.store.card_delivered(ask_id) and not (await view(h, ask_id)).summary.delivered
        await say_to(h, "an answer to part one", reply_to=card)       # that row is sent, so it is a card
        await h.until(lambda: (r := h.store.ask(ask_id)) is not None and r.status == "answered")
        assert not h.store.card_delivered(ask_id)
        gate.set()
        await h.until(lambda: h.store.card_delivered(ask_id))
        await h.until(lambda: answered_texts(h, f"Answer recorded for ask {ask_id}."))
        assert (await view(h, ask_id)).summary.delivered
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 700})


@needs_tmux
def test_merge_requires_pr_and_head(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        path = h.settings.state_dir / asks.ASK_SOCKET
        for bad in (merge(pr_url=None), merge(head_sha=None), merge(pr_url="https://example.org/pull/1")):
            reply = await ctl.request(path, bad, reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
            assert reply.result == "refused"
        assert h.store.asks_with_status(*asks.ACTIVE, "cancelled") == []
        assert not any(r["idempotency_key"].startswith("ask:") for r in h.fake.sent)
    run_with(tmp_path, scenario)


@needs_tmux
def test_merge_card_shows_url_and_never_merges(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        path = h.settings.state_dir / asks.ASK_SOCKET
        reply = await ctl.request(path, merge(), reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
        assert reply.result == "posted" and reply.ask is not None
        ask_id = reply.ask.summary.ask_id
        card = await card_sent(h, ask_id)
        text = next(r["text"] for r in h.fake.sent if r["idempotency_key"] == f"ask:{ask_id}:0")
        assert text.startswith(f"🔀 Ask {ask_id} · merge request · posted by controller")
        assert f"PR: {PR}\nHead: {HEAD}\n" in text and "admind never merges" in text
        await say_to(h, "merged", reply_to=card)
        await h.until(lambda: answered_texts(h, f"Answer recorded for ask {ask_id}."))
        assert (await view(h, ask_id)).answers[0].text == "merged"
        assert audited(h, kind="ask", op="post", ask_kind="merge", pr=True)
    run_with(tmp_path, scenario)


@needs_tmux
def test_post_refused_while_latched(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        h.daemon.latch("test latch")
        assert await h.daemon.on_ask(question(), NONE) == asks.refused(ASK_LATCHED)
        assert (await h.daemon.on_ask(asks.AskGet(ask_id), NONE)).result == "ok"
        listed = await h.daemon.on_ask(asks.AskList(), NONE)
        assert listed.asks is not None and [s.ask_id for s in listed.asks] == [ask_id]
        assert (await h.daemon.on_ask(asks.AskCancel(ask_id), NONE)).result == "ok"
        assert len(h.store.asks_with_status("open", "cancelled")) == 1
    run_with(tmp_path, scenario)


@needs_tmux
def test_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    clock = [start]
    monkeypatch.setattr(asks, "now", lambda: clock[0])

    async def scenario(h: Harness) -> None:
        ids = [await posted(h, question(title=f"q{i}")) for i in range(asks.MAX_OPEN)]
        assert await h.daemon.on_ask(question(), NONE) == asks.refused(ASK_TOO_MANY)
        for ask_id in ids[:10]:
            assert (await h.daemon.on_ask(asks.AskCancel(ask_id), NONE)).result == "ok"
        clock[0] = start + timedelta(minutes=30)
        for i in range(10):
            await posted(h, question(title=f"r{i}"))                # the 30th in the hour is allowed
        h.store.set_ask(ids[10], "cancelled")
        assert await h.daemon.on_ask(question(), NONE) == asks.refused(ASK_TOO_OFTEN)
        clock[0] = start + timedelta(minutes=60, seconds=1)        # the first ten are now over an hour old
        await posted(h, question(title="after the hour"))
        h.store.set_ask(ids[11], "blocked")                        # blocked does not count as active (r2-4)
        clock[0] = start + timedelta(hours=3)
        await posted(h, question(title="in blocked's place"))
        assert len(h.store.asks_with_status(*asks.ACTIVE)) == asks.MAX_OPEN
        assert await h.daemon.on_ask(question(), NONE) == asks.refused(ASK_TOO_MANY)
    run_with(tmp_path, scenario)


@needs_tmux
def test_answer_limits(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        a = await posted(h, question(title="a"))
        card = await card_sent(h, a)
        too_long = await say_to(h, "x" * (asks.MAX_ANSWER + 1), reply_to=card)
        await h.until(lambda: sent_mid(h, f"ask:{too_long}:0") is not None)
        assert answered_texts(h, "Not recorded: an answer is at most 16,000 characters.")
        exact = await say_to(h, "x" * asks.MAX_ANSWER, reply_to=card)
        await h.until(lambda: sent_mid(h, f"ask:{exact}:0") is not None)
        assert h.store.answer_totals(a) == (1, asks.MAX_ANSWER)

        b = await posted(h, question(title="b"))
        card_b = await card_sent(h, b)
        for i in range(asks.MAX_ANSWERS - 1):
            h.store.add_answer(b, "answer", "op", f"b{i:063x}", "y")
        fiftieth = await say_to(h, "the fiftieth", reply_to=card_b)
        await h.until(lambda: sent_mid(h, f"ask:{fiftieth}:0") is not None)
        assert h.store.answer_totals(b)[0] == asks.MAX_ANSWERS
        over = await say_to(h, "the fifty-first", reply_to=card_b)
        await h.until(lambda: sent_mid(h, f"ask:{over}:0") is not None)
        assert answered_texts(h, f"Not recorded: ask {b} already has 50 answers.")
        assert h.store.answer_totals(b)[0] == asks.MAX_ANSWERS

        c = await posted(h, question(title="c"))
        card_c = await card_sent(h, c)
        for i, size in enumerate((16_000, 16_000, 16_000, 15_999)):
            h.store.add_answer(c, "answer", "op", f"c{i:063x}", "z" * size)
        last = await say_to(h, "y", reply_to=card_c)                # exactly 64,000 in total
        await h.until(lambda: sent_mid(h, f"ask:{last}:0") is not None)
        assert h.store.answer_totals(c) == (5, asks.MAX_ANSWER_TOTAL)
        past = await say_to(h, "y", reply_to=card_c)
        await h.until(lambda: sent_mid(h, f"ask:{past}:0") is not None)
        assert answered_texts(h, f"Not recorded: ask {c}'s answers would pass 64,000 characters.")
        assert h.store.answer_totals(c) == (5, asks.MAX_ANSWER_TOTAL)
        assert "x" * 100 not in pasted(h)
    run_with(tmp_path, scenario)


@needs_tmux
def test_details_on_card(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        body = BODY + "\n" + "\n".join(f"detail line {i}" for i in range(80)) + "\nTHE END " + TOKEN
        ask_id = await posted(h, question(body=body))
        card = await card_sent(h, ask_id)
        await card_sent(h, ask_id, 1)
        card_text = "".join(r["text"] for r in h.fake.sent
                            if r["idempotency_key"].startswith(f"ask:{ask_id}:"))
        assert "reply !details for the rest" in card_text and "THE END" not in card_text
        row_ = h.store.ask(ask_id)
        assert row_ is not None and row_.truncated
        mid = await say_to(h, "!details", reply_to=card)
        await h.until(lambda: sent_mid(h, f"askd:{ask_id}:{mid}:0") is not None)
        rows = h.store.db.execute("SELECT key, lane, reply_to, text FROM outbox WHERE key LIKE ? "
                                  "ORDER BY seq",
                                  (f"askd:{ask_id}:{mid}:%",)).fetchall()
        assert {r[1] for r in rows} == {2} and {r[2] for r in rows} == {mid}
        whole = "".join(r[3] for r in rows)
        assert "detail line 79" in whole and "THE END <redacted GitHub token>" in whole and TOKEN not in whole
        parts = h.store.db.execute("SELECT operator, parts FROM ask_details "
                                   "WHERE ask_id = ? AND message_id = ?",
                                   (ask_id, mid)).fetchone()
        assert tuple(parts) == ("op", len(rows))
        await h.until(lambda: h.store.details_delivered(ask_id, "op"))
        status = h.store.db.execute("SELECT status FROM inbound WHERE message_id = ?", (mid,)).fetchone()
        assert status[0] == "done"
        # a chunk of the details is a card too
        details_mid = sent_mid(h, f"askd:{ask_id}:{mid}:0")
        await say_to(h, "answered from the details", reply_to=details_mid)
        await h.until(lambda: answered_texts(h, f"Answer recorded for ask {ask_id}."))
        assert audited(h, kind="ask", action="details", ask_id=ask_id, parts=len(rows))
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 600})


@needs_tmux
def test_cancel(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        card = await card_sent(h, ask_id)
        assert await h.daemon.on_ask(asks.AskCancel(ask_id.upper()), NONE) == asks.AskReply(
            "ok", f"ask {ask_id} cancelled")
        await h.until(lambda: sent_mid(h, f"asknote:{ask_id}:cancelled:0:0") is not None)
        note = next(r for r in h.fake.sent if r["idempotency_key"] == f"asknote:{ask_id}:cancelled:0:0")
        assert note["reply_to_message_id_hex"] == card and "cancelled by its poster" in note["text"]
        assert (await view(h, ask_id)).summary.status == "cancelled"
        again = await h.daemon.on_ask(asks.AskCancel(ask_id), NONE)
        assert again.result == "refused" and "is cancelled" in again.message
        assert (await h.daemon.on_ask(asks.AskCancel("zzzz"), NONE)).result == "refused"
        await h.say(f"!answer {ask_id} too late")
        await h.until(lambda: answered_texts(h, f"Ask {ask_id} is already cancelled. Nothing recorded."))
        late = await say_to(h, "a reply, too late", reply_to=card)
        await h.until(lambda: sent_mid(h, f"ask:{late}:0") is not None)
        assert (await view(h, ask_id)).answers == []
        unsent = await posted(h, question(title="never sent"))       # latched first: no card to thread to
        h.daemon.latch("hold the outbox")
        assert (await h.daemon.on_ask(asks.AskCancel(unsent), NONE)).result == "ok"
        assert h.store.db.execute("SELECT reply_to FROM outbox WHERE key = ?",
                                  (f"asknote:{unsent}:cancelled:0:0",)).fetchone()[0] is None
    run_with(tmp_path, scenario)


@needs_tmux
def test_asks_command_lists_non_terminal(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        first = await h.say("!asks")
        await h.until(lambda: sent_mid(h, f"cmd:{first}:0") is not None)
        assert NO_ACTIVE_ASKS in h.texts()
        keep = await posted(h, question(title="keep me"))
        gone = await posted(h, question(title="cancel me"))
        await h.daemon.on_ask(asks.AskCancel(gone), NONE)
        second = await h.say("!asks")
        await h.until(lambda: sent_mid(h, f"cmd:{second}:0") is not None)
        listing = next(r["text"] for r in h.fake.sent if r["idempotency_key"] == f"cmd:{second}:0")
        assert listing.startswith(f"{keep} question · 0m · keep me") and gone not in listing
        assert "!asks" not in pasted(h)
    run_with(tmp_path, scenario)


@needs_tmux
def test_large_get_and_list(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        path = h.settings.state_dir / asks.ASK_SOCKET
        big = await posted(h, question(title="t" * 200))
        for i in range(4):
            h.store.add_answer(big, "answer", "op", f"{i:064x}", "é" * asks.MAX_ANSWER)   # 2 bytes each
        got = await ctl.request(path, asks.AskGet(big), reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
        assert got.ask is not None and sum(len(a.text) for a in got.ask.answers) == asks.MAX_ANSWER_TOTAL
        for i in range(19):
            h.store.insert_ask(row(f"a{asks.ID_ALPHABET[i]}22", title="t" * 200, body="b" * 16_000), None)
        for i in range(25):
            h.store.insert_ask(row(f"c{asks.ID_ALPHABET[i]}22", title="t" * 200, body="b" * 16_000,
                                   status="cancelled"), None)
        listed = await ctl.request(path, asks.AskList(), reply_type=asks.AskReply, max_reply=asks.MAX_REPLY)
        assert listed.asks is not None and len(listed.asks) == 40
        assert sum(s.status == "cancelled" for s in listed.asks) == 20
        assert len(msgspec.json.encode(listed)) < asks.MAX_REPLY
        assert len(msgspec.json.encode(got)) < asks.MAX_REPLY
    run_with(tmp_path, scenario)


@needs_tmux
def test_existing_details_unchanged(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say(LONG)
        sid = await summary_delivered(h)
        await posted(h, question())
        mid = await say_to(h, "!details", reply_to=sid)
        await h.until(lambda: delivered(h, mid))
        assert "".join(details_texts(h, mid)).endswith(f"echo: {LONG}")
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'askd:%'").fetchone()[0] == 0
    run_with(tmp_path, scenario, configure('echo "short summary"'))


@needs_tmux
def test_card_reply_needs_authorisation(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        await card_sent(h, ask_id)
        mid = h.store.db.execute("SELECT message_id FROM inbound ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        h.daemon.group_ok = False                                  # verification lost after the reply arrived
        await h.daemon.ask_reply(mid, ask_id, "an answer")
        h.daemon.group_ok = True
        assert (await view(h, ask_id)).answers == []
        assert audited(h, kind="drop", reason="no longer authorised", what="ask")
        status = h.store.db.execute("SELECT status FROM inbound WHERE message_id = ?", (mid,)).fetchone()
        assert status[0] == "dropped"
    run_with(tmp_path, scenario)


@needs_tmux
def test_posts_in_flight_are_bounded(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        async with h.daemon.ask_post_lock:                        # hold the post lock: posts queue on it
            first = asyncio.create_task(h.daemon.on_ask(question(title="one"), NONE))
            second = asyncio.create_task(h.daemon.on_ask(question(title="two"), NONE))
            await h.until(lambda: h.daemon.posts_in_flight == asks.MAX_IN_FLIGHT)
            third = await asyncio.wait_for(h.daemon.on_ask(question(title="three"), NONE), 10)
            assert third == asks.refused(ASK_BUSY)
            assert not first.done() and not second.done()
        replies = await asyncio.wait_for(asyncio.gather(first, second), 10)
        assert [r.result for r in replies] == ["posted", "posted"] and h.daemon.posts_in_flight == 0
        assert (await h.daemon.on_ask(question(title="four"), NONE)).result == "posted"
        assert await h.daemon.on_ask(asks.AskPost("approval", bead="btq-ab12c"), NONE) == asks.refused(
            ASK_NO_APPROVALS)
        assert len(h.store.asks_with_status(*asks.ACTIVE)) == 3
    run_with(tmp_path, scenario)


# --- the CLI -------------------------------------------------------------------------------------
def via_main(monkeypatch: pytest.MonkeyPatch, s: object) -> None:
    monkeypatch.setattr(cli.hconfig, "load", lambda: None)
    monkeypatch.setattr(cli, "resolve", lambda cfg, env: s)


def test_cli_without_a_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                              capsys: pytest.CaptureFixture[str]) -> None:
    s = make_settings(tmp_path)
    via_main(monkeypatch, s)
    assert cli.main(["ask", "get", "k7m2"]) == cli.EX_UNAVAILABLE
    assert not (s.state_dir / "admind.db").exists()             # a client: it opens no database
    body = tmp_path / "body.txt"
    body.write_text("short")
    assert cli.main(["ask", "post", "--kind", "question", "--title", "t", "--body-file", str(body)]) == 1
    assert "at least 80" in capsys.readouterr().err
    assert cli.main(["ask", "get", "k7m1"]) == 1                 # refused before any connection
    monkeypatch.setattr(cli, "WAIT_POLL", 0.01)
    assert cli.main(["ask", "wait", "k7m2", "--timeout", "0.05"]) == cli.EX_TIMEOUT
    assert cli.main(["ask", "post", "--kind", "question", "--title", "t", "--body-file",
                     str(tmp_path / "missing")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


@needs_tmux
def test_cli_post_get_list_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    out: dict[str, Any] = {}

    async def scenario(h: Harness) -> None:
        via_main(monkeypatch, h.settings)
        body = tmp_path / "body.txt"
        body.write_text(BODY)

        async def main(*argv: str) -> tuple[int, Any]:
            return await asyncio.to_thread(cli.main, list(argv)), capsys.readouterr()
        out["post"] = await main("ask", "post", "--kind", "question", "--title", "Which?", "--body-file",
                                 str(body), "--from", "controller")
        [ask] = h.store.asks_with_status("open")
        monkeypatch.setattr(sys, "stdin", io.StringIO(BODY))
        out["merge"] = await main("ask", "post", "--kind", "merge", "--title", "M", "--body-file", "-",
                                  "--pr", PR, "--head", HEAD, "--json")
        out["get"] = await main("ask", "get", ask.ask_id)
        out["list"] = await main("ask", "list", "--json")
        out["cancel"] = await main("ask", "cancel", ask.ask_id)
        out["again"] = await main("ask", "cancel", ask.ask_id)
        h.daemon.latch("test")
        out["latched"] = await main("ask", "post", "--kind", "question", "--title", "x", "--body-file",
                                    str(body))
        out["id"] = ask.ask_id
    run_with(tmp_path, scenario)
    ask_id = out["id"]
    assert out["post"][0] == 0 and out["post"][1].out == f"ask {ask_id} posted\n"
    assert out["merge"][0] == 0 and json.loads(out["merge"][1].out)["result"] == "posted"
    assert out["get"][0] == 0 and out["get"][1].out.startswith(f"ask {ask_id} · question · open")
    assert out["list"][0] == 0 and len(json.loads(out["list"][1].out)["asks"]) == 2
    assert out["cancel"] == (0, out["cancel"][1]) and out["cancel"][1].out == f"ask {ask_id} cancelled\n"
    assert out["again"][0] == 1 and "only an open or answered ask" in out["again"][1].err
    assert out["latched"][0] == 1 and "latched" in out["latched"][1].err


@needs_tmux
def test_cli_wait_survives_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    result: list[int] = []
    unavailable = threading.Event()
    polled_again = threading.Event()
    real = cli._ask_request

    def observed(s: Any, req: asks.AskRequest, timeout: float) -> asks.AskReply:
        try:
            reply = real(s, req, timeout)
        except ctl.CtlUnavailable:
            unavailable.set()
            raise
        if unavailable.is_set():
            polled_again.set()
        return reply

    async def scenario(h: Harness) -> None:
        via_main(monkeypatch, h.settings)
        monkeypatch.setattr(cli, "WAIT_POLL", 0.02)
        monkeypatch.setattr(cli, "_ask_request", observed)
        await joined(h)
        ask_id = await posted(h, question())
        server = h.daemon.ask_server
        assert server is not None
        await server.close()                                    # as if admind were restarting
        thread = threading.Thread(target=lambda: result.append(
            cli.main(["ask", "wait", ask_id, "--timeout", "30"])))
        thread.start()
        try:
            assert await asyncio.to_thread(unavailable.wait, 15)
            await server.start()
            assert await asyncio.to_thread(polled_again.wait, 15)    # it reached the daemon again
            assert result == []                                        # ... and is still waiting
            await h.say(f"!answer {ask_id} the answer")
        finally:
            await asyncio.to_thread(thread.join, 20)
        assert not thread.is_alive()
    run_with(tmp_path, scenario)
    assert result == [0]
    assert "the answer" in capsys.readouterr().out
