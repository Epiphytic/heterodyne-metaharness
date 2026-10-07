"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from admind_asks_fixture import sent_mid
from admind_waits import stays, wait_until
from test_admind_approvals import (
    BEAD,
    D2,
    LINES,
    LONG,
    D,
    ask_status,
    attempts,
    bead,
    card,
    decisions,
    edit,
    go,
    go_file,
    legacy,
    queued,
    say,
    send,
    settled,
    waiting,
)
from test_admind_asks import NONE, audited, card_sent, joined, outbox, pasted, posted, question, row
from test_admind_daemon import Harness, needs_tmux
from test_admind_reactions import OLD_TABLES, columns, outbox_count, react, reacted, threads
from test_admind_settings import BASE_CONFIG, write

from heterodyne.admind import asks, chunk, commands
from heterodyne.admind import daemon as daemon_mod
from heterodyne.admind import store as store_mod
from heterodyne.admind.approvals import Attempt
from heterodyne.admind.audit import ref_id
from heterodyne.admind.daemon import (
    APPROVAL_NOT_ANSWERED,
    ASK_NO_APPROVALS,
    CONTROL_REFUSED,
    MISMATCH,
    NEEDS_DETAILS,
    NOT_A_CARD_REPLY,
    NOT_DELIVERED,
    REASON_TOO_LONG,
)
from heterodyne.admind.settings import resolve
from heterodyne.admind.store import Store
from heterodyne.config import ConfigError, load


# --- B1, B10, B11: parsing and help ----------------------------------------------------------------
def test_asks_bump_and_repeat_parse() -> None:
    assert commands.parse("!asks") == commands.Command("asks")
    assert commands.parse("!asks bump") == commands.Command("asks", arg="bump")
    assert commands.parse("!asks  repeat ") == commands.Command("asks", arg="repeat")


@pytest.mark.parametrize("text", ["!asks foo", "!asks bump extra", "!asks repeat now", "!asks Bump",
                                  "!asks bump repeat"])
def test_other_asks_arguments_are_refused_with_the_usage(text: str) -> None:
    with pytest.raises(commands.CommandError) as err:
        commands.parse(text)
    assert str(err.value) == "Usage: !asks [bump|repeat]."


def test_help_names_bump_and_repeat() -> None:
    assert "!asks [bump|repeat]" in commands.HELP


# --- B14: the setting --------------------------------------------------------------------------------
def with_hours(value: object) -> str:
    return BASE_CONFIG.replace('profile = "admin"', f'profile = "admin"\nask_bump_hours = {value}')


def test_ask_bump_hours_defaults_to_12(tmp_path: Path) -> None:
    env = write(tmp_path)
    assert resolve(load(None, env), env).ask_bump_hours == 12


@pytest.mark.parametrize("hours", [0, 1, 720])
def test_ask_bump_hours_in_range(tmp_path: Path, hours: int) -> None:
    env = write(tmp_path, with_hours(hours))
    assert resolve(load(None, env), env).ask_bump_hours == hours


@pytest.mark.parametrize("value", ["-1", "721", "1.5", "true", '"12"'])
def test_ask_bump_hours_out_of_range_is_refused(tmp_path: Path, value: str) -> None:
    env = write(tmp_path, with_hours(value))
    with pytest.raises(ConfigError, match=r"\[admind\] ask_bump_hours must be an integer from 0 to 720"):
        resolve(load(None, env), env)


# --- B14: the activity clock in the store ----------------------------------------------------------
T0 = "2026-10-07T00:00:00+00:00"
T1 = "2026-10-07T01:00:00+00:00"
T2 = "2026-10-07T02:00:00+00:00"


def activity(store: Store, ask_id: str = "k7m2") -> str | None:
    r = store.ask(ask_id)
    assert r is not None
    return r.last_activity_at


def test_a_posted_ask_starts_its_clock_at_its_creation(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(created_at=T1, updated_at=T1), None)
    assert activity(store) == T1
    store.close()


def test_the_clock_only_moves_forward(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(created_at=T1, updated_at=T1), None)
    store.touch_ask("k7m2", T0)
    assert activity(store) == T1
    store.touch_ask("k7m2", T2)
    assert activity(store) == T2
    store.touch_ask("k7m2", T1)
    assert activity(store) == T2
    store.close()


def test_the_migration_starts_existing_clocks_at_the_migration_time(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ask created days ago, with a note an hour ago: not due at deploy; its clock starts then."""
    db = sqlite3.connect(tmp_path / "admind.db")
    db.executescript(OLD_TABLES)
    db.close()
    monkeypatch.setattr(store_mod, "now", lambda: T2)
    store = Store(tmp_path / "admind.db")
    assert "last_activity_at" in columns(store, "asks") and activity(store, "p4xw") == T2
    store.close()
    monkeypatch.setattr(store_mod, "now", lambda: "2026-10-09T00:00:00+00:00")
    again = Store(tmp_path / "admind.db")          # once: a later start never re-initialises it
    assert activity(again, "p4xw") == T2
    again.close()


# --- B6, B12, B13: the recognizers, repeat delivery and the bump target -----------------------------
CMD = "a1" * 32        # a command's message ID
CMD2 = "a2" * 32


def mid(n: int) -> str:
    """The fake message ID wn-agent gave the n-th sent row."""
    return f"{n:064x}"


@pytest.mark.parametrize("key", [f"asknote:k7m2:bump:{CMD}:0", f"asknote:k7m2:bump:{CMD}:12",
                                 "asknote:k7m2:autobump:7:0", "asknote:k7m2:autobump:123:3"])
def test_bump_for_message_matches_both_reminder_forms(tmp_path: Path, key: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.bump_for_message(mid(1)) == "k7m2"
    assert store.ask_for_message(mid(1)) is None            # a bump is never a card (B6)
    store.close()


@pytest.mark.parametrize("key", [
    "asknote:k7m2:cancelled:0:0", "asknote:k7m2:superseded:p4xw:0", f"asknote:k7m2:reconcile:{CMD}:0",
    "asknote:k7m2:cancelled:autobump:0", "asknote:k7m2:autobump:7:0:0", "asknote:k7m2:autobump::0",
    "asknote:k7m2:autobump:x7:0", "asknote:k7m2:autobump:7:", f"asknote:k7m2:bump:{CMD[:-1]}:0",
    f"asknote:k7m2:bump:{CMD.upper()}:0", f"asknote:k7m2:bump:{CMD}0:0", f"asknote:k7m2:bump:{CMD}:x",
    f"asknote:k7m2:bump:{CMD}:0\n", "asknote:K7M2:autobump:7:0", "asknote:k7m2:bump:7:0",
    f"asknote:k7m2:autobump:{CMD}:0", "ask:k7m2:0", f"askd:k7m2:{CMD}:0", f"askr:k7m2:{CMD}:0",
    f"xasknote:k7m2:bump:{CMD}:0", f"ask:{CMD}:0"])
def test_bump_for_message_matches_nothing_else(tmp_path: Path, key: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.bump_for_message(mid(1)) is None
    store.close()


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_bump_for_message_needs_a_sent_row(tmp_path: Path, status: str) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, f"asknote:k7m2:bump:{CMD}:0", status)
    outbox(store, "asknote:k7m2:autobump:1:0", status)
    store.db.execute("UPDATE outbox SET message_id = ?", (mid(1),))
    assert store.bump_for_message(mid(1)) is None
    assert store.bump_for_message(None) is None
    store.close()


def test_bump_for_message_needs_the_ask(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    outbox(store, f"asknote:k7m2:bump:{CMD}:0", "sent", mid(1))
    assert store.bump_for_message(mid(1)) is None
    store.close()


@pytest.mark.parametrize(("key", "card"), [
    (f"askr:k7m2:{CMD}:0", True), (f"askr:k7m2:{CMD}:7", True), (f"askr:k7m2:{CMD[:-1]}:0", False),
    (f"askr:k7m2:{CMD.upper()}:0", False), ("askr:k7m2:0", False), (f"askr:k7m2:{CMD}:", False),
    (f"askr:k7m2:{CMD}:0:0", False), (f"askr:k7m:{CMD}:0", False), (f"askrx:k7m2:{CMD}:0", False)])
def test_a_repeat_chunk_is_a_card(tmp_path: Path, key: str, card: bool) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "sent", mid(1))
    assert store.ask_for_message(mid(1)) == ("k7m2" if card else None)
    assert store.bump_for_message(mid(1)) is None
    store.close()


def test_a_pending_or_failed_repeat_chunk_is_not_a_card(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, f"askr:k7m2:{CMD}:0", "pending")
    outbox(store, f"askr:k7m2:{CMD}:1", "failed")
    store.db.execute("UPDATE outbox SET message_id = ?", (mid(1),))
    assert store.ask_for_message(mid(1)) is None
    store.close()


def repeated(store: Store, request: str, statuses: list[str], at: str = T1) -> None:
    store.add_repeat("k7m2", request, len(statuses), at)
    for i, status in enumerate(statuses):
        outbox(store, f"askr:k7m2:{request}:{i}", status, mid(int(request[:2], 16) * 100 + i))


def test_an_original_of_2_and_a_repeat_of_3(tmp_path: Path) -> None:
    """R8 delivery from one complete repeat, counted by its own parts (B12)."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "failed")
    assert not store.card_delivered("k7m2")
    repeated(store, CMD, ["sent", "sent", "pending"])
    assert not store.card_delivered("k7m2")             # two of three: the original's count is not enough
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = ?",
                                     (f"askr:k7m2:{CMD}:2",)).fetchone()[0], mid(3))
    assert store.card_delivered("k7m2")
    store.close()


