"""Question admission boundaries over real SQLite and the daemon, without external processes."""

import asyncio
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX
from test_admind_asks import NONE, merge, question, row
from test_admind_r1 import Unit, run

from heterodyne.admind import asks
from heterodyne.admind.daemon import ASK_TOO_MANY
from heterodyne.admind.store import Store


@pytest.fixture
def unit(tmp_path: Path):
    value = Unit(tmp_path)
    yield value
    value.store.close()


def seed_questions(store: Store, count: int, *, kind: str = "question", status: str = "open") -> None:
    alphabet = asks.ID_ALPHABET
    for index in range(count):
        ask_id = f"q{alphabet[index // len(alphabet)]}{alphabet[index % len(alphabet)]}2"
        store.insert_ask(row(ask_id, kind=kind, status=status), None)


def test_hundredth_question_is_admitted_and_next_refused_without_effects(unit) -> None:
    assert asks.MAX_UNANSWERED_QUESTIONS == 100
    seed_questions(unit.store, 99)

    async def scenario():
        hundredth = await unit.daemon.on_ask(question(), NONE)
        assert hundredth.result == "posted"
        assert unit.store.unanswered_question_count() == 100
        before_asks = unit.store.db.execute("SELECT * FROM asks ORDER BY ask_id").fetchall()
        before_outbox = unit.store.db.execute("SELECT * FROM outbox ORDER BY seq").fetchall()
        rejected = await unit.daemon.on_ask(question(), NONE)
        assert rejected == asks.refused(ASK_TOO_MANY)
        assert "100 unanswered questions" in rejected.message
        assert unit.store.db.execute("SELECT * FROM asks ORDER BY ask_id").fetchall() == before_asks
        assert unit.store.db.execute("SELECT * FROM outbox ORDER BY seq").fetchall() == before_outbox

    run(scenario())


@pytest.mark.parametrize("kind,status", [
    ("question", "answered"),
    ("question", "deciding"),
    ("question", "uncertain"),
    ("question", "cancelled"),
    ("question", "approved"),
    ("question", "denied"),
    ("question", "stale"),
    ("question", "superseded"),
    ("question", "blocked"),
    ("merge", "open"),
    ("approval", "open"),
])
def test_other_asks_do_not_consume_question_slots(unit, kind: str, status: str) -> None:
    seed_questions(unit.store, 101, kind=kind, status=status)
    assert unit.store.unanswered_question_count() == 0

    async def scenario():
        assert (await unit.daemon.on_ask(question(), NONE)).result == "posted"
        assert unit.store.unanswered_question_count() == 1
        assert len(unit.store.asks_with_status(status)) >= 101

    run(scenario())


def test_operator_answer_immediately_releases_a_question_slot(unit) -> None:
    seed_questions(unit.store, 100)

    async def scenario():
        assert (await unit.daemon.on_ask(question(), NONE)).result == "refused"
        mid = unit.mid()
        assert unit.store.claim_inbound(mid, sender_key=OPERATOR_HEX)
        await unit.daemon.ask_reply(mid, "q222", "yes")
        assert unit.store.ask("q222").status == "answered"
        assert [answer.text for answer in unit.store.answers("q222")] == ["yes"]
        assert unit.store.unanswered_question_count() == 99
        assert (await unit.daemon.on_ask(question(), NONE)).result == "posted"
        assert unit.store.unanswered_question_count() == 100
        assert [answer.text for answer in unit.store.answers("q222")] == ["yes"]

    run(scenario())


def test_concurrent_questions_compete_for_the_last_slot(unit) -> None:
    seed_questions(unit.store, 99)

    async def scenario():
        replies = await asyncio.gather(
            unit.daemon.on_ask(question(title="first"), NONE),
            unit.daemon.on_ask(question(title="second"), NONE),
        )
        assert sorted(reply.result for reply in replies) == ["posted", "refused"]
        assert next(reply for reply in replies if reply.result == "refused") == asks.refused(ASK_TOO_MANY)
        assert unit.store.unanswered_question_count() == 100

    run(scenario())


def test_merge_admission_is_not_limited_by_unanswered_questions(unit) -> None:
    seed_questions(unit.store, 100)

    async def scenario():
        assert (await unit.daemon.on_ask(merge(), NONE)).result == "posted"
        assert unit.store.unanswered_question_count() == 100

    run(scenario())
