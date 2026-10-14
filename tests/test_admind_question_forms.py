"""cxmkf question forms (design §2.1): `check()` (K13, K15), the K14 grace window, rendering and the split
(K16, K23), reaction and reply mapping (K17q, K18q), recording (K19), attribution, and the separation of
answers from approval decisions (K19b).

approve-bead is always the fake; nothing here runs the real one, btq or a live model.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import msgspec
import pytest
from admind_asks_fixture import btq_log, sent_mid
from admind_waits import wait_until
from fakes.settings import OPERATOR_HEX
from test_admind_approvals import BEAD, TOKEN, go, send, settled
from test_admind_asks import NONE, card_sent, joined, merge, posted, question, row, via_main
from test_admind_daemon import Harness, needs_tmux, run_with
from test_admind_reactions import react, reacted, threads

from heterodyne.admind import asks, chunk, cli, verbs
from heterodyne.admind.notices import NoticePost
from heterodyne.admind.store import AskRow

THREE = ["rerun on bel-ci", "skip the check", "ask the lead"]
TONES = list(verbs.SKIN_TONES)
APPROVE_SET = sorted(verbs.APPROVE_EMOJIS) + ["❤️"]         # with the variation selector too
DENY_SET = sorted(verbs.DENY_EMOJIS)
OTHER_EMOJI = "🎉"


def six_long() -> list[str]:
    """Six 80-character choices that redaction leaves alone (no hex runs)."""
    return [(f"choice {n} " + "relay zone " * 8)[:80] for n in range(1, 7)]


def choices(texts: list[str] | None = None, recommended: str | None = "A", **kw: Any) -> asks.AskPost:
    return question(form="choices", choices=THREE if texts is None else texts, recommended=recommended,
                    **kw)


def yes_no(recommended: str | None = None, **kw: Any) -> asks.AskPost:
    return question(form="yes-no", recommended=recommended, **kw)


def form_row(req: asks.AskPost, **kw: Any) -> AskRow:
    """The stored row of a question post, as `post_ask` would build it."""
    question_form, stored = asks.stored_form(req)
    return row(body=kw.pop("body", req.body), question_form=question_form, choices=stored,
               links=asks.stored_links(req.links), **kw)


MC, YN, YN_NO, BARE = form_row(choices()), form_row(yes_no()), form_row(yes_no("no")), row()


# --- check() (K13, K15) --------------------------------------------------------------------------
@pytest.mark.parametrize("n", range(asks.MIN_CHOICES, asks.MAX_CHOICES + 1))
def test_two_to_six_choices_with_one_recommended_are_accepted(n: int) -> None:
    texts = [f"option {i}" for i in range(n)]
    for letter in asks.LETTERS[:n]:
        assert asks.check(choices(texts, letter)) is None


@pytest.mark.parametrize("recommended", [None, "yes", "no"])
def test_yes_no_with_its_default_is_accepted(recommended: str | None) -> None:
    req = yes_no(recommended)
    assert asks.check(req) is None
    assert json.loads(asks.stored_form(req)[1] or "")["recommended"] == (recommended or "yes")


def test_the_six_choice_maximum_fits_the_letters() -> None:
    assert asks.MAX_CHOICES == len(asks.LETTERS) == 6 and asks.MAX_CHOICE == 80


@pytest.mark.parametrize(("req", "needle"), [
    (choices(["only one"]), "2-6 --choice"),
    (choices([f"option {i}" for i in range(7)]), "2-6 --choice"),
    (choices(recommended=None), "needs --recommended"),
    (choices(recommended="D"), "needs --recommended"),              # no D among three
    (choices(recommended="a"), "needs --recommended"),
    (choices(recommended="AB"), "needs --recommended"),
    (yes_no("maybe"), "--yes-no takes --recommended yes or no"),
    (question(form="yes-no", choices=THREE), "not both"),
    (question(choices=THREE, recommended="A"), "need --choice"),    # choices without the form
    (question(recommended="A"), "need --choice"),
    (merge(form="choices", choices=THREE, recommended="A"), "for questions only"),
    (merge(form="yes-no"), "for questions only"),
    (asks.AskPost("approval", bead=BEAD, form="choices", choices=THREE, recommended="A"), "only --bead"),
    (asks.AskPost("approval", bead=BEAD, form="yes-no"), "only --bead"),
    (choices(["a · b", "c"]), "choice A must be one line"),
    (choices(["fine", "x = y"]), "choice B must be one line"),
    (choices(["fine", "two\nlines"]), "choice B must be one line"),
    (choices(["fine", "tab\there"]), "choice B must be one line"),
    (choices(["fine", "nul\x00"]), "choice B must be one line"),
    (choices(["fine", "line\u2028sep"]), "choice B must be one line"),
    (choices(["fine", "x" * 81]), "choice B must be one line"),
    (choices(["fine", ""]), "choice B must be one line"),
    (choices(["fine", "   "]), "choice B must be one line"),
    (choices(["fine", f"use {TOKEN}"]), "choice B would be redacted; reword it"),
])
def test_check_refuses_bad_forms(req: asks.AskPost, needle: str) -> None:
    refusal = asks.check(req)
    assert refusal is not None and needle in refusal, refusal


def test_an_80_character_choice_is_accepted() -> None:
    assert asks.check(choices(["x" * 80, "y"])) is None


# --- K14 -----------------------------------------------------------------------------------------
def test_k14_a_form_less_question_is_accepted_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asks, "REQUIRE_QUESTION_FORM", False)
    assert asks.check(question()) is None
    text, _ = asks.question_card(BARE)
    assert text.split("\n")[0] == "QUESTION" and text.split("\n")[-1] == asks.footer(BARE)
    assert asks.map_reaction(BARE, "👍") is None and asks.map_reply(BARE, "a") is None


def test_k14_a_form_less_question_is_refused_once_the_flag_flips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asks, "REQUIRE_QUESTION_FORM", True)
    assert asks.check(question()) == asks.FORM_NEEDED
    assert asks.check(choices()) is None and asks.check(yes_no()) is None
    assert asks.check(merge()) is None          # merges have no form


@needs_tmux
def test_k14_grace_post_warns_and_answers_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asks, "REQUIRE_QUESTION_FORM", False)

    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(question(), NONE)
        assert reply.result == "posted" and reply.ask is not None
        ask_id = reply.ask.summary.ask_id
        assert reply.message == f"ask {ask_id} posted\n{asks.FORM_WARNING}"
        first = await card_sent(h, ask_id)
        card = next(r["text"] for r in h.fake.sent if r.get("idempotency_key") == f"ask:{ask_id}:0")
        assert card.split("\n")[0] == "QUESTION" and card.split("\n")[-1] == asks.footer(row(ask_id))
        _, said = await reacted(h, "👍", first)
        assert said == f"Answer recorded for ask {ask_id}."
        assert [(a.text, a.choice) for a in h.store.answers(ask_id)] == [("👍", None)]
        structured = await h.daemon.on_ask(choices(), NONE)
        assert structured.result == "posted" and asks.FORM_WARNING not in structured.message
    run_with(tmp_path, scenario)


# --- rendering (K11, K16) ------------------------------------------------------------------------
def test_a_multiple_choice_card_renders_label_choices_answer_line_and_footer_last() -> None:
    text, truncated = asks.question_card(MC)
    lines = text.split("\n")
    assert not truncated
    assert lines[0] == "QUESTION: Multiple Options" and lines[1] == asks.head(MC)
    assert lines[-5:] == [
        "A = rerun on bel-ci (recommended)", "B = skip the check", "C = ask the lead",
        "Answer: 👍 = A (recommended) · 👎 = none · or reply with a letter or your own answer "
        "(!answer k7m2 <text>)",
        "Choices: A = rerun on bel-ci (recommended) · B = skip the check · C = ask the lead"]


@pytest.mark.parametrize(("r", "footer"), [(YN, "Choices: yes (recommended) · no"),
                                           (YN_NO, "Choices: yes · no (recommended)")])
def test_a_yes_no_card_has_no_choice_lines_and_the_footer_last(r: AskRow, footer: str) -> None:
    lines = asks.question_card(r)[0].split("\n")
    assert lines[0] == "QUESTION: yes/no"
    assert lines[-2].startswith("Answer: 👍 = ") and "reply with your own answer" in lines[-2]
    assert lines[-1] == footer
    assert not any(t.startswith(("yes = ", "no = ")) for t in lines)


@pytest.mark.parametrize(("r", "says"), [(YN, "👍 = yes (recommended) · 👎 = no · "),
                                         (YN_NO, "👍 = no (recommended) · 👎 = yes · ")])
def test_the_yes_no_answer_line_names_what_each_reaction_records(r: AskRow, says: str) -> None:
    line = asks.question_card(r)[0].split("\n")[-2]
    assert line.startswith("Answer: " + says), line


def test_a_long_body_is_truncated_and_the_choices_are_not() -> None:
    long = form_row(choices(six_long(), "B"), body="\n".join(f"line {i} of the context" for i in range(300)))
    text, truncated = asks.question_card(long)
    assert truncated and "reply !details for the rest" in text
    assert "line 299 of the context" not in text
    lines = text.split("\n")
    for letter, choice in zip(asks.LETTERS, six_long(), strict=True):
        assert f"{letter} = {choice}" + (" (recommended)" if letter == "B" else "") in lines
    assert lines[-1].startswith("Choices: A = choice 1 ")
    assert "line 299 of the context" in asks.full_text(long)       # !details has it all


def test_split_at_200_with_six_80_character_choices() -> None:
    six = six_long()
    r = form_row(choices(six, "C"))
    parts, _ = asks.card_chunks(r, 200)
    assert len(parts) > 1 and all(len(p) <= 200 for p in parts)
    for i, p in enumerate(parts[1:], start=2):
        assert p.startswith(f"↳ part {i}/{len(parts)}\n")
    lines = [t for p in parts for t in p.split("\n")]
    for letter, choice in zip(asks.LETTERS, six, strict=True):
        assert f"{letter} = {choice}" + (" (recommended)" if letter == "C" else "") in lines   # never cut
    compact = "Choices: A · B · C (recommended) · D · E · F"
    assert parts[-1].split("\n")[-1] == compact and len(compact) <= 47
    assert sum(compact in p for p in parts) == 1


def test_split_at_4000_keeps_the_block_in_one_chunk_with_the_full_footer() -> None:
    six = six_long()
    parts, _ = asks.card_chunks(form_row(choices(six, "C")), 4000)
    assert len(parts) == 1
    full = "Choices: " + " · ".join(f"{letter} = {t}" + (" (recommended)" if letter == "C" else "")
                                    for letter, t in zip(asks.LETTERS, six, strict=True))
    assert parts[0].split("\n")[-1] == full


# --- reaction mapping (K17q) ---------------------------------------------------------------------
@pytest.mark.parametrize("emoji", APPROVE_SET)
def test_an_approve_emoji_maps_to_the_recommended_option(emoji: str) -> None:
    assert asks.map_reaction(MC, emoji) == "A"
    assert asks.map_reaction(form_row(choices(recommended="C")), emoji) == "C"
    assert asks.map_reaction(YN, emoji) == "yes" and asks.map_reaction(YN_NO, emoji) == "no"
    assert asks.map_reaction(BARE, emoji) is None


@pytest.mark.parametrize("emoji", DENY_SET)
def test_a_deny_emoji_maps_to_none_or_the_option_not_recommended(emoji: str) -> None:
    """Amendment 1 §1: on a yes/no card 👎 rejects the recommendation; with `no` recommended it is `yes`."""
    assert asks.map_reaction(MC, emoji) == asks.NONE_OF_THESE
    assert asks.map_reaction(YN, emoji) == "no" and asks.map_reaction(YN_NO, emoji) == "yes"
    assert asks.map_reaction(BARE, emoji) is None


def test_every_skin_tone_is_covered() -> None:
    assert len(TONES) == 6
    for tone in TONES:
        assert asks.map_reaction(MC, "👍" + tone) == "A" and asks.map_reaction(MC, "👎" + tone) == "none"


@pytest.mark.parametrize("emoji", [OTHER_EMOJI, "👀", "🙏", "👍👍"])
def test_any_other_emoji_is_a_free_answer(emoji: str) -> None:
    for r in (MC, YN, YN_NO, BARE):
        assert asks.map_reaction(r, emoji) is None


# --- reply mapping (K18q) ------------------------------------------------------------------------
REPLIES: list[tuple[str, str | None, str | None]] = [   # (reply, on MC (rec. A), on yes/no (rec. yes))
    ("a", "A", None), ("B.", "B", None), ("(c)", "C", None), (" b ", "B", None), ("G", None, None),
    ("yes", "A", "yes"), ("y", "A", "yes"), ("ok", "A", None), ("lgtm", "A", None),
    ("no", "none", "no"), ("n", "none", "no"), ("deny", None, None), ("no thanks", None, None)]


@pytest.mark.parametrize(("text", "on_mc", "on_yn"), REPLIES)
def test_reply_mapping(text: str, on_mc: str | None, on_yn: str | None) -> None:
    assert asks.map_reply(MC, text) == on_mc
    assert asks.map_reply(YN, text) == on_yn
    assert asks.map_reply(BARE, text) is None         # form-less: unchanged


def test_a_letter_past_the_choices_is_free() -> None:
    assert asks.map_reply(MC, "d") is None and asks.map_reply(MC, "D.") is None


def test_on_a_yes_no_card_recommending_no_yes_is_yes_and_ok_is_free() -> None:
    assert asks.map_reply(YN_NO, "yes") == "yes" and asks.map_reply(YN_NO, "ok") is None
    assert asks.map_reply(YN_NO, "no") == "no"


def test_the_confirmation_states_the_mapping() -> None:
    assert asks.recorded(MC, "A", "👍") == "Recorded for k7m2: A = rerun on bel-ci (from 👍)."
    assert asks.recorded(MC, "none", "👎") == "Recorded for k7m2: none of these (from 👎)."
    assert asks.recorded(YN, "yes", " y ") == "Recorded for k7m2: yes (from y)."


# --- recording (K19) -----------------------------------------------------------------------------
@needs_tmux
def test_a_reaction_and_a_reply_record_their_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    async def scenario(h: Harness) -> None:
        via_main(monkeypatch, h.settings)
        await joined(h)
        ask_id = await posted(h, choices())
        first = await card_sent(h, ask_id)
        _, said = await reacted(h, "👍", first)
        assert said == f"Recorded for {ask_id}: A = rerun on bel-ci (from 👍)."
        mid = await send(h, "(c)", first)
        await settled(h, mid)
        said = "".join(r[0] for r in h.store.db.execute(
            "SELECT text FROM outbox WHERE key LIKE ? ORDER BY seq", (f"ask:{mid}:%",)))
        assert said == f"Recorded for {ask_id}: C = ask the lead (from (c))."
        mid = await send(h, "rerun it twice", first)
        await settled(h, mid)
        assert [(a.text, a.choice) for a in h.store.answers(ask_id)] == [
            ("👍", "A"), ("(c)", "C"), ("rerun it twice", None)]
        legacy = await posted(h, question("Which port?"))
        await card_sent(h, legacy)
        await h.say(f"!answer {legacy} port 8000")
        await wait_until(lambda: len(h.store.answers(legacy)) == 1)

        capsys.readouterr()
        assert await asyncio.to_thread(cli.main, ["ask", "get", ask_id]) == 0
        out = capsys.readouterr().out.split("\n")
        heads = [t for t in out if t.startswith("--- ")]
        assert [t.split(" from ")[0] for t in heads] == [
            "--- answer (choice A)", "--- answer (choice C)", "--- answer"]
        assert heads[0].startswith("--- answer (choice A) from op at ")
        assert await asyncio.to_thread(cli.main, ["ask", "get", ask_id, "--json"]) == 0
        view = msgspec.json.decode(capsys.readouterr().out, type=asks.AskReply).ask
        assert view is not None and [a.choice for a in view.answers] == ["A", "C", None]
        assert await asyncio.to_thread(cli.main, ["ask", "wait", ask_id, "--timeout", "10"]) == 0
        assert "--- answer (choice A) from op at " in capsys.readouterr().out
        assert await asyncio.to_thread(cli.main, ["ask", "get", legacy]) == 0
        out = capsys.readouterr().out
        assert "--- answer from op at " in out and "(choice" not in out
        raw = json.loads(await asyncio.to_thread(_json_get, legacy, capsys))
        assert raw["ask"]["answers"][0].get("choice") is None
    run_with(tmp_path, scenario)


def _json_get(ask_id: str, capsys: pytest.CaptureFixture[str]) -> str:
    assert cli.main(["ask", "get", ask_id, "--json"]) == 0
    return capsys.readouterr().out


# --- attribution ---------------------------------------------------------------------------------
@needs_tmux
def test_a_reaction_on_a_later_part_keeps_actor_event_and_thread(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        ask_id = await posted(h, choices(six_long(), "C"))
        stored = h.store.ask(ask_id)
        assert stored is not None and stored.card_parts > 2
        last = await card_sent(h, ask_id, stored.card_parts - 1)
        mid = await react(h, "👍", last)
        await settled(h, mid)
        answers = h.store.answers(ask_id)
        assert [(a.operator, a.message_id, a.text, a.choice) for a in answers] == [("op", mid, "👍", "C")]
        assert mid.startswith("r:")
        assert threads(h, mid) == {last}           # the reply goes on the reacted part
    run_with(tmp_path, scenario, settings_overrides={"chunk_chars": 200})


# --- K19b: only approval cards decide approvals --------------------------------------------------
WORDS = ["!approve", "!deny", "approve", "approved", "yes", "lgtm", "deny"]
EMOJIS = sorted(verbs.APPROVE_EMOJIS) + sorted(verbs.DENY_EMOJIS)
CARDS = ["choices", "yes-no", "form-less", "with-bead", "merge", "notice"]


async def target(h: Harness, which: str) -> tuple[str, str | None]:
    """Post the card `which`: (its chunk 0's message ID, its ask ID or None for a notice)."""
    if which == "notice":
        reply = await h.daemon.on_ask(NoticePost("Plan 4 merged", "The sandbox runtime is in.",
                                                 poster="controller"), NONE)
        assert reply.result == "posted", reply
        notice_id = reply.message.removeprefix("posted ")
        await wait_until(lambda: sent_mid(h, f"notice:{notice_id}:0") is not None)
        mid = sent_mid(h, f"notice:{notice_id}:0")
        assert mid is not None
        return mid, None
    req = {"choices": choices(), "yes-no": yes_no(), "form-less": question(),
           "with-bead": choices(bead=BEAD), "merge": merge()}[which]
    ask_id = await posted(h, req)
    return await card_sent(h, ask_id), ask_id


async def handled(h: Harness, mid: str) -> None:
    await wait_until(lambda: h.store.inbound_status(mid) in ("dispatched", "done", "dropped"), 30)


@needs_tmux
@pytest.mark.parametrize("which", CARDS)
def test_k19b_nothing_on_a_non_approval_card_decides(tmp_path: Path, which: str) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        first, ask_id = await target(h, which)
        for word in WORDS:
            await handled(h, await send(h, word, first))
        for emoji in EMOJIS:
            await handled(h, await react(h, emoji, first, OPERATOR_HEX))
        assert h.store.db.execute("SELECT COUNT(*) FROM ask_attempts").fetchone()[0] == 0
        assert btq_log(h.settings.workdir) == []          # the fake approve-bead never ran
        if ask_id is not None:
            r = h.store.ask(ask_id)
            assert r is not None and r.kind == ("merge" if which == "merge" else "question")
            assert r.status in ("open", "answered")
    go(tmp_path, scenario)


def test_chunk_marker_room_matches_the_design() -> None:
    assert chunk.usable(200) == 184