def test_two_partial_repeats_never_combine(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=1), None)
    outbox(store, "ask:k7m2:0", "failed")
    repeated(store, CMD, ["sent", "failed"])
    repeated(store, CMD2, ["failed", "sent"])
    assert not store.card_delivered("k7m2")
    assert store.bump_target("k7m2") is None
    store.close()


def test_the_original_card_still_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "sent", mid(2))
    assert store.card_delivered("k7m2")
    store.close()


def test_a_repeat_with_no_parts_never_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=1), None)
    store.add_repeat("k7m2", CMD, 0, T1)
    assert not store.card_delivered("k7m2")
    store.close()


def test_a_repeat_is_recorded_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    store.add_repeat("k7m2", CMD, 2, T1)
    store.add_repeat("k7m2", CMD, 5, T2)        # a replay of the same command changes nothing
    assert store.db.execute("SELECT parts, created_at FROM ask_repeats").fetchall() == [(2, T1)]
    store.close()


def test_the_bump_target_prefers_the_original_then_the_earliest_full_repeat(tmp_path: Path) -> None:
    """B3/B13: the original's chunk 0 whenever it is sent; otherwise the first chunk of the earliest fully
    sent repeat; otherwise none. A late original becomes the target."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "pending")
    outbox(store, "ask:k7m2:1", "sent", mid(2))
    assert store.bump_target("k7m2") is None
    repeated(store, CMD2, ["sent", "pending"], T1)         # the earliest, but not fully sent
    repeated(store, CMD, ["sent", "sent"], T2)
    repeated(store, "a3" * 32, ["sent"], "2026-10-07T03:00:00+00:00")
    assert store.bump_target("k7m2") == mid(0xa1 * 100)
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = ?",
                                     (f"askr:k7m2:{CMD2}:1",)).fetchone()[0], mid(9))
    assert store.bump_target("k7m2") == mid(0xa2 * 100)      # now the earliest fully sent
    store.mark_sent(store.db.execute("SELECT seq FROM outbox WHERE key = 'ask:k7m2:0'").fetchone()[0], mid(1))
    assert store.bump_target("k7m2") == mid(1)               # the late original
    store.close()


def test_the_bump_target_is_the_original_chunk_0_even_if_the_card_is_partial(tmp_path: Path) -> None:
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(card_parts=2), None)
    outbox(store, "ask:k7m2:0", "sent", mid(1))
    outbox(store, "ask:k7m2:1", "failed")
    outbox(store, f"askd:k7m2:{CMD}:0", "sent", mid(5))
    assert store.bump_target("k7m2") == mid(1)
    store.close()


@pytest.mark.parametrize(("key", "blocks"), [
    (f"asknote:k7m2:bump:{CMD}:0", True), ("asknote:k7m2:autobump:3:1", True), (f"askr:k7m2:{CMD}:0", True),
    ("asknote:k7m2:cancelled:0:0", False), ("ask:k7m2:0", False), (f"askd:k7m2:{CMD}:0", False),
    ("asknote:p4xw:autobump:3:0", False), (f"askr:p4xw:{CMD}:0", False)])
def test_pending_reminders(tmp_path: Path, key: str, blocks: bool) -> None:
    """B14: a pending reminder or repeat chunk of the ask blocks an automatic bump; failed or sent ones do
    not, and neither does anything else."""
    store = Store(tmp_path / "admind.db")
    store.insert_ask(row(), None)
    outbox(store, key, "pending")
    assert store.reminder_pending("k7m2") is blocks
    seq = store.db.execute("SELECT seq FROM outbox WHERE key = ?", (key,)).fetchone()[0]
    store.mark_failed(seq)
    assert not store.reminder_pending("k7m2")
    store.close()


# --- B4: the reminder's wording ----------------------------------------------------------------------
APPROVAL_TAIL = "React 👍 or 👎 on the card above, or reply to it with approve or deny <reason>."
ANSWER_TAIL = "Reply to the card above to answer."
TOKEN = "ghp_" + "A" * 36


def three_hours_on() -> datetime:
    return datetime.fromisoformat(row().created_at) + timedelta(hours=3, minutes=5)


@pytest.mark.parametrize(("kind", "tail"), [("approval", APPROVAL_TAIL), ("question", ANSWER_TAIL),
                                            ("merge", ANSWER_TAIL)])
def test_reminder_wording(kind: str, tail: str) -> None:
    r = row(kind=kind, title="Approve the plan 4 sandbox runtime")
    assert asks.reminder(r, three_hours_on()) == (
        f"Still outstanding: ask k7m2 · {kind} · 3h · Approve the plan 4 sandbox runtime.\n{tail}")
    assert asks.reminder(r, three_hours_on(), every=12) == (
        "Still outstanding (automatic reminder, every 12 h): ask k7m2 · "
        f"{kind} · 3h · Approve the plan 4 sandbox runtime.\n{tail}")


def test_reminder_title_is_cut_at_80() -> None:
    r = row(title="t" * 79 + "uv")
    assert f"· {'t' * 79}u.\n" in asks.reminder(r, three_hours_on())


@pytest.mark.parametrize("secret", [TOKEN, "ab" * 40])
def test_reminder_title_is_redacted_whole_before_it_is_cut(secret: str) -> None:
    """A secret that crosses character 80 is redacted whole: no fragment of it appears (B4)."""
    r = row(title="x" * 59 + " " + secret + " tail")
    text = asks.reminder(r, three_hours_on())
    assert secret[:8] not in text and "ab" * 4 not in text and "AAAA" not in text
    assert "x" * 59 + " <redacted" in text


def test_a_reminder_splits_like_any_notice() -> None:
    """B8: at the 200-character minimum an approval reminder with a long title takes two chunks."""
    r = row(kind="approval", title="t" * 80)
    assert len(chunk.split(asks.reminder(r, three_hours_on()), 200)) == 2


# --- B1-B5, B7-B9: `!asks bump` ------------------------------------------------------------------------
def rows(h: Harness, like: str) -> list[tuple[str, str | None, str]]:
    """(key, reply_to, text) of the outbox rows whose key is LIKE `like`, in queue order."""
    return [(str(k), r, str(t)) for k, r, t in h.store.db.execute(
        "SELECT key, reply_to, text FROM outbox WHERE key LIKE ? ORDER BY seq", (like,)).fetchall()]


def bump_rows(h: Harness, ask_id: str, command: str) -> list[tuple[str, str | None, str]]:
    return rows(h, f"asknote:{ask_id}:bump:{command}:%")


def cmd_reply(h: Harness, mid: str) -> str:
    return "".join(t for _, _, t in rows(h, f"cmd:{mid}:%"))


def set_status(h: Harness, ask_id: str, status: str) -> None:
    """Put an ask in `status` without an attempt (so the `!asks` backstop leaves it alone)."""
    h.store.db.execute("UPDATE asks SET status = ? WHERE ask_id = ?", (status, ask_id))


def undelivered(h: Harness, ask_id: str, kind: str = "question") -> None:
    """An open ask whose card was never queued, let alone sent."""
    at = asks.stamp(asks.now())
    h.store.insert_ask(row(ask_id, kind=kind, created_at=at, updated_at=at), None)


@needs_tmux
def test_bump_threads_each_open_ask_to_its_card_and_reports_the_rest(tmp_path: Path) -> None:
    """B2, B3, B5, B9: open asks only, oldest first, each reminder threaded to exactly its card's chunk 0;
    a second and a third bump thread there again, never to an earlier bump."""
    async def scenario(h: Harness) -> None:
        a, a_first = await card(h)
        q = await posted(h, question())
        q_first = await card_sent(h, q)
        answered = await posted(h, question("Which host?"))
        await card_sent(h, answered)
        assert (await say(h, f"!answer {answered} the first one", None)).startswith("Answer")
        await h.daemon.on_ask(asks.AskGet(answered), NONE)        # collecting it changes nothing (B2)
        deciding = await posted(h, question("Which port?"))
        uncertain = await posted(h, question("Which user?"))
        cancelled = await posted(h, question("Which disk?"))
        await card_sent(h, cancelled)
        set_status(h, deciding, "deciding")
        set_status(h, uncertain, "uncertain")
        set_status(h, cancelled, "cancelled")
        undelivered(h, "n2n2")
        mid = ""
        for _ in range(3):
            mid = await send(h, "!asks bump", None)
            await settled(h, mid)
            assert cmd_reply(h, mid) == (f"Bumped 2 asks: {a}, {q}.\n"
                                         f"{answered} is answered; awaiting its asker\n"
                                         f"{deciding} is deciding\n{uncertain} is uncertain\n"
                                         "n2n2: card not delivered; try !asks repeat")
            assert {r for _, r, _ in rows(h, f"cmd:{mid}:%")} == {mid}         # threaded to the command
            assert [(k, r) for k, r, _ in bump_rows(h, a, mid)] == [(f"asknote:{a}:bump:{mid}:0", a_first)]
            assert [(k, r) for k, r, _ in bump_rows(h, q, mid)] == [(f"asknote:{q}:bump:{mid}:0", q_first)]
            for other in (answered, deciding, uncertain, cancelled, "n2n2"):
                assert bump_rows(h, other, "%") == []
            assert h.store.inbound_status(mid) == "done"
            assert audited(h, kind="command", message_id=ref_id(mid), command="asks", sub="bump",
                           bumped=[a, q], skipped=[{"ask_id": answered, "why": "answered"},
                                                   {"ask_id": deciding, "why": "deciding"},
                                                   {"ask_id": uncertain, "why": "uncertain"},
                                                   {"ask_id": "n2n2", "why": "card not delivered"}])
            await wait_until(lambda m=mid: sent_mid(h, f"asknote:{q}:bump:{m}:0") is not None)
        approval = h.store.ask(a)
        assert approval is not None
        _, _, text = bump_rows(h, a, mid)[0]
        assert text.startswith(f"Still outstanding: ask {a} · approval · ")
        assert text.endswith(f" · {approval.title[:80]}.\n{APPROVAL_TAIL}")
        _, _, text = bump_rows(h, q, mid)[0]
        assert text.startswith(f"Still outstanding: ask {q} · question · ")
        assert text.endswith(f" · Which relay?.\n{ANSWER_TAIL}")
        assert decisions(h) == [] and "Still outstanding" not in pasted(h)
    go(tmp_path, scenario)


@needs_tmux
def test_bump_summary_with_one_and_with_none(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        assert await say(h, "!asks bump", None, tag="cmd") == "No outstanding asks."
        q = await posted(h, question())
        await card_sent(h, q)
        assert await say(h, "!asks bump", None, tag="cmd") == f"Bumped 1 ask: {q}."
        set_status(h, q, "deciding")
        assert await say(h, "!asks bump", None, tag="cmd") == f"Bumped 0 asks.\n{q} is deciding"
    go(tmp_path, scenario)


@needs_tmux
def test_a_split_reminder_threads_every_chunk_to_chunk_0(tmp_path: Path) -> None:
    """B3, B8: a card of several chunks threads to chunk 0; at chunk_chars=200 an approval reminder with an
    80-character title splits, and every chunk of it threads there too."""
    title = "t" * 80
    readout = {**LINES, "title": ["title:", f"  │ {title}"], "description": LONG["description"]}

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert h.store.ask(ask_id).card_parts > 2  # type: ignore[union-attr]
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        bumps = bump_rows(h, ask_id, mid)
        assert len(bumps) == 2 and {r for _, r, _ in bumps} == {first}
        assert "".join(t for _, _, t in bumps).endswith(f"{title}.\n{APPROVAL_TAIL}")
    go(tmp_path, scenario, {BEAD: bead(readout=readout)}, chunk_chars=200)


@needs_tmux
def test_a_refreshed_ask_is_bumped_on_its_own_card(tmp_path: Path) -> None:
    """R29: the fresh ask's bumps thread to its own chunk 0; the stale one is not bumped."""
    async def scenario(h: Harness) -> None:
        old, old_first = await card(h)
        edit(tmp_path, digest=D2)
        await say(h, "approve", old_first)
        fresh = h.store.newer_ask(old)
        assert fresh is not None and fresh.refreshed_from == old and ask_status(h, old) == "stale"
        await wait_until(lambda: h.store.card_delivered(fresh.ask_id))
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        assert cmd_reply(h, mid) == f"Bumped 1 ask: {fresh.ask_id}."
        assert {r for _, r, _ in bump_rows(h, fresh.ask_id, mid)} == {sent_mid(h, f"ask:{fresh.ask_id}:0")}
        assert bump_rows(h, old, "%") == []
    go(tmp_path, scenario)


