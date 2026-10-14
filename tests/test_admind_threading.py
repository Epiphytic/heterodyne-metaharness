"""cxmkf §2.1: continuation parts thread under their chunk 0 (K22, K23). `Store.resolve_thread` on its own,
then the daemon with the fake wn-agent, which records each send's `reply_to`.

Nothing here runs the real claude or wn-agent, touches ~/.claude, bd or the network. IDs are built at
runtime, never 64-hex literals.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from admind_asks_fixture import sent_mid
from test_admind_approvals import BEAD, LINES, LONG, bead, card, go, send, settled
from test_admind_asks import NONE, audited, joined, posted, question
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_r13_lanes import join
from test_admind_reactions import reacted

from heterodyne.admind import chunk, notices
from heterodyne.admind.daemon import MAX_SEND_ATTEMPTS
from heterodyne.admind.notices import NoticePost
from heterodyne.admind.store import Store
from heterodyne.marmot.control import FinalSent

PARTS = ["part one", chunk.marker(2, 3) + "part two", chunk.marker(3, 3) + "part three"]
# A question body that makes a three-part card at chunk_chars 200 (head, body, footer).
BODY3 = "p" * 150


# --- resolve_thread (store) ------------------------------------------------------------------------
def seq_of(store: Store, key: str) -> int:
    return int(store.db.execute("SELECT seq FROM outbox WHERE key = ?", (key,)).fetchone()[0])


def stored(store: Store, key: str) -> tuple[Any, Any]:
    return tuple(store.db.execute("SELECT reply_to, thread_key FROM outbox WHERE key = ?", (key,)).fetchone())


def parent(store: Store, status: str, mid: str | None = None) -> None:
    store.enqueue("t:0", "zero", None)
    if status == "sent":
        store.mark_sent(seq_of(store, "t:0"), mid)
    elif status == "failed":
        store.mark_failed(seq_of(store, "t:0"))
    elif status == "missing":
        store.db.execute("DELETE FROM outbox WHERE key = 't:0'")


def test_resolve_thread_for_every_parent_state(tmp_path: Path) -> None:
    mid = "c" * 63 + "1"
    for status, given, want in [("sent", mid, (mid, None)), ("sent", None, (None, "no-message-id")),
                                ("failed", None, (None, "parent-failed")),
                                ("pending", None, (None, "parent-pending")),
                                ("missing", None, (None, "parent-missing"))]:
        store = Store(tmp_path / f"{status}-{given is None}.db")
        try:
            parent(store, status, given)
            store.enqueue("t:1", "one", None, thread_key="t:0")
            assert store.next_pending() is not None
            assert store.resolve_thread(seq_of(store, "t:1")) == (*want, "t:0"), status
            assert stored(store, "t:1") == (want[0], None)
            row = next(r for r in store.pending() if r.key == "t:1")
            assert row.thread_key is None and row.reply_to == want[0]
        finally:
            store.close()


def test_a_resolution_survives_a_restart(tmp_path: Path) -> None:
    """Resolved and committed before the send: a crash then leaves the target stored, the key cleared."""
    mid = "d" * 63 + "2"
    store = Store(tmp_path / "admind.db")
    parent(store, "sent", mid)
    store.enqueue("t:1", "one", None, thread_key="t:0")
    store.resolve_thread(seq_of(store, "t:1"))
    store.close()
    again = Store(tmp_path / "admind.db")
    try:
        row = again.next_pending()
        assert row is not None and row.key == "t:1" and row.reply_to == mid and row.thread_key is None
    finally:
        again.close()


# --- the daemon ------------------------------------------------------------------------------------
def sends(h: Harness, key: str) -> list[dict[str, Any]]:
    """Every send_final request for `key`, failed attempts included."""
    return [r for r in h.fake.requests if r.get("type") == "send_final" and r.get("idempotency_key") == key]


def reply_to(h: Harness, key: str) -> str | None:
    return next(r["reply_to_message_id_hex"] for r in h.fake.sent if r["idempotency_key"] == key)


async def fast_backoff(h: Harness) -> None:
    """Retries without waiting: a backoff (2 s or more) returns at once."""
    real = h.daemon._sleep          # noqa: SLF001 - the test seam for time

    async def sleep(delay: float) -> None:
        await real(0 if delay >= 2 else delay)
    h.daemon._sleep = sleep         # noqa: SLF001
    await join(h)


def fallback(h: Harness, key: str, why: str, parent_key: str = "t:0") -> bool:
    return audited(h, kind="thread_fallback", key=key, parent=parent_key, why=why)


async def drained(h: Harness, *keys: str) -> None:
    await h.until(lambda: all(sent_mid(h, k) is not None for k in keys))


@needs_tmux
def test_a_three_part_card_threads_under_chunk_zero(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question(body=BODY3))
        row = h.store.ask(ask_id)
        assert row is not None and row.card_parts == 3
        keys = [f"ask:{ask_id}:{i}" for i in range(3)]
        await drained(h, *keys)
        first = sent_mid(h, keys[0])
        assert [reply_to(h, k) for k in keys] == [None, first, first]
        texts = {r["idempotency_key"]: r["text"] for r in h.fake.sent}
        assert texts[keys[1]].startswith(chunk.marker(2, 3)) and texts[keys[2]].startswith(chunk.marker(3, 3))
        assert all(len(texts[k]) <= 200 for k in keys)
        assert not audited(h, kind="thread_fallback")
        assert h.store.card_delivered(ask_id)
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


def without_message_id(h: Harness, key: str) -> None:
    """The send of `key` returns no message ID (the fake always returns one)."""
    real = h.daemon.client.send_final

    async def send_final(account: str, group: str, text: str, reply_to: str | None, k: str) -> FinalSent:
        sent = await real(account, group, text, reply_to, k)
        return FinalSent([]) if k == key else sent
    h.daemon.client.send_final = send_final       # type: ignore[method-assign]


def sent_unidentified(h: Harness, key: str) -> bool:
    """The outbox row `key` is recorded as sent, with no message ID."""
    row = h.store.db.execute("SELECT status, message_id FROM outbox WHERE key = ?", (key,)).fetchone()
    return row is not None and tuple(row) == ("sent", None)


@needs_tmux
def test_chunk_zero_sent_without_a_message_id(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        without_message_id(h, "t:0")
        h.daemon.post_parts("t", PARTS)
        h.daemon.post("after", "unrelated", None)
        await drained(h, "t:1", "t:2", "after")
        assert sent_unidentified(h, "t:0")      # sent_mid needs the recorded ID, and there is none
        assert [reply_to(h, k) for k in ("t:1", "t:2")] == [None, None]
        assert fallback(h, "t:1", "no-message-id") and fallback(h, "t:2", "no-message-id")
    run_with(tmp_path, scenario)


@needs_tmux
def test_chunk_zero_failed(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)
        h.fake.fail_sends = MAX_SEND_ATTEMPTS
        h.daemon.post_parts("t", PARTS)
        h.daemon.post("after", "unrelated", None)
        await drained(h, "t:1", "t:2", "after")
        assert sent_mid(h, "t:0") is None and len(sends(h, "t:0")) == MAX_SEND_ATTEMPTS
        assert [reply_to(h, k) for k in ("t:1", "t:2")] == [None, None]
        assert fallback(h, "t:1", "parent-failed") and fallback(h, "t:2", "parent-failed")
    run_with(tmp_path, scenario)


@needs_tmux
def test_chunk_zero_row_deleted(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        h.daemon.post_parts("t", PARTS)                 # held: nobody has been seen yet
        h.store.db.execute("DELETE FROM outbox WHERE key = 't:0'")
        h.daemon.post("after", "unrelated", None)
        await joined(h)
        await drained(h, "t:1", "t:2", "after")
        assert [reply_to(h, k) for k in ("t:1", "t:2")] == [None, None]
        assert fallback(h, "t:1", "parent-missing") and fallback(h, "t:2", "parent-missing")
    run_with(tmp_path, scenario)


@needs_tmux
def test_a_child_picked_before_its_parent_never_waits(tmp_path: Path) -> None:
    """Lanes order the outbox, so a lane-1 child is picked while its lane-2 parent is still pending."""
    async def scenario(h: Harness) -> None:
        h.daemon.post("t:0", "zero", None, lane=2)
        h.daemon.post("t:1", "one", None, lane=1, thread_key="t:0")
        await joined(h)
        await drained(h, "t:0", "t:1")
        keys = [r["idempotency_key"] for r in h.fake.sent]
        assert keys.index("t:1") < keys.index("t:0") and reply_to(h, "t:1") is None
        assert fallback(h, "t:1", "parent-pending")
    run_with(tmp_path, scenario)


@needs_tmux
def test_a_crash_between_resolution_and_send(tmp_path: Path) -> None:
    """The outbox task dies after the resolution committed; its restart sends to the stored target."""
    async def scenario(h: Harness) -> None:
        await join(h)
        real = h.daemon.client.send_final
        crashed: list[str] = []

        async def send_final(account: str, group: str, text: str, reply_to: str | None, k: str) -> FinalSent:
            if k == "t:1" and not crashed:
                crashed.append(k)
                raise RuntimeError("injected")
            return await real(account, group, text, reply_to, k)
        h.daemon.client.send_final = send_final       # type: ignore[method-assign]
        h.daemon.post_parts("t", PARTS)
        await drained(h, "t:0", "t:1", "t:2")
        first = sent_mid(h, "t:0")
        assert crashed == ["t:1"] and [reply_to(h, k) for k in ("t:1", "t:2")] == [first, first]
        assert stored(h.store, "t:1") == (first, None)
        assert audited(h, kind="task", action="crashed", error="RuntimeError")
    run_with(tmp_path, scenario)


@needs_tmux
def test_a_retry_keeps_the_same_target(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)

        def fail_next(req: dict[str, Any]) -> None:
            if req["idempotency_key"] == "t:0":
                h.fake.fail_sends = 1
        h.fake.on_send = fail_next
        h.daemon.post_parts("t", PARTS)
        await drained(h, "t:0", "t:1", "t:2")
        first = sent_mid(h, "t:0")
        attempts = sends(h, "t:1")
        assert len(attempts) == 2 and {a["reply_to_message_id_hex"] for a in attempts} == {first}
        assert not audited(h, kind="thread_fallback")
    run_with(tmp_path, scenario)


@needs_tmux
def test_a_card_whose_chunk_zero_failed_is_not_delivered(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)
        h.fake.fail_sends = MAX_SEND_ATTEMPTS
        ask_id = await posted(h, question(body=BODY3))
        await drained(h, f"ask:{ask_id}:1", f"ask:{ask_id}:2")
        assert sent_mid(h, f"ask:{ask_id}:0") is None
        assert fallback(h, f"ask:{ask_id}:1", "parent-failed", f"ask:{ask_id}:0")
        assert not h.store.card_delivered(ask_id)
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_notices_and_repeats_thread_bumps_and_details_do_not_change(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(NoticePost("t", "\n".join(c * 150 for c in "pq")), NONE)
        notice_id = reply.message.removeprefix("posted ")
        nkeys = [f"notice:{notice_id}:{i}" for i in range(3)]
        await drained(h, *nkeys)
        assert [reply_to(h, k) for k in nkeys] == [None, sent_mid(h, nkeys[0]), sent_mid(h, nkeys[0])]

        ask_id = await posted(h, question(body=BODY3))
        await h.until(lambda: h.store.card_delivered(ask_id))
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        rkeys = [f"askr:{ask_id}:{mid}:{i}" for i in range(3)]
        await drained(h, *rkeys)
        assert [reply_to(h, k) for k in rkeys] == [None, sent_mid(h, rkeys[0]), sent_mid(h, rkeys[0])]

        target = h.store.bump_target(ask_id)
        bump = await send(h, "!asks bump", None)
        await settled(h, bump)
        bkey = f"asknote:{ask_id}:bump:{bump}:0"
        await drained(h, bkey)
        assert target is not None and reply_to(h, bkey) == target

        card0 = sent_mid(h, f"ask:{ask_id}:0")
        request = await send(h, "!details", card0)
        await settled(h, request)
        dkeys = [k for (k,) in h.store.db.execute("SELECT key FROM outbox WHERE key LIKE ?",
                                                    (f"askd:{ask_id}:{request}:%",))]
        assert dkeys
        await drained(h, *dkeys)
        assert {reply_to(h, k) for k in dkeys} == {request}
        assert not audited(h, kind="thread_fallback")
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


def on_part_three(h: Harness, ask_id: str) -> Callable[[], Awaitable[str]]:
    async def mid() -> str:
        await h.until(lambda: h.store.card_delivered(ask_id))
        third = sent_mid(h, f"ask:{ask_id}:2")
        assert third is not None and reply_to(h, f"ask:{ask_id}:2") == sent_mid(h, f"ask:{ask_id}:0")
        return third
    return mid


@needs_tmux
def test_a_reaction_on_part_three_answers_a_question(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question(body=BODY3))
        third = await on_part_three(h, ask_id)()
        _, text = await reacted(h, "🎉", third)
        assert text == f"Answer recorded for ask {ask_id}."
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_a_reaction_on_part_three_decides_an_approval(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        row = h.store.ask(ask_id)
        assert row is not None and row.card_parts >= 3
        third = await on_part_three(h, ask_id)()
        _, text = await reacted(h, "👍", third)
        assert text.startswith(f"Approved {BEAD} as op")
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=200)


def test_the_notice_key_matches_what_post_parts_writes() -> None:
    assert notices.NOTICE_KEY.fullmatch("notice:abcdef:3")
