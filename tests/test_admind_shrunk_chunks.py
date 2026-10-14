"""cxmkf r1 B3: a repeat or `!details` after `chunk_chars` was lowered never cuts a link (K23).

A question posted at chunk_chars 4000 with a link line wider than a chunk at 200 is refused visibly when it is
repeated or asked for in full at 200: nothing of it is queued, the operator is told why, and the audit says
"link too long". Nothing here runs the real claude or wn-agent, touches ~/.claude, bd or the network. IDs are
built at runtime, never 64-hex literals.
"""

import dataclasses
from pathlib import Path

import pytest
from admind_asks_fixture import sent_mid
from test_admind_approvals import send, settled
from test_admind_asks import audited, joined, posted, question, row
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_links import BASE, SHA

from heterodyne.admind import asks, chunk
from heterodyne.admind.links import LinkSpec

URL = f"{BASE}/blob/{SHA}/docs/" + "z" * 250 + ".md"       # pinned, and wider than a chunk at 200
LINK = LinkSpec("design", URL)


def shrink(h: Harness, chunk_chars: int = 200) -> None:
    h.daemon.s = dataclasses.replace(h.daemon.s, chunk_chars=chunk_chars)


def outbox(h: Harness, pattern: str) -> list[str]:
    return [t for (t,) in h.store.db.execute("SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq",
                                             (pattern,))]


def no_cut_url(h: Harness) -> None:
    """Every queued message has the link whole or not at all."""
    for (text,) in h.store.db.execute("SELECT text FROM outbox"):
        assert URL in text or URL[:60] not in text, text[:80]


def test_full_chunks_refuses_rather_than_cutting_a_link() -> None:
    r = row(links=asks.stored_links([LINK]))
    assert any(URL in c for c in asks.full_chunks(r, 4000))
    with pytest.raises(chunk.TooWide):
        asks.full_chunks(r, 200)


@needs_tmux
def test_a_repeat_after_chunk_chars_is_lowered_is_refused_visibly(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question(links=[LINK]))
        await h.until(lambda: h.store.card_delivered(ask_id))
        shrink(h)
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        assert outbox(h, f"askr:{ask_id}:%") == []
        reply = "".join(outbox(h, f"cmd:{mid}:%"))
        assert "Repeated 0 asks." in reply
        assert asks.SKIPPED["link too long"].format(ask_id=ask_id) in reply
        assert audited(h, kind="command", command="asks", sub="repeat",
                       skipped=[{"ask_id": ask_id, "why": "link too long"}])
        no_cut_url(h)
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 4000})


@needs_tmux
def test_details_after_chunk_chars_is_lowered_is_refused_visibly(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question(links=[LINK]))
        await h.until(lambda: h.store.card_delivered(ask_id))
        card0 = sent_mid(h, f"ask:{ask_id}:0")
        shrink(h)
        mid = await send(h, "!details", card0)
        await settled(h, mid)
        assert outbox(h, f"askd:{ask_id}:%") == []
        assert h.store.db.execute("SELECT COUNT(*) FROM ask_details").fetchone()[0] == 0
        reply = "".join(outbox(h, f"cmd:{mid}:%"))
        assert reply == asks.DETAILS_LINK_TOO_LONG.format(ask_id=ask_id, n=len(f"{LINK.label}: {URL}"),
                                                          usable=chunk.usable(200), c=200)
        assert audited(h, kind="ask", action="details-refused", message_id=mid, ask_id=ask_id,
                       reason="link too long")
        no_cut_url(h)
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 4000})


@needs_tmux
def test_a_repeat_and_details_still_work_when_the_link_fits(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, question(links=[LINK]))
        await h.until(lambda: h.store.card_delivered(ask_id))
        shrink(h, 1000)
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        assert any(URL in t for t in outbox(h, f"askr:{ask_id}:{mid}:%"))
        request = await send(h, "!details", sent_mid(h, f"ask:{ask_id}:0"))
        await settled(h, request)
        assert any(URL in t for t in outbox(h, f"askd:{ask_id}:{request}:%"))
        no_cut_url(h)
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 4000})
