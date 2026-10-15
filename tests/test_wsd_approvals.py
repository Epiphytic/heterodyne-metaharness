"""Plan 4c Task 10, shared with plan 5i part 2: the approval bead's composition and its verdict
(`heterodyne.wsd.approvals`). `compose` builds the bead's title, description and `metadata.ask`; `verdict`
applies E13's predicates 2-10 to a readout. Predicate 1 (`Busy`) is the caller's: a `Busy` reply is never
given to `verdict`. Part 2 adds its own deny-reason test (`view.answers`); here `view` is None."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import ASK_FIELDS, World, factory

from heterodyne.config import secret_scan
from heterodyne.wsd import approvals
from heterodyne.wsd import btq as wbtq

# --- the seams: how a test builds the inputs. `AskFields` holds the four text fields; `Ref` is one ref;
# `Composed` has `.title`, `.description` and `.ask` (a dict with exactly ASK_FIELDS); `Expected` is what the
# journal says the decision must be about. A readout is parsed from approve-bead's line by
# `wbtq.parse_readout`, which forbids unknown fields but keeps a missing, null or wrong-typed field, so that
# `verdict` names the predicate it fails. ---

PURPOSE = "execution"


def ask_fields(**changes: str) -> Any:
    fields = {"effect": "publish codex install 0.160.0-0123456789ab manifest <display>",
              "excludes": "selecting it (`use`), and any other version or content.",
              "why": "the recipe in [harnesses.codex] asks for it.",
              "risks": "the sources' trust as listed."}
    return approvals.AskFields(**{**fields, **changes})


def refs() -> list[Any]:
    return [approvals.Ref("commit", REPO, COMMIT)]


def readout_of(fields: dict[str, Any]) -> Any:
    return wbtq.parse_readout(json.dumps(fields).encode())


def expected(bead: str, context_digest: str, effect: str) -> Any:
    """The journal's side: the bead, its context digest, and a check of `metadata.ask.effect` (predicate
    10: T10 parses it against the staged install; part 2 compares it with the asked effect)."""
    return approvals.Expected(bead, context_digest, lambda value: value == effect)


def decide(readout: Any, shown: dict[str, Any], recomputed: tuple[str | None, list[str]], expect: Any) -> Any:
    return approvals.verdict(readout, shown, recomputed, expect, None)


# --- the fixtures ---

COMMIT = hashlib.sha1(b"the running harness commit").hexdigest()  # noqa: S324 - a commit ID's form
OPERATOR = "operator-a"
TITLE = "Add the codex 0.160.0 managed install (0.160.0-0123456789ab)"
CONTEXT = "What: add a managed install of codex 0.160.0 to the store."
REPO = "Epiphytic/heterodyne-metaharness"
OTHER_EFFECT = "publish codex install 0.160.0-ba9876543210 manifest <display>"


def created(world: World, composed: Any) -> tuple[Any, dict[str, Any]]:
    """The bead `bd create` makes from `composed`, through fake btq, and the queue that shows it."""
    queue = factory(world)("approvals", "wsd-test")
    bead = queue.bd("create", composed.title, f"--description={composed.description}", "--type=task",
                    "--labels=kind:approval", "--metadata", json.dumps({"ask": composed.ask}), "--json")
    return queue, queue.show(bead["id"])


@pytest.fixture
def bead(tmp_path: Path) -> tuple[dict[str, Any], tuple[str | None, list[str]], Any]:
    """An approval bead as created, its recomputed digest, and what the journal expects of it."""
    composed = approvals.compose(TITLE, ask_fields(), CONTEXT, refs(), PURPOSE)
    queue, shown = created(World(tmp_path / "btq-state"), composed)
    recomputed = queue.ask_digest(shown)
    assert recomputed[0] is not None and recomputed[1] == []
    return shown, recomputed, expected(shown["id"], recomputed[0], composed.ask["effect"])


def approved(shown: dict[str, Any], digest: str) -> dict[str, Any]:
    return {"format": 1, "busy": False, "id": shown["id"], "status": "closed", "labels": ["kind:approval"],
            "kind_approval": True, "digest": digest, "posted_digest": digest, "gaps": [],
            "design_review_valid": None, "adr_revision": None, "approvers": [OPERATOR], "decision": "approve",
            "approved_by": OPERATOR, "approved_digest": digest, "denied_by": None, "denied_digest": None,
            "via": "marmot", "via_ref": None, "decided": True, "gate_valid": True, "gate_reasons": [],
            "links": [], "readout": {"title": [], "description": [], "ask": []}}


# --- compose ---

def test_composed_ask_has_exactly_ask_fields_and_its_digest_is_btqs(tmp_path: Path) -> None:
    title, context = TITLE, CONTEXT
    composed = approvals.compose(title, ask_fields(), context, refs(), PURPOSE)
    assert tuple(sorted(composed.ask)) == tuple(sorted(ASK_FIELDS))
    assert composed.ask["effect"] == ask_fields().effect and composed.ask["why"] == ask_fields().why
    assert composed.ask["refs"] == [{"kind": "commit", "repo": REPO, "id": COMMIT}]
    assert composed.title == title and context in composed.description
    queue, shown = created(World(tmp_path / "btq-state"), composed)
    digest, gaps = queue.ask_digest(shown)
    assert gaps == [] and digest is not None
    # the bead as created is the bead as composed: the digest of one is the digest of the other
    assert digest == queue.ask_digest({"title": composed.title, "description": composed.description,
                                       "metadata": {"ask": composed.ask}})[0]
    assert secret_scan.identifier_kind(composed.description) is None


def test_compose_is_deterministic() -> None:
    a = approvals.compose("t", ask_fields(), "c", refs(), PURPOSE)
    b = approvals.compose("t", ask_fields(), "c", refs(), PURPOSE)
    assert (a.title, a.description, a.ask) == (b.title, b.description, b.ask)


# --- verdict ---

def test_verdict_approves_when_every_predicate_holds(bead: tuple[dict[str, Any], Any, Any]) -> None:
    shown, recomputed, expect = bead
    assert isinstance(decide(readout_of(approved(shown, recomputed[0])), shown, recomputed, expect),
                      approvals.Approved)


def other() -> str:
    return hashlib.sha256(b"another ask").hexdigest()


# (predicate, case, the readout's change, the bead's change, the expected end). A readout change is a dict of
# fields set (`None` included) or a list of fields left out; "<other>" is another digest. "denied" is a
# Denied, every other end is Unverified with that predicate's number.
CASES: list[tuple[int, str, dict[str, Any] | list[str], dict[str, Any], str]] = [
    (2, "missing", ["id"], {}, "unverified"),
    (2, "null", {"id": None}, {}, "unverified"),
    (2, "type", {"id": 7}, {}, "unverified"),
    (2, "value", {"id": "btq-other"}, {}, "unverified"),
    (3, "missing", ["kind_approval"], {}, "unverified"),
    (3, "null", {"kind_approval": None}, {}, "unverified"),
    (3, "type", {"kind_approval": "true"}, {}, "unverified"),
    (3, "value", {"kind_approval": False}, {}, "unverified"),
    (4, "missing", ["status"], {}, "unverified"),
    (4, "null", {"status": None}, {}, "unverified"),
    (4, "type", {"status": 1}, {}, "unverified"),
    (4, "value", {"status": "open"}, {}, "unverified"),
    (4, "decided-missing", ["decided"], {}, "unverified"),
    (4, "decided-null", {"decided": None}, {}, "unverified"),
    (4, "decided-type", {"decided": "yes"}, {}, "unverified"),
    (4, "decided-value", {"decided": False}, {}, "unverified"),
    (5, "missing", ["decision"], {}, "unverified"),
    (5, "null", {"decision": None}, {}, "unverified"),
    (5, "type", {"decision": 1}, {}, "unverified"),
    (5, "value", {"decision": "maybe"}, {}, "unverified"),
    (5, "deny", {"decision": "deny", "approved_by": None, "approved_digest": None, "denied_by": OPERATOR,
                 "denied_digest": "<digest>"}, {}, "denied"),
    (5, "denied-by", {"denied_by": OPERATOR}, {}, "denied"),
    (5, "denied-digest", {"denied_digest": "<digest>"}, {}, "denied"),
    (5, "denied-missing", ["denied_by", "denied_digest"], {}, "unverified"),
    (5, "denied-type", {"denied_by": 3}, {}, "unverified"),
    (6, "missing", ["digest"], {}, "unverified"),
    (6, "null", {"digest": None}, {}, "unverified"),
    (6, "type", {"digest": 5}, {}, "unverified"),
    (6, "value", {"digest": "<other>"}, {}, "unverified"),
    (6, "empty", {"digest": ""}, {}, "unverified"),
    (6, "gaps", {"gaps": ["metadata.ask.risks is empty"]}, {}, "unverified"),
    (6, "gaps-missing", ["gaps"], {}, "unverified"),
    (6, "gaps-null", {"gaps": None}, {}, "unverified"),
    (6, "recomputed", {}, {"why": "edited after posting"}, "unverified"),
    (7, "missing", ["posted_digest"], {}, "unverified"),
    (7, "null", {"posted_digest": None}, {}, "unverified"),
    (7, "type", {"posted_digest": 5}, {}, "unverified"),
    (7, "value", {"posted_digest": "<other>"}, {}, "unverified"),
    (8, "missing", ["approved_digest"], {}, "unverified"),
    (8, "null", {"approved_digest": None}, {}, "unverified"),
    (8, "type", {"approved_digest": 5}, {}, "unverified"),
    (8, "value", {"approved_digest": "<other>"}, {}, "unverified"),
    (9, "missing", ["approvers"], {}, "unverified"),
    (9, "null", {"approvers": None}, {}, "unverified"),
    (9, "type", {"approvers": OPERATOR}, {}, "unverified"),
    (9, "value", {"approvers": []}, {}, "unverified"),
    (9, "empty-name", {"approvers": [""], "approved_by": ""}, {}, "unverified"),
    (9, "by-missing", ["approved_by"], {}, "unverified"),
    (9, "by-null", {"approved_by": None}, {}, "unverified"),
    (9, "by-type", {"approved_by": 3}, {}, "unverified"),
    (9, "by-value", {"approved_by": "operator-b"}, {}, "unverified"),
    (10, "null", {}, {"effect": None}, "unverified"),
    (10, "type", {}, {"effect": 5}, "unverified"),
    (10, "value", {}, {"effect": OTHER_EFFECT}, "unverified"),
]


@pytest.mark.parametrize("predicate, case, change, edit, end", CASES, ids=[f"{p}-{c}" for p, c, *_ in CASES])
def test_verdict_approves_only_when_every_predicate_holds(
        bead: tuple[dict[str, Any], Any, Any], predicate: int, case: str, change: dict[str, Any] | list[str],
        edit: dict[str, Any], end: str) -> None:
    shown, recomputed, expect = bead
    digest = recomputed[0]
    if edit:
        # the bead as `show` returns it now; for predicate 10 its digests move with it, so only 10 sees it
        ask = {**shown["metadata"]["ask"], **edit}
        shown = {**shown, "metadata": {**shown["metadata"], "ask": ask}}
    fields = approved(shown, digest)
    if isinstance(change, list):
        for name in change:
            fields.pop(name)
    else:
        fields.update({k: (other() if v == "<other>" else digest if v == "<digest>" else v)
                       for k, v in change.items()})
    now = (shown_digest(shown), []) if edit else recomputed
    if edit and predicate == 10:
        for name in ("digest", "posted_digest", "approved_digest"):
            fields[name] = now[0]
        expect = approvals.Expected(expect.bead, now[0], expect.effect)
    got = decide(readout_of(fields), shown, now, expect)
    if end == "denied":
        assert isinstance(got, approvals.Denied)
    else:
        assert isinstance(got, approvals.Unverified) and got.n == predicate, got


def shown_digest(shown: dict[str, Any]) -> str:
    """fake btq's `ask_digest` over a bead dict (any ask shape: predicate 10's edits keep the fields)."""
    body = json.dumps([shown.get("title"), shown.get("description", ""), shown["metadata"]["ask"]],
                      sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


@pytest.mark.parametrize("fmt", [None, 999, 2, 0, "1", True, 1.0, [1]])
def test_a_readout_whose_format_is_not_the_integer_1_is_refused(fmt: Any) -> None:
    for fields in ({**approved({"id": "btq-1"}, "x"), "format": fmt}, {"format": fmt, "busy": True}):
        with pytest.raises(ValueError, match="^the readout is not format 1$"):
            wbtq.parse_readout(json.dumps(fields).encode())


def test_a_readout_without_a_format_is_refused() -> None:
    fields = approved({"id": "btq-1"}, "x")
    del fields["format"]
    with pytest.raises(ValueError, match="^the readout is not format 1$"):
        wbtq.parse_readout(json.dumps(fields).encode())


def test_a_readout_with_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValueError):
        wbtq.parse_readout(json.dumps({**approved({"id": "btq-1"}, "x"), "surprise": 1}).encode())
    assert isinstance(wbtq.parse_readout(b'{"format": 1, "busy": true}'), wbtq.Busy)
