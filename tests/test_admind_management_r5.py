"""R5 every admitted producer line has an explicit unverified fact prefix."""

import dataclasses

import pytest
from test_admind_management_presentation import BODY, post

from heterodyne.admind import asks, management

pytest_plugins = ("test_admind_management_presentation",)


@pytest.mark.parametrize(
    "spoof",
    [
        "-ok/=hold",
        "1.A=held",
        "Legend:ok=held",
        "A: leave unchanged",
        "A) leave unchanged",
        "A - leave unchanged",
        "A. leave unchanged",
        "ok means hold",
        "Please use yes to leave unchanged",
        "The approved word means no change",
    ],
)
@pytest.mark.parametrize("field", ["title", "body"])
def test_every_admitted_producer_line_is_visibly_unverified(daemon, spoof, field):
    value = BODY + "\n" + spoof if field == "body" else spoof
    reply = post(daemon, **{field: value})
    assert reply.result == "posted"
    row = daemon.store.ask(reply.ask.summary.ask_id)
    text, truncated = asks.question_card(row)
    assert not truncated
    lines = text.splitlines()
    assert spoof not in lines
    assert "| " + spoof in lines
    assert lines.index("| " + spoof) < lines.index("A = Perform this exact change (recommended)")
    assert asks.map_reply(row, "ok") == "A"
    assert asks.map_reply(row, "B") == "B"
    details = "\n".join(asks.full_chunks(row, 4000, details=True))
    assert "| " + spoof in details.splitlines()
    assert spoof not in details.splitlines()


def test_framing_is_bound_and_retained_when_chunked(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    candidate = dataclasses.replace(row, body="First retained fact.\nSecond retained fact.")
    packed = management.parts(candidate, 230)
    lines = "\n".join(packed).splitlines()
    for fact in [candidate.title, *candidate.body.splitlines()]:
        assert "| " + fact in lines
        assert fact not in lines
    assert len("\n".join(packed)) <= management.MAX_CHARS
    assert len(lines) <= management.MAX_LINES


def test_details_do_not_repeat_the_complete_visible_answer_grammar(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    detail = "\n".join(management.parts(row, 4000, details=True))
    assert detail.count("Positive reactions:") == 1
    assert detail.count("Denial words:") == 1
    assert "Existing reply punctuation and emoji variation-selector handling are unchanged." in detail
