"""cxmkf Amendment 1 §2 (r1 B1): part 1 of a multi-part post shows its count, and a continuation that fails
for good gets a short visible reply under part 1 (K23, K22f).

Nothing here runs the real claude or wn-agent, touches ~/.claude, bd or the network. IDs are built at
runtime, never 64-hex literals.
"""

from pathlib import Path

from admind_asks_fixture import sent_mid
from test_admind_approvals import send, settled
from test_admind_asks import NONE, audited, posted, question
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_threading import BODY3, fast_backoff, reply_to

from heterodyne.admind import chunk
from heterodyne.admind.notices import NoticePost


def body(n: int, width: int = 150) -> list[tuple[str, chunk.Kind]]:
    return [("LABEL", "atomic"), ("head", "atomic"), *[(c * width, "body") for c in "pqrstuvw"[:n]]]


# --- the part count on part 1 (K23) ----------------------------------------------------------------
def test_part_one_of_a_multi_part_post_ends_with_its_count() -> None:
    parts = chunk.pack(body(3), 200)
    n = len(parts)
    assert n >= 3
    assert parts[0].split("\n")[0] == "LABEL"
    assert parts[0].split("\n")[-1] == f"↳ part 1/{n}"
    for i, part in enumerate(parts[1:], 2):
        assert part.startswith(chunk.marker(i, n)) and "↳ part 1/" not in part
    assert all(len(p) <= 200 for p in parts)


def test_a_one_part_post_has_no_marker() -> None:
    parts = chunk.pack(body(2, 40), 4000)
    assert len(parts) == 1 and "↳ part" not in parts[0]


def test_full_chunks_with_both_markers_stay_within_chunk_chars() -> None:
    for chunk_chars in (200, 201, 517, 4000):
        room = chunk.usable(chunk_chars)
        lines: list[tuple[str, chunk.Kind]] = [("x" * room, "atomic") for _ in range(12)]
        parts = chunk.pack(lines, chunk_chars)
        assert len(parts) == 12
        assert all(len(p) <= chunk_chars for p in parts), chunk_chars


# --- a failed continuation (K22f) ------------------------------------------------------------------
def partfail(h: Harness, key: str) -> list[tuple[str, str | None]]:
    return [(str(t), r) for t, r in h.store.db.execute(
        "SELECT text, reply_to FROM outbox WHERE key = ?", (f"partfail:{key}",))]


def failed(h: Harness, key: str) -> bool:
    r = h.store.db.execute("SELECT status FROM outbox WHERE key = ?", (key,)).fetchone()
    return r is not None and r[0] == "failed"


async def reported(h: Harness, key: str) -> None:
    await h.until(lambda: failed(h, key) and sent_mid(h, f"partfail:{key}") is not None)


@needs_tmux
def test_a_failed_card_part_is_reported_under_part_one(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)              # joins too
        h.fake.fail_key = lambda key: key.startswith("ask:") and key.endswith(":1")
        ask_id = await posted(h, question(body=BODY3))
        key, parent = f"ask:{ask_id}:1", f"ask:{ask_id}:0"
        await reported(h, key)
        assert partfail(h, key) == [("part 2/3 failed to deliver, ask the poster to repeat",
                                     sent_mid(h, parent))]
        assert reply_to(h, f"partfail:{key}") == sent_mid(h, parent)
        await h.until(lambda: audited(h, kind="part_failed", key=key, parent=parent))
        assert audited(h, kind="send", key=key, action="failed")
        assert not h.store.card_delivered(ask_id)
        assert h.store.ask_for_message(sent_mid(h, f"partfail:{key}")) is None   # not a card
        assert partfail(h, f"ask:{ask_id}:2") == []                             # only the failed part
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_a_failed_notice_part_and_a_failed_repeat_part_are_reported(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)              # joins too
        h.fake.fail_key = lambda key: key.startswith(("notice:", "askr:")) and key.endswith(":2")
        reply = await h.daemon.on_ask(NoticePost("Deploy complete", "\n".join(c * 150 for c in "pq")), NONE)
        notice_id = reply.message.removeprefix("posted ")
        key = f"notice:{notice_id}:2"
        await reported(h, key)
        assert partfail(h, key) == [("part 3/3 failed to deliver, ask the poster to repeat",
                                     sent_mid(h, f"notice:{notice_id}:0"))]

        ask_id = await posted(h, question(body=BODY3))
        await h.until(lambda: h.store.card_delivered(ask_id))
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        rkey = f"askr:{ask_id}:{mid}:2"
        await reported(h, rkey)
        assert partfail(h, rkey) == [("part 3/3 failed to deliver, ask the poster to repeat",
                                      sent_mid(h, f"askr:{ask_id}:{mid}:0"))]
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_part_one_failing_is_not_reported_and_a_later_failure_goes_top_level(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)              # joins too
        h.fake.fail_key = lambda key: key.startswith("ask:") and key.endswith((":0", ":1"))
        ask_id = await posted(h, question(body=BODY3))
        key = f"ask:{ask_id}:1"
        await reported(h, key)
        assert failed(h, f"ask:{ask_id}:0") and partfail(h, f"ask:{ask_id}:0") == []
        assert partfail(h, key) == [("part 2/3 failed to deliver, ask the poster to repeat", None)]
        assert reply_to(h, f"partfail:{key}") is None
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


@needs_tmux
def test_failed_details_parts_are_not_reported(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await fast_backoff(h)              # joins too
        ask_id = await posted(h, question(body=BODY3))
        await h.until(lambda: h.store.card_delivered(ask_id))
        h.fake.fail_key = lambda key: key.startswith("askd:")
        request = await send(h, "!details", sent_mid(h, f"ask:{ask_id}:0"))
        await settled(h, request)
        keys = [k for (k,) in h.store.db.execute("SELECT key FROM outbox WHERE key LIKE ?",
                                                 (f"askd:{ask_id}:{request}:%",))]
        assert len(keys) >= 2
        await h.until(lambda: all(failed(h, k) for k in keys))
        reports = h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'partfail:%'").fetchone()
        assert reports[0] == 0
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})