@needs_tmux
def test_bump_runs_the_backstop_first(tmp_path: Path) -> None:
    """B1: a stranded `deciding` ask is reconciled back to open, then bumped."""
    stale = "ee" * 32

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert h.store.begin_attempt(Attempt(ask_id, stale, "approve", "op", "marmot:" + ref_id(stale), D,
                                             None))
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        assert ask_status(h, ask_id) == "open" and attempts(h, ask_id)[0][-1] == "untouched"
        assert cmd_reply(h, mid) == f"Bumped 1 ask: {ask_id}."
        assert {r for _, r, _ in bump_rows(h, ask_id, mid)} == {first}
    go(tmp_path, scenario)


@needs_tmux
def test_bump_rechecks_authorisation_after_the_backstop(tmp_path: Path) -> None:
    stale = "ee" * 32

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        assert h.store.begin_attempt(Attempt(ask_id, stale, "approve", "op", "marmot:" + ref_id(stale), D,
                                             None))
        edit(tmp_path, read="wait")
        mid = await send(h, "!asks bump", None)
        await waiting(h)
        h.daemon.latch("test latch")
        go_file(h).touch()
        await settled(h, mid)
        assert audited(h, kind="drop", message_id=ref_id(mid), what="command")
        assert cmd_reply(h, mid) == "" and bump_rows(h, ask_id, "%") == []
    go(tmp_path, scenario)


