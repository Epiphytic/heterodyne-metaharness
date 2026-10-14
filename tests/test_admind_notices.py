"""cxmkf §2.1: FYI notices (K1-K10). Pure checks of `notices.admit` and the store's counts, then the daemon
with the fake claude and the fake wn-agent.

A notice is never an ask: no `asks` row, no ID on the card, no answer path. Nothing here runs the real claude
or wn-agent, touches ~/.claude, bd or the network. IDs and SHAs are built at runtime, never 64-hex literals.
"""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from admind_asks_fixture import sent_mid
from admind_waits import wait_until
from test_admind_approvals import APPROVAL, go, queued, send, settled
from test_admind_asks import NONE, PR, TOKEN, audited, joined, posted, question, records
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_r13_lanes import backoff_gates
from test_admind_reactions import outbox_count, reacted

from heterodyne.admind import asks, chunk, notices
from heterodyne.admind.daemon import ASK_TOO_MANY
from heterodyne.admind.links import MAX_LINKS, MAX_URL, LinkSpec
from heterodyne.admind.notices import NoticePost
from heterodyne.admind.store import Store

SHA = "0123456789abcdef" * 2 + "01234567"
BLOB = f"https://github.com/owner/repo/blob/{SHA}/"


def url(length: int) -> str:
    """A pinned blob URL of exactly `length` characters."""
    return BLOB + "z" * (length - len(BLOB))      # not "a": a 64-hex run is redacted


def admitted(req: NoticePost, chunk_chars: int = 4000) -> notices.Admitted:
    out = notices.admit(req, chunk_chars)
    assert isinstance(out, notices.Admitted), out
    return out


# --- K3, K4, K8: the card and its bounds (pure) ----------------------------------------------------
def test_card_text() -> None:
    req = NoticePost("Deploy done", "line one\nline two\n", [LinkSpec("PR", PR)], poster="ci")
    out = admitted(req)
    assert out.chunks == ["FYI: no reply needed\n"
                          "ℹ️ Notice · posted by ci (a local process; unverified)\n"
                          "Deploy done\n\nline one\nline two\nLinks:\n"
                          f"PR: {PR}"]
    assert out.text == out.chunks[0]
    assert "!answer" not in out.text and "React" not in out.text and "Ask " not in out.text


def test_card_without_body_or_links() -> None:
    assert admitted(NoticePost("Just a title", " \n")).chunks == [
        f"{notices.LABEL}\n{notices.head('local')}\nJust a title"]


def test_the_maximum_notice_is_too_long() -> None:
    links = [LinkSpec(f"link {i}", url(MAX_URL)) for i in range(MAX_LINKS)]
    req = NoticePost("t", "w" * notices.MAX_BODY_CHARS, links)
    assert notices.admit(req, 4000) == "notice-too-long"


def test_parts_at_200() -> None:
    """At chunk_chars 200 each 150-character line needs its own chunk after the framing: 3 lines are 4
    parts, 4 lines are 5."""
    def body(n: int) -> str:
        return "\n".join(chr(ord("p") + i) * 150 for i in range(n))
    out = admitted(NoticePost("t", body(3)), 200)
    assert len(out.chunks) == notices.NOTICE_MAX_PARTS
    assert all(len(c) <= 200 for c in out.chunks)
    assert [c.split("\n")[0] for c in out.chunks[1:]] == [chunk.marker(i, 4).rstrip("\n") for i in (2, 3, 4)]
    assert out.chunks[0].startswith(f"{notices.LABEL}\n{notices.head('local')}\n")
    assert notices.admit(NoticePost("t", body(4)), 200) == "too-many-parts"


@pytest.mark.parametrize(("req", "why"), [
    (NoticePost(""), "title"),
    (NoticePost(" "), "title"),
    (NoticePost("a\nb"), "title"),
    (NoticePost("t" * (notices.MAX_TITLE + 1)), "title"),
    (NoticePost("t", "b" * (notices.MAX_BODY_CHARS + 1)), "body-too-long"),
    (NoticePost("t", "\n".join(["b"] * (notices.MAX_BODY_LINES + 1))), "body-too-long"),
    (NoticePost("t", links=[LinkSpec("l", url(MAX_URL + 1))]), "link-too-long"),
    (NoticePost("t", links=[LinkSpec("l", PR)] * (MAX_LINKS + 1)), "too-many-links"),
    (NoticePost("t", links=[LinkSpec("", PR)]), "link-label"),
    (NoticePost("t", links=[LinkSpec("a\nb", PR)]), "link-label"),
    (NoticePost("t", links=[LinkSpec("x" * 41, PR)]), "link-label"),
    (NoticePost("t", links=[LinkSpec("l", "https://github.com/owner/repo/tree/main/x")]), "link-format"),
    (NoticePost("t", links=[LinkSpec("l", BLOB + TOKEN)]), "link-redacted"),
])
def test_refusals(req: NoticePost, why: str) -> None:
    assert notices.admit(req, 4000) == why
    assert why in notices.WHY and why in notices.MESSAGES


