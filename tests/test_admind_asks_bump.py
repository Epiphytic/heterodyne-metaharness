"""`!asks bump`, `!asks repeat` and automatic bumps (2026-10-07 design delta, B1-B14).

Every ID is an obvious fake. Ordering uses gates and bounded waits; B14's clock is `asks.now`, replaced
with a fake. Nothing here sleeps to order events.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from admind_asks_fixture import sent_mid
from admind_waits import wait_until
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
    say,
    send,
    settled,
    waiting,
)
from test_admind_asks import NONE, audited, card_sent, joined, outbox, pasted, posted, question, row
from test_admind_daemon import Harness, needs_tmux
from test_admind_reactions import OLD_TABLES, columns
from test_admind_settings import BASE_CONFIG, write

from heterodyne.admind import asks, chunk, commands
from heterodyne.admind import store as store_mod
from heterodyne.admind.approvals import Attempt
from heterodyne.admind.audit import ref_id
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


def test_the_migration_starts_existing_clocks_at_the_migration_time(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
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
