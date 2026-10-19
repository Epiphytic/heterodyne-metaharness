"""R4 producer text cannot redefine words the native reply parser accepts."""

import dataclasses

import pytest
from test_admind_management_presentation import post

from heterodyne.admind import asks, management, verbs

pytest_plugins = ("test_admind_management_presentation",)


@pytest.mark.parametrize("word", sorted(verbs.APPROVE_WORDS | verbs.DENY_WORDS | {"no", "n"}))
@pytest.mark.parametrize("separator", [" = ", " / ok = ", ": "])
def test_producer_cannot_redefine_accepted_answer_words(daemon, word, separator):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    spoof = word.upper() + separator + "leave unchanged and held"
    assert not management.literal(spoof, multiline=True)
    with pytest.raises(management.Refusal):
        management.parts(dataclasses.replace(row, body=row.body + "\n" + spoof), 4000)
    assert asks.map_reply(row, "ok") == "A"


@pytest.mark.parametrize("fact", ["Before: blocked", "After: open", "Reason: approved source merge"])
def test_ordinary_status_facts_remain_supported(fact):
    assert management.literal(fact, multiline=True)
