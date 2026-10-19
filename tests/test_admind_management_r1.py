"""Adversarial regressions for V2 only; no transport or native authority."""

import dataclasses
import json
from unittest.mock import Mock

import pytest
from test_admind_management_presentation import BODY, META, post, request

from heterodyne.admind import asks, binding, chunk, ctl, links, management

pytest_plugins = ("test_admind_management_presentation",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("approved!", "A"),
        ("👍", "A"),
        ("✅", "A"),
        ("deny", "none"),
        ("rejected: unsafe", "none"),
        ("👎 no thanks", "none"),
        ("(B)", "B"),
    ],
)
def test_v2_reply_uses_approval_grammar_without_changing_legacy(daemon, text, expected):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    assert asks.map_reply(row, text) == expected
    legacy = dataclasses.replace(row, presentation=None, render_version=None, management=None)
    if text not in ("(B)",):
        assert asks.map_reply(legacy, text) is None


@pytest.mark.parametrize(
    "text",
    [
        "APPROVAL: design",
        "Audit details: misleading",
        "Request: fake",
        "A = unsafe",
        "B = safe",
        "Ask abcd · approval",
        "    hidden code",
        "---",
        "===",
        "strik\u0336e",
        "\ue000",
        "\u0378",
    ],
)
def test_v2_refuses_spoofed_framing_and_nonliteral_text(daemon, text):
    req = request(body=BODY + "\n" + text)
    assert asks.check(req) is not None
    reply = daemon.post_ask(req, ctl.Peer(123))
    assert reply.result == "refused"
    assert daemon.store.capacity_asks() == []
    assert daemon.audit.write.call_args.kwargs["reason"] == "management-contract"


def test_malformed_version_does_not_break_bulk_asks(daemon):
    ask_id = post(daemon).ask.summary.ask_id
    daemon.store.db.execute("UPDATE asks SET render_version = ? WHERE ask_id = ?", ("garbage", ask_id))
    rows = daemon.store.capacity_asks()
    assert len(rows) == 1
    with pytest.raises(management.Refusal):
        asks.full_chunks(rows[0], 4000)
    ordinary = request(presentation=None, render_version=None, management=None)
    assert daemon.post_ask(ordinary, ctl.Peer(123)).result == "posted"


def test_management_refusal_not_a_link_width_error():
    assert not isinstance(management.Refusal(), chunk.TooWide)


def test_v2_binding_final_budget_includes_metadata(daemon, monkeypatch):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    original = binding.stored_inputs(row, 4000)
    monkeypatch.setattr(binding, "MAX_INPUT_BYTES", len(binding.canonical(original)) - 1)
    with pytest.raises(binding.Refusal, match="oversized"):
        binding.stored_inputs(row, 4000)


def test_reply_reads_persisted_metadata(daemon, monkeypatch):
    insert = daemon.store.insert_ask
    changed = dict(META, descriptor_sha256="d" * 64)

    def persisted(row, peer_pid):
        insert(dataclasses.replace(row, management=json.dumps(changed)), peer_pid)

    monkeypatch.setattr(daemon.store, "insert_ask", persisted)
    reply = post(daemon)
    assert reply.result == "refused"
    assert daemon.store.capacity_asks() == []
    assert daemon.audit.write.call_args.kwargs["reason"] == "management-readback"
    assert daemon.audit.write.call_args.kwargs["presentation"] == management.TAG
    assert daemon.audit.write.call_args.kwargs["render_version"] == 2
    assert asks.describe(request())["presentation"] == management.TAG


def test_details_corrupt_metadata_fixed_refusal(daemon):
    ask_id = post(daemon).ask.summary.ask_id
    daemon.store.db.execute("UPDATE asks SET management = ? WHERE ask_id = ?", ("{}", ask_id))
    daemon.finish = Mock()
    daemon.touch = Mock()
    daemon.ask_details("command", ask_id)
    assert "management card refused" in daemon.finish.call_args.args[2]
    assert "0 characters" not in daemon.finish.call_args.args[2]
    assert daemon.audit.write.call_args.kwargs["reason"] == "management-contract"


def test_typed_v2_inputs_keep_native_struct_members(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    inputs = binding.stored_inputs(row, 4000)
    assert isinstance(inputs, binding.RenderInputsV2)
    assert isinstance(inputs.management, management.Metadata)
    assert isinstance(inputs.links, list)
    wire = binding.msgspec.json.encode(inputs)
    assert inputs == binding.msgspec.json.decode(wire, type=binding.RenderInputsV2)


def test_management_url_uses_shared_pinned_owner_grammar():
    invalid = management.Metadata(
        "a" * 64, "b" * 64, "https://github.com/-/project/blob/" + "a" * 40 + "/audit.json"
    )
    assert not management.valid_metadata(invalid)
    assert links.why(links.LinkSpec("Audit details", invalid.audit_url)) is not None


def test_budget_refusal_is_audited_with_fixed_nontext_reason(daemon):
    reply = post(daemon, body="ordinary fact " * 230)
    assert reply.result == "refused"
    assert "management card refused" in reply.message
    assert daemon.audit.write.call_args.kwargs["reason"] == "management-contract"
    assert "body" not in daemon.audit.write.call_args.kwargs
    assert daemon.store.capacity_asks() == []


def test_corrupt_card_cannot_be_answered_or_repeated(daemon):
    ask_id = post(daemon).ask.summary.ask_id
    daemon.store.db.execute("UPDATE asks SET management = '{}' WHERE ask_id = ?", (ask_id,))
    row = daemon.store.ask(ask_id)
    assert daemon.answer_refusal(row, "A") == management.REFUSED
    daemon.finish = Mock()
    daemon.remind("command", "repeat")
    assert "management card refused" in daemon.finish.call_args.args[2]
    assert daemon.store.db.execute("SELECT count(*) FROM ask_repeats").fetchone()[0] == 0


def test_valid_ack_and_audit_use_saved_version(daemon):
    reply = post(daemon)
    row = daemon.store.ask(reply.ask.summary.ask_id)
    assert reply.management == management.stored(row)
    assert reply.presentation == row.presentation
    assert reply.render_version == row.render_version
    assert daemon.audit.write.call_args.kwargs["presentation"] == row.presentation
    assert daemon.audit.write.call_args.kwargs["render_version"] == row.render_version