@needs_tmux
def test_bump_from_a_non_operator_is_dropped(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        stranger = "f7" * 32
        await send(h, "!asks bump", None, stranger)
        await wait_until(lambda: audited(h, kind="drop", sender_prefix=stranger[:8],
                                         reason="sender is not an operator"))
        assert bump_rows(h, ask_id, "%") == []
    go(tmp_path, scenario)


@needs_tmux
def test_a_replayed_bump_queues_nothing_new(tmp_path: Path) -> None:
    """B7: the same inbound row run again queues no new reminder and no second summary."""
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        before = h.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        async with h.daemon.work_lock:
            await h.daemon.handle(mid, "!asks bump", None)
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == before
        assert len(bump_rows(h, ask_id, mid)) == 1
    go(tmp_path, scenario)


@needs_tmux
def test_a_failure_inside_the_bump_leaves_nothing(tmp_path: Path) -> None:
    """B7: the reminders, the summary and `done` commit together, or not at all."""
    async def scenario(h: Harness) -> None:
        a, _ = await card(h)
        q = await posted(h, question())
        await card_sent(h, q)
        touch = h.store.touch_ask

        def fail_on_second(ask_id: str, at: str) -> None:
            if ask_id == q:
                raise RuntimeError("store failure")
            touch(ask_id, at)
        h.store.touch_ask = fail_on_second  # type: ignore[method-assign]
        mid = await send(h, "!asks bump", None)
        await wait_until(lambda: audited(h, kind="handler", action="failed", error="RuntimeError"))
        assert bump_rows(h, a, mid) == [] and bump_rows(h, q, mid) == [] and cmd_reply(h, mid) == ""
        assert h.store.inbound_status(mid) == "executing"
    go(tmp_path, scenario)


# --- B6: a bump is not a card ---------------------------------------------------------------------------
def hint(ask_id: str) -> str:
    return (f"That was a reminder. React or reply on ask {ask_id}'s card "
            "(the message the reminder replies to).")


async def bumped(h: Harness, ask_id: str) -> str:
    """Send `!asks bump` and return the message ID of ask `ask_id`'s sent reminder."""
    command = await send(h, "!asks bump", None)
    await settled(h, command)
    await wait_until(lambda: sent_mid(h, f"asknote:{ask_id}:bump:{command}:0") is not None)
    reminder = sent_mid(h, f"asknote:{ask_id}:bump:{command}:0")
    assert reminder is not None
    return reminder


def untouched(h: Harness, ask_id: str, text: str) -> None:
    """Nothing decided, answered, noted, detailed or pasted for ask `ask_id`."""
    assert decisions(h) == [] and attempts(h, ask_id) == [] and h.store.answers(ask_id) == []
    assert ask_status(h, ask_id) == "open" and outbox_count(h, "askd:%") == 0
    assert f"echo: {text}" not in h.texts() and text not in pasted(h)


@needs_tmux
@pytest.mark.parametrize("text", ["approve", "deny too broad", "what is this", "!approve", "!deny no",
                                  "!details", "!details full"])
def test_a_reply_on_a_bump_gets_the_hint_and_nothing_else(tmp_path: Path, text: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        reminder = await bumped(h, ask_id)
        assert h.store.ask_for_message(reminder) is None
        mid = await send(h, text, reminder)
        await settled(h, mid)
        assert queued(h, mid) == hint(ask_id) and threads(h, mid) == {mid}   # the reply's thread (delta §5)
        assert h.store.inbound_status(mid) == "done" and outbox_count(h, f"cmd:{mid}:%") == 0
        assert audited(h, kind="ask", action="bump-hint", message_id=ref_id(mid), ask_id=ask_id)
        untouched(h, ask_id, text)
    go(tmp_path, scenario)


@needs_tmux
def test_a_reply_on_a_question_bump_is_not_an_answer(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        q = await posted(h, question())
        await card_sent(h, q)
        reminder = await bumped(h, q)
        assert await say(h, "the first one", reminder) == hint(q)
        untouched(h, q, "the first one")
    go(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("emoji", ["👍", "👎"])
def test_a_decision_emoji_on_a_bump_gets_the_hint(tmp_path: Path, emoji: str) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        reminder = await bumped(h, ask_id)
        mid, text = await reacted(h, emoji, reminder)
        assert text == hint(ask_id) and threads(h, mid) == {reminder}       # the reacted message (delta §5)
        untouched(h, ask_id, emoji)
    go(tmp_path, scenario)


@needs_tmux
def test_the_hint_threads_to_the_reacted_chunk_or_to_the_reply(tmp_path: Path) -> None:
    """B6, delta §5: on a split reminder, a reaction on chunk 1 gets its hint threaded to chunk 1, not chunk 0
    or the card; a reply on chunk 1 gets it threaded to the reply itself."""
    readout = {**LINES, "title": ["title:", f"  │ {'t' * 80}"], "description": LONG["description"]}

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        command = await send(h, "!asks bump", None)
        await settled(h, command)
        key = f"asknote:{ask_id}:bump:{command}:"
        await wait_until(lambda: sent_mid(h, key + "0") is not None and sent_mid(h, key + "1") is not None)
        chunk0, chunk1 = sent_mid(h, key + "0"), sent_mid(h, key + "1")
        assert chunk1 is not None and len({chunk0, chunk1, first}) == 3
        mid, text = await reacted(h, "👍", chunk1)
        assert text == hint(ask_id) and threads(h, mid) == {chunk1}
        reply = await send(h, "approve", chunk1)
        await settled(h, reply)
        assert queued(h, reply) == hint(ask_id) and threads(h, reply) == {reply}
        untouched(h, ask_id, "approve")
    go(tmp_path, scenario, {BEAD: bead(readout=readout)}, chunk_chars=200)


@needs_tmux
def test_another_emoji_on_a_bump_does_nothing(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        reminder = await bumped(h, ask_id)
        mid, text = await reacted(h, "🎉", reminder)
        assert text == "" and outbox_count(h, f"%{mid}%") == 0 and h.store.inbound_status(mid) == "done"
        assert audited(h, kind="event", action="ignored", what="reaction", message_id=f"r:{ref_id(mid[2:])}")
        untouched(h, ask_id, "🎉")
    go(tmp_path, scenario)


@needs_tmux
def test_independent_commands_on_a_bump_run_normally(tmp_path: Path) -> None:
    """B1: `!asks`, `!asks bump` and `!answer <id> <text>` keep their routing as replies to a bump."""
    async def scenario(h: Harness) -> None:
        await joined(h)
        q = await posted(h, question())
        await card_sent(h, q)
        reminder = await bumped(h, q)
        assert (await say(h, "!asks", reminder, tag="cmd")).startswith(f"{q} question")
        mid = await send(h, "!asks bump", reminder)
        await settled(h, mid)
        assert cmd_reply(h, mid) == f"Bumped 1 ask: {q}."
        assert await say(h, f"!answer {q} the first one", reminder) == f"Answer recorded for ask {q}."
        assert [a.text for a in h.store.answers(q)] == ["the first one"]
    go(tmp_path, scenario)


@needs_tmux
def test_a_reply_to_another_ask_notice_keeps_its_routing(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        q = await posted(h, question())
        await card_sent(h, q)
        assert (await h.daemon.on_ask(asks.AskCancel(q), NONE)).result == "ok"
        await wait_until(lambda: sent_mid(h, f"asknote:{q}:cancelled:0:0") is not None)
        notice = sent_mid(h, f"asknote:{q}:cancelled:0:0")
        assert notice is not None and h.store.bump_for_message(notice) is None
        await send(h, "thanks", notice)
        await h.until(lambda: "echo: thanks" in h.texts())                   # to the agent, as today
    go(tmp_path, scenario)


@needs_tmux
def test_a_bump_reply_from_a_stranger_or_while_latched_is_refused_as_any_message(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        reminder = await bumped(h, ask_id)
        stranger = "f7" * 32
        mid = await send(h, "approve", reminder, stranger)
        await wait_until(lambda: audited(h, kind="drop", sender_prefix=stranger[:8],
                                         reason="sender is not an operator"))
        assert h.store.inbound_status(mid) is None
        h.daemon.latch("test latch")
        mid = await send(h, "approve", reminder)
        r = await react(h, "👍", reminder)
        await wait_until(lambda: audited(h, kind="drop", operator="op", what="reaction", emoji="👍"))
        await stays(lambda: outbox_count(h, f"ask:{mid}:%") == 0 and outbox_count(h, f"ask:{r}:%") == 0)
        untouched(h, ask_id, "approve")
    go(tmp_path, scenario)


@needs_tmux
def test_a_replayed_reaction_on_a_bump_gets_no_second_hint(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        reminder = await bumped(h, ask_id)
        eid = "e3" * 32
        mid = await react(h, "👍", reminder, event=eid)
        await settled(h, mid)
        await react(h, "👎", reminder, event=eid)
        await wait_until(lambda: audited(h, kind="drop", reason="replayed reaction id", ref=ref_id(eid)))
        assert queued(h, mid) == hint(ask_id) and outbox_count(h, "ask:r:%") == 1
        untouched(h, ask_id, "👍")
    go(tmp_path, scenario)


# --- B11-B13: `!asks repeat` --------------------------------------------------------------------------
def repeat_rows(h: Harness, ask_id: str, command: str) -> list[tuple[str, str | None, str]]:
    return rows(h, f"askr:{ask_id}:{command}:%")


async def repeated_card(h: Harness, ask_id: str) -> tuple[str, str]:
    """Send `!asks repeat`, wait until ask `ask_id`'s repeat is wholly sent: (command, its chunk 0's ID)."""
    command = await send(h, "!asks repeat", None)
    await settled(h, command)
    parts = len(repeat_rows(h, ask_id, command))
    await wait_until(lambda: all(sent_mid(h, f"askr:{ask_id}:{command}:{i}") for i in range(parts)))
    first = sent_mid(h, f"askr:{ask_id}:{command}:0")
    assert first is not None
    return command, first


def fail_original(h: Harness, ask_id: str, part: int | None = None) -> None:
    """Mark the original card's chunk `part` (the last by default) failed, as the sender does after its
    retries."""
    row_ = h.store.ask(ask_id)
    assert row_ is not None
    last = row_.card_parts - 1 if part is None else part
    h.store.db.execute("UPDATE outbox SET status = 'failed', message_id = NULL WHERE key = ?",
                       (f"ask:{ask_id}:{last}",))


@needs_tmux
def test_repeat_reposts_every_open_card_top_level_from_its_stored_chunks(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        a, _ = await card(h)
        q = await posted(h, question())
        await card_sent(h, q)
        deciding = await posted(h, question("Which port?"))
        set_status(h, deciding, "deciding")
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        assert cmd_reply(h, mid) == f"Repeated 2 asks: {a}, {q}.\n{deciding} is deciding"
        approval, question_row = h.store.ask(a), h.store.ask(q)
        assert approval is not None and question_row is not None
        assert [t for _, _, t in repeat_rows(h, a, mid)] == asks.stored_details(approval)
        assert [t for _, _, t in repeat_rows(h, q, mid)] == chunk.split(asks.full_text(question_row),
                                                                         h.settings.chunk_chars)
        assert {r for _, r, _ in repeat_rows(h, a, mid) + repeat_rows(h, q, mid)} == {None}   # top-level
        assert repeat_rows(h, deciding, "%") == []
        assert h.store.db.execute("SELECT ask_id, parts FROM ask_repeats WHERE request = ? ORDER BY rowid",
                                  (mid,)).fetchall() == [(a, len(asks.stored_details(approval))),
                                                         (q, len(repeat_rows(h, q, mid)))]
        assert audited(h, kind="command", message_id=ref_id(mid), command="asks", sub="repeat",
                       bumped=[a, q], skipped=[{"ask_id": deciding, "why": "deciding"}])
        before = h.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        async with h.daemon.work_lock:                                         # B7: a replay
            await h.daemon.handle(mid, "!asks repeat", None)
        assert h.store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == before
    go(tmp_path, scenario)


@needs_tmux
def test_an_approve_reaction_on_a_repeat_approves(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        _, first = await repeated_card(h, ask_id)
        mid, text = await reacted(h, "👍", first)
        assert text.startswith(f"Approved {BEAD} as op") and threads(h, mid) == {first}
        assert ask_status(h, ask_id) == "approved" and len(decisions(h)) == 1
    go(tmp_path, scenario)


@needs_tmux
def test_a_deny_reply_on_a_repeat_denies(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        command, _ = await repeated_card(h, ask_id)
        last = sent_mid(h, f"askr:{ask_id}:{command}:{len(repeat_rows(h, ask_id, command)) - 1}")
        assert last is not None
        assert await say(h, "deny no", last) == f"Denied {BEAD} as op (via Marmot)."
        assert ask_status(h, ask_id) == "denied" and decisions(h)[0][-2:] == ["--deny", "--note=no"]
    go(tmp_path, scenario)


@needs_tmux
def test_a_failed_card_is_decidable_from_one_wholly_sent_repeat(tmp_path: Path) -> None:
    """B12: with the original's last chunk failed, an approve on the repeat is refused while one of its
    chunks is pending and accepted once it is wholly sent."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        fail_original(h, ask_id)
        assert await say(h, "approve", first) == NOT_DELIVERED.format(ask_id=ask_id)
        gate = asyncio.Event()

        def hold_after_first(req: dict[str, Any]) -> None:
            if req["idempotency_key"].startswith("askr:") and req["idempotency_key"].endswith(":0"):
                h.fake.send_gate = gate
        h.fake.on_send = hold_after_first
        command = await send(h, "!asks repeat", None)
        await wait_until(lambda: sent_mid(h, f"askr:{ask_id}:{command}:0") is not None)
        repeat = sent_mid(h, f"askr:{ask_id}:{command}:0")
        assert len(repeat_rows(h, ask_id, command)) > 1
        assert await say(h, "approve", repeat) == NOT_DELIVERED.format(ask_id=ask_id)
        h.fake.on_send = None
        h.fake.send_gate = None
        gate.set()
        await wait_until(lambda: h.store.card_delivered(ask_id))
        assert (await say(h, "approve", repeat)).startswith(f"Approved {BEAD} as op")
        assert len(decisions(h)) == 1
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=500)


@needs_tmux
def test_a_reply_on_a_repeat_of_a_decided_ask_gets_the_r31_answer(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        _, repeat = await repeated_card(h, ask_id)
        assert (await say(h, "approve", first)).startswith(f"Approved {BEAD}")
        assert await say(h, "approve", repeat) == f"Ask {ask_id} is already approved by op. Nothing recorded."
        assert len(decisions(h)) == 1
    go(tmp_path, scenario)


@needs_tmux
def test_a_repeat_does_not_replace_a_legacy_asks_details(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        legacy(h, ask_id)
        _, repeat = await repeated_card(h, ask_id)
        assert await say(h, "approve", repeat) == NEEDS_DETAILS.format(ask_id=ask_id)
        assert decisions(h) == []
    go(tmp_path, scenario)


@needs_tmux
def test_competing_decisions_on_the_card_and_a_repeat_record_one(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        _, repeat = await repeated_card(h, ask_id)
        edit(tmp_path, read="wait")
        on_card = await send(h, "approve", first)
        await waiting(h)
        on_repeat = await react(h, "👍", repeat)
        edit(tmp_path, read=None)
        go_file(h).touch()
        await settled(h, on_card)
        await settled(h, on_repeat)
        assert len(decisions(h)) == 1 and ask_status(h, ask_id) == "approved"
        assert queued(h, on_card).startswith(f"Approved {BEAD}")
        assert queued(h, on_repeat) == f"Ask {ask_id} is already approved by op. Nothing recorded."
    go(tmp_path, scenario)


@needs_tmux
def test_repeated_approval_chunks_survive_a_chunk_size_change(tmp_path: Path) -> None:
    """B12: the stored checked chunks are reused as they are, whatever chunk_chars is now."""
    ids: list[str] = []

    async def first_run(h: Harness) -> None:
        ask_id, _ = await card(h)
        ids.append(ask_id)
    go(tmp_path, first_run, {BEAD: bead(readout={**LINES, **LONG})})

    async def smaller(h: Harness) -> None:
        h.seq += 1000                       # past the first run's message IDs, which a restart remembers
        stored = h.store.ask(ids[0])
        assert stored is not None
        assert chunk.split("".join(asks.stored_details(stored)), 200) != asks.stored_details(stored)
        mid = await send(h, "!asks repeat", None)
        await settled(h, mid)
        assert [t for _, _, t in repeat_rows(h, ids[0], mid)] == asks.stored_details(stored)
    go(tmp_path, smaller, fresh=False, chunk_chars=200)


@needs_tmux
def test_bumps_thread_to_the_repeat_until_the_original_arrives(tmp_path: Path) -> None:
    """B13: with the original's chunk 0 undelivered, bumps thread to the earliest wholly sent repeat (never
    to a later one or a bump); once the original is sent, late, to the original."""
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        fail_original(h, ask_id, 0)
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        assert cmd_reply(h, mid) == f"Bumped 0 asks.\n{ask_id}: card not delivered; try !asks repeat"
        assert bump_rows(h, ask_id, "%") == []
        _, earliest = await repeated_card(h, ask_id)
        await repeated_card(h, ask_id)
        for _ in range(2):
            mid = await send(h, "!asks bump", None)
            await settled(h, mid)
            assert {r for _, r, _ in bump_rows(h, ask_id, mid)} == {earliest}
            await wait_until(lambda m=mid: sent_mid(h, f"asknote:{ask_id}:bump:{m}:0") is not None)
        h.store.db.execute("UPDATE outbox SET status = 'sent', message_id = ? WHERE key = ?",
                           (first, f"ask:{ask_id}:0"))
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        assert {r for _, r, _ in bump_rows(h, ask_id, mid)} == {first}
    go(tmp_path, scenario)


# --- B14: the activity clock moves with accepted activity ----------------------------------------------
class Clock:
    """A fake `asks.now`: starts at START and moves only when told to."""
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at

    def advance(self, hours: float) -> str:
        self.at += timedelta(hours=hours)
        return asks.stamp(self.at)


START = datetime.fromisoformat("2026-10-07T00:00:00+00:00")


def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(asks, "now", c)
    return c


@needs_tmux
def test_accepted_activity_on_an_approval_ask_moves_its_clock(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        assert activity(h.store, ask_id) == asks.stamp(START)               # set when posted
        at = c.advance(1)
        assert (await say(h, "looks fine to me", first)).startswith("Noted")
        assert activity(h.store, ask_id) == at                              # a note
        at = c.advance(1)
        request = await send(h, "!details", first)
        await settled(h, request)
        assert activity(h.store, ask_id) == at                              # `!details`
        at = c.advance(1)
        await reacted(h, "🎉", first)
        assert activity(h.store, ask_id) == at                              # an accepted, ignored reaction
        at = c.advance(1)
        reminder = await bumped(h, ask_id)
        assert activity(h.store, ask_id) == at                              # a manual bump, when queued
        c.advance(1)
        assert await say(h, "approve", reminder) == hint(ask_id)
        assert (await reacted(h, "👍", reminder))[1] == hint(ask_id)
        assert await say(h, "approve\x07", reminder, tag="refused") == CONTROL_REFUSED
        assert activity(h.store, ask_id) == at                              # a reminder is not a card
        at = c.advance(1)
        _, repeat = await repeated_card(h, ask_id)
        assert activity(h.store, ask_id) == at                              # a repeat, when queued
        stranger = "f7" * 32
        before = c.advance(1)
        await react(h, "👍", first, stranger)
        await wait_until(lambda: audited(h, kind="drop", sender_prefix=stranger[:8], what="reaction"))
        assert activity(h.store, ask_id) == at                              # a stranger's: dropped
        eid = "e4" * 32
        mid = await react(h, "👎", repeat, event=eid)
        await settled(h, mid)
        at = before
        assert activity(h.store, ask_id) == at                              # a deny attempt, on a repeat
        c.advance(1)
        await react(h, "👍", first, event=eid)                              # the replayed reaction
        await wait_until(lambda: audited(h, kind="drop", reason="replayed reaction id", ref=ref_id(eid)))
        assert activity(h.store, ask_id) == at
    go(tmp_path, scenario)


@needs_tmux
def test_answers_and_refusals_on_a_question_move_its_clock(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        await joined(h)
        q = await posted(h, question())
        first = await card_sent(h, q)
        at = c.advance(1)
        assert await say(h, "the first one", first) == f"Answer recorded for ask {q}."
        assert activity(h.store, q) == at
        at = c.advance(1)
        assert (await say(h, f"!answer {q} or the second", None)).startswith(f"Added to ask {q}")
        assert activity(h.store, q) == at
        at = c.advance(1)
        assert (await say(h, " ", first)).startswith("Not recorded")
        assert activity(h.store, q) == at                                   # accepted, then refused
        c.advance(1)
        assert (await say(h, "!asks", None, tag="cmd")).startswith(q)       # a listing is not activity
        assert activity(h.store, q) == at
    go(tmp_path, scenario)


@needs_tmux
def test_a_refused_approve_moves_the_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        fail_original(h, ask_id)
        at = c.advance(1)
        assert await say(h, "approve", first) == NOT_DELIVERED.format(ask_id=ask_id)
        assert activity(h.store, ask_id) == at
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=500)


@needs_tmux
def test_commands_on_a_card_leave_its_clock_but_parse_errors_move_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B14 (review r2): `!asks` and `!ps` as replies to the original card, its `!details` or a repeat are not
    about the ask and leave its clock; a parse error replying to each is refused activity and moves it."""
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        request = await send(h, "!details", first)
        await settled(h, request)
        await wait_until(lambda: sent_mid(h, f"askd:{ask_id}:{request}:0") is not None)
        details = sent_mid(h, f"askd:{ask_id}:{request}:0")
        _, repeat = await repeated_card(h, ask_id)
        assert details is not None and len({first, details, repeat}) == 3
        for target in (first, details, repeat):
            at = activity(h.store, ask_id)
            c.advance(1)
            assert (await say(h, "!asks", target, tag="cmd")).startswith(ask_id)
            mid = await send(h, "!ps", target)
            await settled(h, mid)
            assert queued(h, mid, "cmd") != ""
            assert activity(h.store, ask_id) == at                          # not about the ask
            at = c.advance(1)
            assert (await say(h, "!answer", target, tag="cmd")).startswith("Usage: !answer")
            assert activity(h.store, ask_id) == at                          # a parse error on it
    go(tmp_path, scenario)


# Each accepted refusal on an identified ask (B14), as (the ask's kind, how it is refused on `first`, the
# card's chunk 0). Each asserts its own refusal, so a route that stops refusing fails rather than passes.
async def answer_on_an_approval(h: Harness, ask_id: str, first: str) -> None:
    text = await say(h, f"!answer {ask_id} hi", None, tag="cmd")
    assert text == APPROVAL_NOT_ANSWERED.format(ask_id=ask_id)


async def oversized_deny_command(h: Harness, ask_id: str, first: str) -> None:
    text = await say(h, f"!deny {BEAD} " + "x" * (commands.MAX_REASON + 1), first, tag="cmd")
    assert "the reason is at most" in text


async def oversized_deny_reply(h: Harness, ask_id: str, first: str) -> None:
    assert await say(h, "deny " + "x" * (commands.MAX_REASON + 1), first) == REASON_TOO_LONG


async def mismatched_bead(h: Harness, ask_id: str, first: str) -> None:
    assert await say(h, "!approve hz-nope", first) == MISMATCH.format(bead=BEAD)


async def approve_on_a_question(h: Harness, ask_id: str, first: str) -> None:
    assert await say(h, "!approve", first) == NOT_A_CARD_REPLY


async def not_delivered(h: Harness, ask_id: str, first: str) -> None:
    fail_original(h, ask_id)
    assert await say(h, "approve", first) == NOT_DELIVERED.format(ask_id=ask_id)


async def needs_details(h: Harness, ask_id: str, first: str) -> None:
    await wait_until(lambda: h.store.card_delivered(ask_id))
    h.store.db.execute("UPDATE asks SET truncated = 1 WHERE ask_id = ?", (ask_id,))
    assert await say(h, "approve", first) == NEEDS_DETAILS.format(ask_id=ask_id)


async def not_configured(h: Harness, ask_id: str, first: str) -> None:
    await wait_until(lambda: h.store.card_delivered(ask_id))
    h.daemon.approve_bead = None
    assert await say(h, "approve", first) == ASK_NO_APPROVALS


async def already_decided(h: Harness, ask_id: str, first: str) -> None:
    set_status(h, ask_id, "approved")
    assert (await say(h, "approve", first)).startswith(f"Ask {ask_id} is already approved")
    set_status(h, ask_id, "open")                       # so the deadline below can be observed


async def control_characters(h: Harness, ask_id: str, first: str) -> None:
    assert await say(h, "approve\x07", first, tag="refused") == CONTROL_REFUSED


async def control_character_reaction(h: Harness, ask_id: str, first: str) -> None:
    mid, text = await reacted(h, "👍\x07", first)
    assert text == "" and attempts(h, ask_id) == []


REFUSALS = {
    "!answer on an approval": ("approval", answer_on_an_approval),
    "oversized !deny": ("approval", oversized_deny_command),
    "oversized deny reply": ("approval", oversized_deny_reply),
    "MISMATCH": ("approval", mismatched_bead),
    "NOT_A_CARD_REPLY": ("question", approve_on_a_question),
    "not delivered": ("approval", not_delivered),
    "needs details": ("approval", needs_details),
    "not configured": ("approval", not_configured),
    "already decided": ("approval", already_decided),
    "control characters": ("approval", control_characters),
    "control-character reaction": ("approval", control_character_reaction),
}


@needs_tmux
@pytest.mark.parametrize("route", list(REFUSALS))
def test_an_accepted_refusal_moves_the_reminder_deadline(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    """B14: a refusal at hour 10 on an identified ask moves its clock, so it is first auto-bumped at hour
    22, not at hour 12 (review r1)."""
    c = clock(monkeypatch)
    kind, refuse = REFUSALS[route]

    async def scenario(h: Harness) -> None:
        if kind == "approval":
            ask_id, first = await card(h)
        else:
            await joined(h)
            ask_id = await posted(h, question())
            first = await card_sent(h, ask_id)
        at = c.advance(10)
        await refuse(h, ask_id, first)
        assert activity(h.store, ask_id) == at
        c.advance(2)
        await check(h)
        assert auto_rows(h, ask_id) == []                                   # 12 h after the post: not due
        c.advance(10)
        await check(h)
        assert len(auto_rows(h, ask_id)) == 1                               # 12 h after the refusal
    go(tmp_path, scenario, {BEAD: bead(readout={**LINES, **LONG})}, chunk_chars=500)


# --- B14: automatic bumps ------------------------------------------------------------------------------
def auto_rows(h: Harness, ask_id: str) -> list[tuple[str, str | None, str]]:
    return rows(h, f"asknote:{ask_id}:autobump:%")


async def check(h: Harness) -> None:
    """One automatic-bump check, as the loop runs it."""
    async with h.daemon.work_lock:
        h.daemon.autobump()


@needs_tmux
def test_an_open_ask_is_bumped_once_it_has_been_quiet_for_ask_bump_hours(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        await joined(h)
        q = await posted(h, question())
        first = await card_sent(h, q)
        others = [await posted(h, question(t)) for t in ("Which host?", "Which port?", "Which user?")]
        for other in others:
            await card_sent(h, other)
        assert (await say(h, f"!answer {others[0]} the first one", None)).startswith("Answer")
        set_status(h, others[1], "deciding")
        set_status(h, others[2], "uncertain")
        c.advance(12 - 1 / 60)
        await check(h)
        assert auto_rows(h, q) == []
        at = c.advance(1 / 60)
        await check(h)
        assert [(k, r) for k, r, _ in auto_rows(h, q)] == [(f"asknote:{q}:autobump:1:0", first)]
        assert auto_rows(h, q)[0][2] == (f"Still outstanding (automatic reminder, every 12 h): ask {q} · "
                                         f"question · 12h · Which relay?.\n{ANSWER_TAIL}")
        assert activity(h.store, q) == at
        for other in others:
            assert auto_rows(h, other) == []
        assert audited(h, kind="ask", action="autobump", bumped=[q], skipped=[])
        await check(h)                                                      # the clock moved: not due
        assert len(auto_rows(h, q)) == 1
        await wait_until(lambda: h.store.bump_for_message(sent_mid(h, f"asknote:{q}:autobump:1:0")) == q)
        c.advance(6)
        assert (await say(h, "or the second", first)).startswith("Answer")    # activity resets it ...
        set_status(h, q, "open")
        c.advance(11)
        await check(h)
        assert len(auto_rows(h, q)) == 1
        c.advance(1)                                                         # ... to 12 h after the reply
        await check(h)
        assert [k for k, _, _ in auto_rows(h, q)] == [f"asknote:{q}:autobump:{n}:0" for n in (1, 2)]
        assert {r for _, r, _ in auto_rows(h, q)} == {first}                 # never to a reminder (B3)
        assert outbox_count(h, "cmd:%") == 0                                 # no summary is posted
    go(tmp_path, scenario)


@needs_tmux
def test_ask_bump_hours_zero_disables_automatic_bumps(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        c.advance(24 * 30)
        await check(h)
        assert auto_rows(h, ask_id) == [] and activity(h.store, ask_id) == asks.stamp(START)
    go(tmp_path, scenario, ask_bump_hours=0)


@needs_tmux
def test_a_latch_or_an_unverified_group_defers_without_moving_the_clock(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        c.advance(13)
        async with h.daemon.work_lock:
            h.daemon.group_ok = False
            h.daemon.autobump()
            h.daemon.group_ok = True
        assert auto_rows(h, ask_id) == [] and activity(h.store, ask_id) == asks.stamp(START)
        assert not audited(h, kind="ask", action="autobump")
        h.daemon.latch("test latch")
        await check(h)
        assert auto_rows(h, ask_id) == [] and activity(h.store, ask_id) == asks.stamp(START)
        assert not audited(h, kind="ask", action="autobump")
    go(tmp_path, scenario)


@needs_tmux
def test_a_latch_after_the_enqueue_is_left_to_the_outbound_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        c.advance(12)
        async with h.daemon.work_lock:
            h.daemon.autobump()
            h.daemon.latch("test latch")                # before the outbox loop can see the row
        key = f"asknote:{ask_id}:autobump:1:0"
        assert [k for k, _, _ in auto_rows(h, ask_id)] == [key]
        await stays(lambda: sent_mid(h, key) is None)
    go(tmp_path, scenario)


@needs_tmux
def test_a_crash_before_the_commit_leaves_nothing_then_the_next_check_bumps_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        c.advance(12)
        touch = h.store.touch_ask

        def crash(ask: str, at: str) -> None:
            raise RuntimeError("store failure")
        h.store.touch_ask = crash  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await check(h)
        assert auto_rows(h, ask_id) == [] and activity(h.store, ask_id) == asks.stamp(START)
        assert h.store.get("autobump_seq") is None
        h.store.touch_ask = touch  # type: ignore[method-assign]
        await check(h)
        await check(h)
        assert [(k, r) for k, r, _ in auto_rows(h, ask_id)] == [(f"asknote:{ask_id}:autobump:1:0", first)]
    go(tmp_path, scenario)


@needs_tmux
def test_a_pending_reminder_blocks_until_it_fails_and_a_restart_keeps_that(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = clock(monkeypatch)
    ids: list[str] = []

    async def first_run(h: Harness) -> None:
        ask_id, _ = await card(h)
        ids.append(ask_id)
        gate = asyncio.Event()
        h.fake.send_gate = gate                         # nothing more is sent
        mid = await send(h, "!asks bump", None)
        await settled(h, mid)
        assert len(bump_rows(h, ask_id, mid)) == 1      # pending behind the gate
        at = asks.stamp(c.at)
        c.advance(13)
        await check(h)
        assert auto_rows(h, ask_id) == [] and activity(h.store, ask_id) == at
        assert audited(h, kind="ask", action="autobump", bumped=[],
                       skipped=[{"ask_id": ask_id, "why": "pending delivery"}])
        gate.set()
    go(tmp_path, first_run)

    async def restarted(h: Harness) -> None:
        h.seq += 1000
        ask_id = ids[0]
        h.store.db.execute("UPDATE outbox SET status = 'failed' WHERE key LIKE ?",
                           (f"asknote:{ask_id}:bump:%",))
        await check(h)
        assert [k for k, _, _ in auto_rows(h, ask_id)] == [f"asknote:{ask_id}:autobump:1:0"]
    go(tmp_path, restarted, fresh=False)

    async def again(h: Harness) -> None:
        await check(h)
        assert len(auto_rows(h, ids[0])) == 1
    go(tmp_path, again, fresh=False)


@needs_tmux
def test_after_a_long_outage_each_ask_is_bumped_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop itself, after a restart with clocks 30 h old: one bump per ask, then none."""
    c = clock(monkeypatch)
    ids: list[str] = []

    async def first_run(h: Harness) -> None:
        await joined(h)
        for title in ("Which relay?", "Which host?"):
            ids.append(await posted(h, question(title)))
            await card_sent(h, ids[-1])
    go(tmp_path, first_run, ask_bump_hours=48)

    c.advance(30)
    monkeypatch.setattr(daemon_mod, "AUTOBUMP_SECONDS", 0.01)

    async def restarted(h: Harness) -> None:
        for ask_id in ids:
            await wait_until(lambda a=ask_id: len(auto_rows(h, a)) == 1)
        await stays(lambda: all(len(auto_rows(h, a)) == 1 for a in ids))
    go(tmp_path, restarted, fresh=False)

    async def again(h: Harness) -> None:
        await stays(lambda: all(len(auto_rows(h, a)) == 1 for a in ids))
    go(tmp_path, again, fresh=False)


@needs_tmux
@pytest.mark.parametrize("how", ["approve", "!approve", "👍", "👎"])
def test_b6_applies_to_an_automatic_bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    c = clock(monkeypatch)

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        c.advance(12)
        await check(h)
        key = f"asknote:{ask_id}:autobump:1:0"
        await wait_until(lambda: sent_mid(h, key) is not None)
        reminder = sent_mid(h, key)
        assert reminder is not None
        await wait_until(lambda: h.store.bump_for_message(reminder) == ask_id)    # marked sent
        assert h.store.ask_for_message(reminder) is None
        if how.startswith(("a", "!")):
            assert await say(h, how, reminder) == hint(ask_id)
        else:
            assert (await reacted(h, how, reminder))[1] == hint(ask_id)
        untouched(h, ask_id, how)
    go(tmp_path, scenario)