def test_a_link_line_over_usable_is_refused() -> None:
    line = f"l: {url(chunk.usable(200) + 1 - 3)}"
    assert len(line) == chunk.usable(200) + 1
    assert notices.admit(NoticePost("t", links=[LinkSpec("l", line[3:])]), 200) == "link-line-too-long"
    fits = notices.admit(NoticePost("t", links=[LinkSpec("l", line[3:-1])]), 200)
    assert isinstance(fits, notices.Admitted)


def test_a_secret_in_the_body_is_redacted_before_it_is_measured() -> None:
    out = admitted(NoticePost("t", f"token {TOKEN} here"))
    assert TOKEN not in out.text and "<redacted" in out.text and "".join(out.chunks) == out.text


def test_every_refusal_code_has_a_message() -> None:
    assert set(notices.WHY) - {"internal"} <= set(notices.MESSAGES)


def test_describe_holds_no_text() -> None:
    req = NoticePost("SENTINEL", "SENTINEL", [LinkSpec("SENTINEL", PR)], "p")
    assert "SENTINEL" not in json.dumps(notices.describe(req))


# --- K5, K7: counts and pruning (store) ------------------------------------------------------------
def pending(store: Store, *keys: str) -> None:
    for key in keys:
        store.enqueue(key, "x", None, lane=2)


