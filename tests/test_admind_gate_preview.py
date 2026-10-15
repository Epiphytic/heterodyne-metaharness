"""btq-ryfxf: admind refuses an approval ask that btq's gate would reject once approved (design
docs/superpowers/specs/2026-10-14-admind-pre-ask-gate-design.md, tests 10-17).

Every read is `approve-bead <bead> --json --gate-preview`; `postable` refuses unless the preview is present
and valid, and quotes btq's reasons, redacted, to the poster; the decide preflight refuses an approve on an
invalid preview. approve-bead is always the fake (tests/fakes/fake_approve_bead.py); nothing here runs the
real approve-bead or btq.
"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import msgspec
import pytest
from admind_asks_fixture import approve_bead_wrapper, bead_db, btq_log, decisions
from test_admind_approvals import (
    APPROVAL,
    BEAD,
    D2,
    D12,
    FIELDS,
    OTHER,
    TOKEN,
    approve,
    ask_status,
    attempts,
    bead,
    card,
    edit,
    fake,
    go,
    queued,
    readout,
    say,
    send,
    settled,
    undecide,
)
from test_admind_asks import NONE, audited, joined, merge, posted, question
from test_admind_daemon import Harness, needs_tmux

from heterodyne.admind import approvals, asks
from heterodyne.admind.approvals import BtqError, Readout
from heterodyne.admind.audit import ref_id

PREFIX = f"btq: approval {BEAD} rejected: "
NO_ADR = "no ADR file ref at adr_revision 0123456"
NO_REVIEW = "design_review is missing"
REASONS = [PREFIX + NO_ADR, PREFIX + NO_REVIEW]
GATE = "btq's gate would reject this approval once approved; fix the bead and post it again: "
HOSTILE_KEYS = f"{TOKEN}_digest, {'ab' * 32}_digest"
HOSTILE = [PREFIX + f"recorded digest(s) conflict with the current ask: {HOSTILE_KEYS}"]
HOSTILE_REDACTED = ("recorded digest(s) conflict with the current ask: <redacted GitHub token>_digest, "
                    "<redacted hex key>_digest")


def gate_refusal(*reasons: str) -> str:
    return GATE + "; ".join(reasons)


def stored(h: Harness) -> int:
    return h.store.db.execute("SELECT COUNT(*) FROM asks").fetchone()[0]


def ask_chunks(h: Harness) -> int:
    return h.store.db.execute("SELECT COUNT(*) FROM outbox WHERE key LIKE 'ask:%'").fetchone()[0]


# --- the fake ------------------------------------------------------------------------------------
def test_fake_gate_preview_interface(tmp_path: Path) -> None:
    wrapper = approve_bead_wrapper(tmp_path)
    bead_db(tmp_path, **{BEAD: bead(), OTHER: bead(preview_ok=False),
                         "btq-old1": bead(preview="unsupported")})

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(wrapper), *args], capture_output=True, text=True, timeout=30, check=False)

    assert "gate_preview" not in json.loads(run(BEAD, "--json").stdout)
    assert json.loads(run(BEAD, "--json", "--gate-preview").stdout)["gate_preview"] == {"valid": True,
                                                                                        "reasons": []}
    assert json.loads(run(OTHER, "--json", "--gate-preview").stdout)["gate_preview"] == {
        "valid": False, "reasons": [f"btq: approval {OTHER} rejected: no reason given"]}
    alone = run(BEAD, "--gate-preview")
    assert alone.returncode == 2 and len(alone.stderr.splitlines()) == 1
    assert run(BEAD, "--json", "--gate-preview", "--yes").returncode == 2
    assert run("btq-old1", "--json", "--gate-preview").returncode == 2
    edit(tmp_path, status="closed")
    assert json.loads(run(BEAD, "--json", "--gate-preview").stdout)["gate_preview"] is None


# --- Readout and read (test 10, test 12's decoding) ----------------------------------------------
def test_readout_needs_the_gate_preview() -> None:
    r = msgspec.json.decode(json.dumps(FIELDS), type=Readout)
    assert r.gate_preview == approvals.GatePreview(True, [])
    for bad in ({k: v for k, v in FIELDS.items() if k != "gate_preview"},
                {**FIELDS, "gate_preview": {"valid": True}},
                {**FIELDS, "gate_preview": {"valid": True, "reasons": [], "x": 1}},
                {**FIELDS, "gate_preview": {"valid": "yes", "reasons": []}}):
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(json.dumps(bad), type=Readout)


def test_every_read_asks_for_the_preview(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path)
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and r.gate_preview == approvals.GatePreview(True, [])
        assert btq_log(tmp_path) == [[BEAD, "--json", "--gate-preview"]]
        assert await approvals.read_back(ab, BEAD) is not None
        assert btq_log(tmp_path)[-1] == [BEAD, "--json", "--gate-preview"]
    asyncio.run(scenario())


@pytest.mark.parametrize("fields", [
    {"preview": "unsupported"},                     # an approve-bead that predates the flag: exit 2
    {"preview": "omit"},                            # no gate_preview key
    {"preview": "null"},                            # null for an open, undecided bead
    {"preview": "always", "status": "closed"},      # a preview for a closed bead
    {"preview": "always", "decision": "approve"},   # a preview for a decided bead
])
def test_read_fails_closed_on_a_broken_preview(tmp_path: Path, fields: dict[str, Any]) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path, **fields)
        with pytest.raises(BtqError) as caught:
            await ab.read(BEAD)
        assert caught.value.word == ("unavailable" if fields["preview"] == "unsupported" else "bad output")
    asyncio.run(scenario())


def test_read_takes_null_for_a_closed_or_decided_bead(tmp_path: Path) -> None:
    async def scenario() -> None:
        ab = fake(tmp_path, status="closed")
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and r.gate_preview is None
        undecide(tmp_path)
        edit(tmp_path, decision="approve")
        r = await ab.read(BEAD)
        assert isinstance(r, Readout) and r.decided and r.gate_preview is None
    asyncio.run(scenario())


# --- postable (tests 11, 13) ---------------------------------------------------------------------
def test_postable_refuses_on_the_preview() -> None:
    assert approvals.postable(readout(gate_preview=None)) == (
        "approve-bead gave no gate preview for this bead; try again", "gate")
    invalid = readout(gate_preview={"valid": False, "reasons": REASONS})
    assert approvals.postable(invalid) == (gate_refusal(NO_ADR, NO_REVIEW), "gate")
    other = [f"btq: approval {OTHER} rejected: x", f"  {PREFIX}y", "plain"]     # only the exact prefix goes
    assert approvals.postable(readout(gate_preview={"valid": False, "reasons": other})) == (
        gate_refusal(*other), "gate")


def test_the_existing_refusals_win_over_the_preview() -> None:
    invalid = {"valid": False, "reasons": REASONS}
    refusal = approvals.postable(readout(gaps=["no effect"], gate_preview=invalid))
    assert refusal is not None and refusal[1] == "not postable" and "grooming: no effect" in refusal[0]
    refusal = approvals.postable(readout(design_review_valid=False, gate_preview=invalid))
    assert refusal is not None and refusal[1] == "not postable" and "two-LLM" in refusal[0]


# --- posting (tests 11-14, 17) -------------------------------------------------------------------
@needs_tmux
def test_a_gate_rejection_is_refused_at_post(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply == asks.refused(gate_refusal(NO_ADR, NO_REVIEW))
        assert audited(h, kind="ask", action="refused", ask_kind="approval", bead=BEAD, reason="gate")
        assert stored(h) == 0 and ask_chunks(h) == 0
        assert btq_log(tmp_path) == [[BEAD, "--json", "--gate-preview"]]
    go(tmp_path, scenario, {BEAD: bead(preview_ok=False, preview_reasons=REASONS)})


@needs_tmux
def test_hostile_digest_key_names_are_redacted_at_post(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply == asks.refused(GATE + HOSTILE_REDACTED)
        audit = (h.settings.state_dir / "audit.jsonl").read_text()
        assert TOKEN not in reply.message and "ab" * 32 not in reply.message
        assert TOKEN not in audit and "ab" * 32 not in audit
        assert stored(h) == 0
    go(tmp_path, scenario, {BEAD: bead(preview_ok=False, preview_reasons=HOSTILE)})


@needs_tmux
@pytest.mark.parametrize(("fields", "word"), [
    ({"preview": "unsupported"}, "unavailable"),
    ({"preview": "omit"}, "bad output"),
    ({"preview": "null"}, "bad output"),
    ({"preview": "always", "status": "closed"}, "bad output"),
])
def test_a_broken_preview_refuses_the_post(tmp_path: Path, fields: dict[str, Any], word: str) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply == asks.refused(f"admind could not read {BEAD} ({word}); try again.")
        assert audited(h, kind="ask", action="refused", bead=BEAD, reason=word)
        assert stored(h) == 0 and ask_chunks(h) == 0
    go(tmp_path, scenario, {BEAD: bead(**fields)})


@needs_tmux
def test_gaps_beat_the_gate_at_post(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        reply = await h.daemon.on_ask(APPROVAL, NONE)
        assert reply.result == "refused" and "send it back for grooming: no effect" in reply.message
        assert GATE not in reply.message
        assert audited(h, kind="ask", action="refused", bead=BEAD, reason="not postable")
    go(tmp_path, scenario, {BEAD: bead(gaps=["no effect"], preview_ok=False, preview_reasons=REASONS)})


@needs_tmux
def test_a_fixed_bead_posts_after_a_gate_refusal(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        assert (await h.daemon.on_ask(APPROVAL, NONE)).result == "refused"
        edit(tmp_path, preview_ok=True, preview_reasons=[], digest=D2)
        ask_id = await posted(h, APPROVAL)
        row = h.store.ask(ask_id)
        assert row is not None and row.digest == D2 and stored(h) == 1
        assert audited(h, kind="ask", action="posted", ask_id=ask_id, superseded=[])
    go(tmp_path, scenario, {BEAD: bead(preview_ok=False, preview_reasons=REASONS)})


@needs_tmux
def test_question_and_merge_posts_never_run_approve_bead(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await joined(h)
        await posted(h, question())
        await posted(h, merge())
        assert btq_log(tmp_path) == []
    go(tmp_path, scenario, {BEAD: bead(preview="unsupported")})


# --- the R29 refresh (test 15) -------------------------------------------------------------------
@needs_tmux
def test_a_refresh_is_refused_on_the_preview(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, digest=D2, preview_ok=False, preview_reasons=REASONS)
        text = await say(h, approve(), first)
        why = gate_refusal(NO_ADR, NO_REVIEW)
        assert text == (f"{BEAD} changed after this card was posted (shown {D12}, now {D2[:12]}). Nothing "
                        f"recorded, and admind could not post a fresh card: {why}.")
        assert ask_status(h, ask_id) == "stale" and h.store.newer_ask(ask_id) is None
        assert stored(h) == 1 and decisions(h) == []
        assert audited(h, kind="ask", action="refused", ask_id=ask_id, reason="stale", refresh_refused="gate",
                       updated_parts=0)
    go(tmp_path, scenario)


# --- deciding (test 16) --------------------------------------------------------------------------
def decide_refusal(*reasons: str) -> str:
    return (f"btq's gate would reject approving {BEAD} now: {'; '.join(reasons)}. Nothing recorded. Fix the "
            "bead on the host, or deny it.")


@needs_tmux
def test_approve_is_refused_in_preflight_on_an_invalid_preview(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, preview_ok=False, preview_reasons=REASONS)      # the policy changed, say
        mid = await send(h, approve(), first)
        await settled(h, mid)
        assert queued(h, mid) == decide_refusal(NO_ADR, NO_REVIEW)
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
        assert attempts(h, ask_id)[-1][0] == mid and attempts(h, ask_id)[-1][-1] == "refused"
        assert audited(h, kind="ask", action="refused", message_id=ref_id(mid), reason="gate", status="open")
        text = await say(h, f"!deny {BEAD} not now", first)
        assert text.startswith(f"Denied {BEAD} as op (via Marmot).")
        assert len(decisions(h)) == 1 and "--deny" in decisions(h)[0] and ask_status(h, ask_id) == "denied"
    go(tmp_path, scenario)


@needs_tmux
def test_hostile_digest_key_names_are_redacted_at_decide(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        ask_id, first = await card(h)
        edit(tmp_path, preview_ok=False, preview_reasons=HOSTILE)
        text = await say(h, approve(), first)
        assert text == decide_refusal(HOSTILE_REDACTED)
        audit = (h.settings.state_dir / "audit.jsonl").read_text()
        assert TOKEN not in text and "ab" * 32 not in text and TOKEN not in audit and "ab" * 32 not in audit
        assert decisions(h) == [] and ask_status(h, ask_id) == "open"
    go(tmp_path, scenario)
