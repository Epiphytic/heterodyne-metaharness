"""R3 consent disclosure and bounded details, using disposable native-owned fixtures."""

import dataclasses
import json
from unittest.mock import Mock

import msgspec
import pytest
from fakes.settings import OPERATOR_HEX, operator
from test_admind_management_presentation import BODY, post

from heterodyne.admind import asks, binding, cli, ctl, management, verbs

pytest_plugins = ("test_admind_management_presentation",)


@pytest.mark.parametrize("word", sorted(verbs.APPROVE_WORDS))
def test_card_discloses_every_word_that_records_approval(daemon, word):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    card = "\n".join(management.lines(row))
    legend = next(line for line in card.splitlines() if line.startswith("A /"))
    assert word in legend.split(" =", 1)[0].split(" / ")
    assert asks.map_reply(row, word) == "A"
    assert "Positive reactions: 👍 (all skin tones), ✅, ❤, ♥ = approve." in card
    assert all(asks.map_reaction(row, emoji) == "A" for emoji in verbs.APPROVE_EMOJIS)


def test_card_discloses_denial_grammar_without_changing_native_choices(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    card = "\n".join(management.lines(row))
    assert "deny / 👎 ❌ = none of these; request stays held." in card
    assert "denied, reject, rejected (optional reason); no or n alone = none of these." in card
    for text in ["deny: unsafe", "denied", "reject - unsafe", "rejected", "no", "n", "👎🏽 unsafe"]:
        assert asks.map_reply(row, text) == "none"
    assert asks.map_reply(row, "no idea") is None


@pytest.mark.parametrize("first", ["B", "deny", "no", "n", "👎", "❌", "Needs clarification"])
def test_negative_then_a_retains_complete_history_without_claiming_authorization(daemon, first):
    ask_id = post(daemon).ask.summary.ask_id
    daemon.operators = {OPERATOR_HEX: "op"}
    daemon.finish = Mock()
    for (seq,) in daemon.store.db.execute("SELECT seq FROM outbox"):
        daemon.store.mark_sent(seq, "ab" * 32)
    for mid, text in [("cd" * 32, first), ("ef" * 32, "A")]:
        daemon.store.claim_inbound(mid, sender_key=OPERATOR_HEX)
        daemon.answer_ask(mid, ask_id, text)
    native = binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))
    assert isinstance(native.binding, binding.EnvelopeV2)
    assert native.binding.answer_history_complete and native.binding.delivered
    assert [answer.text for answer in native.binding.answers] == [first, "A"]
    assert native.binding.answers[0].choice != "A"
    assert native.binding.answers[1].choice == "A"
    assert all(answer.current_policy for answer in native.binding.answers)
    confirmation = daemon.finish.call_args.args[2]
    assert "Recorded for" in confirmation
    assert "Recorded answer is not authorization" in confirmation
    assert "queue verifies complete history and holds on any contrary reply" in confirmation
    card = "\n".join(management.lines(daemon.store.ask(ask_id)))
    assert "a later A cannot override" not in card
    assert "Queue verifies complete history; any contrary or ambiguous answer holds this request." in card


def test_recorded_legacy_confirmation_is_byte_unchanged(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    legacy = dataclasses.replace(row, presentation=None, render_version=None, management=None)
    assert asks.recorded(legacy, "A", "ok") == (
        f"Recorded for {legacy.ask_id}: A = Perform this exact change (from ok)."
    )


@pytest.mark.parametrize("version", [1, 2])
def test_explicit_binding_version_two_rejects_successful_legacy_envelope(
    daemon, monkeypatch, capsys, version
):
    posted = daemon.post_ask(asks.AskPost("question", "Legacy", BODY, form="yes-no"), ctl.Peer(123))
    ask_id = posted.ask.summary.ask_id
    native = binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))
    assert isinstance(native.binding, binding.Envelope)

    async def rpc(path, req, timeout, **kwargs):
        return msgspec.json.decode(msgspec.json.encode(native), type=kwargs["reply_type"])

    monkeypatch.setattr(cli.ctl, "request", rpc)
    status = cli.ask_binding_command(daemon.s, ask_id, version=version)
    raw = json.loads(capsys.readouterr().out)
    if version == 2:
        assert status == 1
        assert raw["result"] == "refused" and raw["code"] == "invalid-reply"
        assert raw["binding"] is None and raw["message"] == "ask.binding.v2 unavailable"
    else:
        assert status == 0
        assert raw == json.loads(msgspec.json.encode(native))


@pytest.mark.parametrize("bound", ["lines", "characters"])
def test_details_refuses_when_added_grammar_crosses_whole_packed_budget(daemon, bound):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    card = "\n".join(management.lines(row))
    extra = "\nFact." * (36 - len(card.splitlines())) if bound == "lines" else "x" * (3200 - len(card))
    row = dataclasses.replace(row, body=row.body + extra)
    normal = "\n".join(management.parts(row, 4000))
    assert len(normal) <= 3200 and len(normal.splitlines()) <= 36
    with pytest.raises(management.Refusal):
        management.parts(row, 4000, details=True)


def test_details_rechecks_redaction_after_final_native_packing(daemon, monkeypatch):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    original = management.chunk.pack

    def pack(lines, size):
        result = original(lines, size)
        if any(line.startswith("Existing reply punctuation") for line, _ in lines):
            result[-1] += "\nnsec1" + "q" * 58
        return result

    monkeypatch.setattr(management.chunk, "pack", pack)
    assert management.parts(row, 4000)
    with pytest.raises(management.Refusal):
        management.parts(row, 4000, details=True)


def test_details_budget_counts_actual_continuation_framing(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    details = "\n".join(management.parts(row, 4000, details=True))
    row = dataclasses.replace(row, body=row.body + "\nFact." * (36 - len(details.splitlines())))
    unframed = "\n".join(management.parts(row, 4000, details=True))
    assert len(unframed.splitlines()) == 36 and len(unframed) <= 3200
    actual = management.chunk.pack([(line, "atomic") for line in unframed.splitlines()], 170)
    assert len("\n".join(actual).splitlines()) > 36
    with pytest.raises(management.Refusal):
        management.parts(row, 170, details=True)
