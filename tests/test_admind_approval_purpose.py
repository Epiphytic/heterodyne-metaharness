"""cxmkf approval purpose labels (K11, K17) and approval link lines (K23's LINK_TOO_LONG), design §2.1.

approve-bead is always the fake; nothing here runs the real one or btq. The fake predates `purpose`, so the
harness test adds it to the readout the daemon reads.
"""

from pathlib import Path
from typing import Any

import msgspec
import pytest
from test_admind_approvals import BEAD, FIELDS, LINES, card, card_row, go, readout
from test_admind_asks_bump import repeat_rows, repeated_card
from test_admind_daemon import Harness, needs_tmux

from heterodyne.admind import approvals, asks, chunk
from heterodyne.admind.approvals import ApproveBead, Busy, Readout

SHA = "0123456789abcdef" * 2 + "01234567"           # 40 hex, built at runtime
OWNER, REPO = "o" + "w" * 38, "r" * 100
LONG_URL = f"https://github.com/{OWNER}/{REPO}/blob/{SHA}/docs/design.md"
LONG_LINK = "        link: " + LONG_URL                # as `_linked` renders it under `    - ref`


def linked(url: str = LONG_URL, **kw: Any) -> Readout:
    return readout(links=[{"doc": 1, "url": url}], **kw)


# --- link lines (K23) ----------------------------------------------------------------------------
def test_the_design_example_is_the_line_measured() -> None:
    assert len(OWNER) == 39 and len(REPO) == 100
    assert len("link: " + LONG_URL) == 226           # the design's count, without the ref's indent
    assert len(LONG_LINK) == 234 > chunk.usable(200) == 184


def test_a_long_approval_link_line_is_refused_at_200() -> None:
    refusal = asks.approval_card(card_row(), linked(), 200)
    assert refusal == asks.LINK_TOO_LONG.format(n=len(LONG_LINK), usable=184, c=200)


def test_a_long_updated_content_link_line_is_refused_at_200() -> None:
    assert asks.updated_content(BEAD, linked(), 200) == asks.LINK_TOO_LONG.format(
        n=len(LONG_LINK), usable=184, c=200)


@pytest.mark.parametrize("chunk_chars", [4000, 260, 300])
def test_a_long_link_line_is_whole_in_one_chunk_when_it_fits(chunk_chars: int) -> None:
    built = asks.approval_card(card_row(), linked(), chunk_chars)
    assert isinstance(built, asks.ApprovalCard)
    updated = asks.updated_content(BEAD, linked(), chunk_chars)
    assert isinstance(updated, list)
    for parts in (built.card_chunks, updated):
        assert all(len(p) <= chunk_chars for p in parts)
        assert sum(LONG_LINK in p.split("\n") for p in parts) == 1
        assert all(LONG_URL in p or OWNER not in p for p in parts)    # no chunk holds part of the URL
    if chunk_chars == 4000:
        assert len(built.card_chunks) == 1


# --- purpose labels (K11, K17) -------------------------------------------------------------------
@pytest.mark.parametrize(("purpose", "label"), [("design", "APPROVAL: design"),
                                                ("execution", "APPROVAL: execution"),
                                                (None, "APPROVAL: purpose not set")])
def test_the_label_comes_from_the_purpose(purpose: str | None, label: str) -> None:
    r = readout(purpose=purpose)
    assert asks.approval_label(r) == label
    built = asks.approval_card(card_row(), r, 4000)
    assert isinstance(built, asks.ApprovalCard)
    assert built.card_chunks[0].split("\n")[0] == label           # inside the checked chunks
    assert built.details_chunks == built.card_chunks
    small = asks.approval_card(card_row(), r, 200)
    assert isinstance(small, asks.ApprovalCard)
    assert small.card_chunks[0].split("\n")[0] == label and len(small.card_chunks) > 1
    assert sum(label in p for p in small.card_chunks) == 1


def test_an_old_readout_without_purpose_decodes_as_not_set() -> None:
    old = msgspec.json.encode(FIELDS)
    assert b"purpose" not in old
    r = msgspec.json.decode(old, type=Readout)
    assert r.purpose is None and approvals.postable(r) is None
    assert asks.approval_label(r) == asks.NO_PURPOSE == "APPROVAL: purpose not set"


@pytest.mark.parametrize("valid", [True, False, None])
def test_the_purpose_is_never_inferred(valid: bool | None) -> None:
    r = readout(design_review_valid=valid, purpose="execution")
    assert asks.approval_label(r) == "APPROVAL: execution"
    assert asks.approval_label(readout(design_review_valid=valid)) == asks.NO_PURPOSE


@pytest.mark.parametrize("purpose", ["Design", "plan", "", "design ", "execution\n"])
def test_postable_refuses_any_other_purpose(purpose: str) -> None:
    assert approvals.postable(readout(purpose=purpose)) == (      # the value itself is never echoed
        "the bead is not approvable as written; send it back for grooming: its purpose must be design or "
        "execution")
    assert asks.approval_label(readout(purpose=purpose)) == asks.NO_PURPOSE


@pytest.mark.parametrize("purpose", [None, "design", "execution"])
def test_postable_accepts_the_three_values(purpose: str | None) -> None:
    assert approvals.postable(readout(purpose=purpose)) is None


@needs_tmux
@pytest.mark.parametrize(("purpose", "label"), [("design", "APPROVAL: design"),
                                                (None, "APPROVAL: purpose not set")])
def test_a_posted_card_and_its_repeat_carry_the_label(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      purpose: str | None, label: str) -> None:
    read = ApproveBead.read

    async def with_purpose(self: ApproveBead, bead: str, *args: Any, **kw: Any) -> Readout | Busy:
        r = await read(self, bead, *args, **kw)
        return r if isinstance(r, Busy) or purpose is None else msgspec.structs.replace(r, purpose=purpose)

    monkeypatch.setattr(ApproveBead, "read", with_purpose)

    async def scenario(h: Harness) -> None:
        ask_id, _ = await card(h)
        stored = h.store.ask(ask_id)
        assert stored is not None
        details = asks.stored_details(stored)
        assert details[0].split("\n")[0] == label
        sent = [r["text"] for r in h.fake.sent
                if str(r.get("idempotency_key", "")).startswith(f"ask:{ask_id}:")]
        assert sent == details
        command, _ = await repeated_card(h, ask_id)
        assert [t for _, _, t in repeat_rows(h, ask_id, command)] == details     # reused, not rebuilt
    go(tmp_path, scenario, chunk_chars=200)


def test_the_fixture_readout_has_a_ref_to_link() -> None:
    assert any("[--doc 1]" in t for t in LINES["ask"])