def test_undelivered_counts_notices_from_the_outbox(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    try:
        pending(store, "notice:abcdef:0", "notice:abcdef:1", "notice:ghjkmn:0", "notice:ABCDEF:0",
                "notice:abc:0", "noticex:abcdef:0", "ask:k7m2:0")
        sent = "notice:pqrstu:0"
        pending(store, sent)
        seq = int(store.db.execute("SELECT seq FROM outbox WHERE key = ?", (sent,)).fetchone()[0])
        store.mark_sent(seq, None)
        assert store.undelivered_notices() == 2
        store.db.execute("DELETE FROM notices")          # rows deleted by hand: the outbox still counts
        assert store.undelivered_notices() == 2
    finally:
        store.close()


def test_prune_keeps_notices_still_being_delivered(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    try:
        old, new = "2026-10-01T00:00:00+00:00", "2026-10-13T00:00:00+00:00"
        for notice_id, at in (("abcdef", old), ("ghjkmn", old), ("pqrstu", new)):
            store.insert_notice(notice_id, "local", None, 1, 0, 0, 1, at)
        pending(store, "notice:abcdef:0", "notice:pqrstu:0")
        before = "2026-10-07T00:00:00+00:00"
        assert store.prune_notices(before) == 1                 # ghjkmn: old and delivered
        assert store.notice_exists("abcdef") and not store.notice_exists("ghjkmn")
        assert store.notices_since("2026-10-12T00:00:00+00:00") == 1
        store.db.execute("UPDATE outbox SET status = 'failed' WHERE key = 'notice:abcdef:0'")
        assert store.prune_notices(before) == 1 and not store.notice_exists("abcdef")
        assert store.notice_exists("pqrstu")
    finally:
        store.close()


# --- the daemon ------------------------------------------------------------------------------------
async def notice(h: Harness, req: NoticePost | None = None) -> str:
    reply = await h.daemon.on_ask(req or NoticePost("Deploy done", "all green"), NONE)
    assert reply.result == "posted" and reply.ask is None and reply.asks is None, reply
    m = re.fullmatch(r"posted ([2-9a-hjkmnp-z]{6})", reply.message)
    assert m is not None, reply.message
    return m.group(1)


def refused(why: str) -> asks.AskReply:
    return asks.refused(notices.MESSAGES[why])


def notice_rows(h: Harness) -> list[tuple[str, int, str]]:
    return [(str(r[0]), int(r[1]), str(r[2])) for r in h.store.db.execute(
        "SELECT key, lane, status FROM outbox WHERE key LIKE 'notice:%' ORDER BY seq")]


def delivered(h: Harness) -> bool:
    return h.store.undelivered_notices() == 0


@needs_tmux
def test_a_notice_is_never_an_ask(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question())
        notice_id = await notice(h)
        assert h.store.db.execute("SELECT COUNT(*) FROM asks").fetchone()[0] == 1
        listed = await h.daemon.on_ask(asks.AskList(), NONE)
        assert listed.asks is not None and [s.ask_id for s in listed.asks] == [ask_id]
        assert (await h.daemon.on_ask(asks.AskGet(notice_id), NONE)).result == "refused"
        await h.until(lambda: delivered(h))
        assert [lane for _, lane, _ in notice_rows(h)] == [2]
        text = next(t for t in h.texts() if t.startswith(notices.LABEL))
        assert notice_id not in text and "!answer" not in text
        mid = await send(h, "!asks", None)
        await settled(h, mid)
        listing = queued(h, mid, "cmd")
        assert ask_id in listing and notice_id not in listing
        assert audited(h, kind="notice", action="posted", notice_id=notice_id, parts=1, poster="local")
    run_with(tmp_path, scenario)


@needs_tmux
def test_notices_do_not_count_toward_max_open(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ids = [await posted(h, question(title=f"q{i}")) for i in range(asks.MAX_OPEN)]
        await notice(h)
        assert await h.daemon.on_ask(APPROVAL, NONE) == asks.refused(ASK_TOO_MANY)
        assert (await h.daemon.on_ask(asks.AskCancel(ids[0]), NONE)).result == "ok"
        for i in range(4):
            await notice(h, NoticePost(f"n{i}"))
        assert (await h.daemon.on_ask(APPROVAL, NONE)).result == "posted"
    go(tmp_path, scenario)


@needs_tmux
def test_a_lane_one_card_goes_before_pending_notice_chunks(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        notice_id = await notice(h, NoticePost("t", "\n".join(c * 150 for c in "pqr")))     # 4 parts, held
        ask_id = await posted(h, question())
        await joined(h)
        await h.until(lambda: delivered(h) and h.store.card_delivered(ask_id))
        keys = [r["idempotency_key"] for r in h.fake.sent]
        assert keys.index(f"ask:{ask_id}:0") < keys.index(f"notice:{notice_id}:0")
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_a_notice_in_backoff_does_not_delay_a_card(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        gates = await backoff_gates(h)
        h.fake.fail_sends = 1
        notice_id = await notice(h)
        await wait_until(lambda: len(gates) == 1)
        ask_id = await posted(h, question())
        await wait_until(lambda: sent_mid(h, f"ask:{ask_id}:0") is not None)
        assert sent_mid(h, f"notice:{notice_id}:0") is None
        gates[0].set()
        await wait_until(lambda: sent_mid(h, f"notice:{notice_id}:0") is not None)
    run_with(tmp_path, scenario)


def replies_to(h: Harness, mid: str) -> str:
    """Everything admind queued in answer to inbound `mid`, whatever its tag."""
    return "".join(r[0] for r in h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq",
                                                    (f"%:{mid}:%",)))


@needs_tmux
def test_reactions_and_replies_to_a_notice(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        notice_id = await notice(h)
        await h.until(lambda: sent_mid(h, f"notice:{notice_id}:0") is not None)
        card = sent_mid(h, f"notice:{notice_id}:0")
        unseen = "f8" * 32                  # a message admind never posted: today's non-card handling
        assert card is not None
        mid, text = await reacted(h, "👍", card)
        assert text == "" and outbox_count(h, f"%{mid}%") == 0 and h.store.inbound_status(mid) == "done"
        assert audited(h, kind="event", action="ignored", what="reaction")
        await send(h, "thanks for the heads-up", card)
        await h.until(lambda: "echo: thanks for the heads-up" in h.texts())
        for command in ("!approve", "!details"):
            on_notice, on_unseen = await send(h, command, card), await send(h, command, unseen)
            await settled(h, on_notice)
            await settled(h, on_unseen)
            assert replies_to(h, on_notice) == replies_to(h, on_unseen) != "", command
        assert h.store.db.execute("SELECT COUNT(*) FROM ask_attempts").fetchone()[0] == 0
    run_with(tmp_path, scenario)


@needs_tmux
def test_refusals_queue_nothing(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        for req, why in [(NoticePost("t", "w" * notices.MAX_BODY_CHARS,
                                     [LinkSpec(f"l{i}", url(MAX_URL)) for i in range(MAX_LINKS)]),
                          "notice-too-long"),
                         (NoticePost("t", "\n".join(c * 150 for c in "pqrs")), "too-many-parts"),
                         (NoticePost("t", links=[LinkSpec("l", url(MAX_URL + 1))]), "link-too-long"),
                         (NoticePost("t", links=[LinkSpec("l", url(190))]), "link-line-too-long"),
                         (NoticePost("t", links=[LinkSpec("l", PR)] * (MAX_LINKS + 1)), "too-many-links"),
                         (NoticePost("t", links=[LinkSpec(" ", PR)]), "link-label")]:
            assert await h.daemon.on_ask(req, NONE) == refused(why), why
            assert audited(h, kind="notice", action="refused", why=why)
        assert notice_rows(h) == [] and h.store.db.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0
        await notice(h, NoticePost("t", "\n".join(c * 150 for c in "pqr")))         # 4 parts: accepted
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_hourly_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    clock = [start]
    monkeypatch.setattr(asks, "now", lambda: clock[0])

    async def scenario(h: Harness) -> None:
        await joined(h)
        for i in range(notices.NOTICE_PER_HOUR):
            clock[0] = start + timedelta(minutes=i)
            await notice(h, NoticePost(f"n{i}"))
            await h.until(lambda: delivered(h))
        assert await h.daemon.on_ask(NoticePost("13th"), NONE) == refused("rate-hour")
        clock[0] = start + timedelta(minutes=60, seconds=1)         # the first is now over an hour old
        await notice(h, NoticePost("after the hour"))
        assert await h.daemon.on_ask(NoticePost("again"), NONE) == refused("rate-hour")
    run_with(tmp_path, scenario)


@needs_tmux
def test_undelivered_limit_and_delivery_after_verification(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        assert not h.daemon.may_post()                               # nobody seen yet: nothing goes out
        for i in range(notices.NOTICE_UNDELIVERED):
            await notice(h, NoticePost(f"n{i}"))
        rows = notice_rows(h)
        assert await h.daemon.on_ask(NoticePost("11th"), NONE) == refused("undelivered")
        assert notice_rows(h) == rows
        await joined(h)
        await h.until(lambda: delivered(h))
        assert sum(t.startswith(notices.LABEL) for t in h.texts()) == notices.NOTICE_UNDELIVERED
        await notice(h, NoticePost("now there is room"))
    run_with(tmp_path, scenario)


@needs_tmux
def test_refused_while_latched(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        h.daemon.latch("test latch")
        assert await h.daemon.on_ask(NoticePost("t"), NONE) == refused("latched")
        assert notice_rows(h) == [] and audited(h, kind="notice", action="refused", why="latched")
    run_with(tmp_path, scenario)


@needs_tmux
def test_accepted_while_the_group_is_unverified(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        h.fake.fail_group_info = True
        await h.until(lambda: not h.daemon.group_ok)
        notice_id = await notice(h)
        assert notice_rows(h) == [(f"notice:{notice_id}:0", 2, "pending")]
        h.fake.fail_group_info = False
        await h.until(lambda: delivered(h))
    run_with(tmp_path, scenario, settings_overrides={"group_check_seconds": 0.2})


async def concurrently(h: Harness, n: int = 20) -> list[asks.AskReply]:
    return list(await asyncio.gather(*(h.daemon.on_ask(NoticePost(f"c{i}"), NONE) for i in range(n))))


@needs_tmux
def test_concurrent_posts_against_the_queue_limit(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        for i in range(notices.NOTICE_UNDELIVERED - 1):
            await notice(h, NoticePost(f"n{i}"))
        replies = await concurrently(h)
        assert sum(r.result == "posted" for r in replies) == 1
        assert all(r == refused("undelivered") for r in replies if r.result != "posted")
        assert len(notice_rows(h)) == notices.NOTICE_UNDELIVERED
    run_with(tmp_path, scenario)


@needs_tmux
def test_concurrent_posts_against_the_hourly_limit(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        at = asks.stamp(asks.now())
        for notice_id in ("abcdef", "ghjkmn", "pqrstu", "vwxyz2", "345678", "9abcde", "fghjkm", "npqrst",
                          "uvwxyz"):
            h.store.insert_notice(notice_id, "local", None, 1, 0, 0, 1, at)        # delivered: no rows
        replies = await concurrently(h)
        ids = {r.message.removeprefix("posted ") for r in replies if r.result == "posted"}
        assert len(ids) == 3
        assert all(r == refused("rate-hour") for r in replies if r.result != "posted")
        assert {k.split(":")[1] for k, _, _ in notice_rows(h)} == ids
    run_with(tmp_path, scenario)


@needs_tmux
def test_redaction(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        notice_id = await notice(h, NoticePost("t", f"the token is {TOKEN}"))
        key = f"notice:{notice_id}:0"
        text = h.store.db.execute("SELECT text FROM outbox WHERE key = ?", (key,)).fetchone()[0]
        assert TOKEN not in text and "<redacted" in text
        assert await h.daemon.on_ask(NoticePost("t", links=[LinkSpec("l", BLOB + TOKEN)]), NONE) == \
            refused("link-redacted")
    run_with(tmp_path, scenario)


SENTINELS = ("zqxtitlesentinel", "zqxbodysentinel", "zqxlabelsentinel", "zqxurlsentinel")


@needs_tmux
def test_the_audit_never_holds_notice_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    title, body, label, path = SENTINELS
    good = [LinkSpec(label, BLOB + path)]

    async def scenario(h: Harness) -> None:
        await notice(h, NoticePost(title, body, good))
        for req in [NoticePost(title + "\n", body, good),
                    NoticePost(title, body * 1000, good),
                    NoticePost(title, body, [LinkSpec(label, f"https://example.com/{path}")]),
                    NoticePost(title, body, [LinkSpec(label, url(MAX_URL + 1) + path)]),
                    NoticePost(title, body, [LinkSpec(label + "\n", BLOB + path)]),
                    NoticePost(title, body, good * (MAX_LINKS + 1)),
                    NoticePost(title, body, [LinkSpec(label, BLOB + TOKEN + path)]),
                    NoticePost(title, "\n".join([body * 9] * 20), good),
                    NoticePost(title, body, [LinkSpec(label, url(190) + path)]),
                    NoticePost(title, body * 200, [LinkSpec(f"{label}{i}", url(MAX_URL)) for i in range(8)])]:
            assert (await h.daemon.on_ask(req, NONE)).result == "refused", req
        for _ in range(notices.NOTICE_UNDELIVERED - 1):
            await notice(h, NoticePost(title, body, good))
        assert (await h.daemon.on_ask(NoticePost(title, body, good), NONE)) == refused("undelivered")
        h.store.db.execute("DELETE FROM outbox WHERE key LIKE 'notice:%'")
        for _ in range(notices.NOTICE_PER_HOUR - notices.NOTICE_UNDELIVERED):
            await notice(h, NoticePost(title, body, good))
        assert (await h.daemon.on_ask(NoticePost(title, body, good), NONE)) == refused("rate-hour")

        def boom(req: NoticePost, chunk_chars: int) -> notices.Admitted | str:
            raise RuntimeError(title)
        monkeypatch.setattr(notices, "admit", boom)
        assert await h.daemon.on_ask(NoticePost(title, body, good), NONE) == asks.failed()
        assert audited(h, kind="notice", action="refused", why="internal", error="RuntimeError")
        monkeypatch.undo()
        h.daemon.latch("test latch")
        assert (await h.daemon.on_ask(NoticePost(title, body, good), NONE)) == refused("latched")
        raw = (h.settings.state_dir / "audit.jsonl").read_text()
        assert not [s for s in SENTINELS if s in raw]
        ours = [r for r in records(h.settings.state_dir / "audit.jsonl") if r.get("kind") == "notice"]
        whys = {r["why"] for r in ours if r["action"] == "refused"}
        assert whys <= set(notices.WHY)
        assert {"title", "body-too-long", "link-format", "link-too-long", "link-label", "too-many-links",
                "link-redacted", "link-line-too-long", "too-many-parts", "notice-too-long", "undelivered",
                "rate-hour", "internal", "latched"} <= whys
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


def stamp_days_ago(days: int) -> str:
    return asks.stamp(asks.now() - timedelta(days=days))


@needs_tmux
def test_pruning_waits_for_delivery(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ids = [await notice(h, NoticePost(f"n{i}")) for i in range(notices.NOTICE_UNDELIVERED)]
        h.store.db.execute("UPDATE notices SET created_at = ?", (stamp_days_ago(8),))
        h.daemon.recover()                                           # the startup housekeeping pass
        assert all(h.store.notice_exists(i) for i in ids)
        assert await h.daemon.on_ask(NoticePost("11th"), NONE) == refused("undelivered")   # prunes too
        assert all(h.store.notice_exists(i) for i in ids)
        await joined(h)
        await h.until(lambda: delivered(h))
        last = await notice(h)
        assert not any(h.store.notice_exists(i) for i in ids) and h.store.notice_exists(last)
    run_with(tmp_path, scenario)


def test_fixture_ids_are_notice_ids() -> None:
    """The hand-made IDs above are in the notice alphabet, so the store's regex counts them."""
    for notice_id in ("abcdef", "ghjkmn", "pqrstu", "vwxyz2", "345678", "9abcde", "fghjkm", "npqrst",
                      "uvwxyz"):
        assert notices.NOTICE_KEY.fullmatch(f"notice:{notice_id}:0"), notice_id
