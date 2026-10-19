"""R2 adversarial regressions: disposable source fixtures, no operational authority."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import msgspec
import pytest
from fakes.settings import make_settings
from test_admind_management_presentation import BODY, post, request

from heterodyne.admind import asks, binding, cli, ctl, management

pytest_plugins = ("test_admind_management_presentation",)


@pytest.mark.parametrize(
    "fact",
    [
        "Option A = Leave unchanged and held (recommended)",
        "option B=perform",
        "A / no / leave held",
        "deny / perform",
        "Audit  details: misleading",
        "Audit\u00a0details: misleading",
        "A \uff1d Leave unchanged and held",
        "\u0410PPROVAL: task",
        "\U0001f44d = hold",
        "After: o\u0335pen",
        "After: o\u0338pen",
        "After: o\u0332pen",
        "A\u20e0",
        "&#65; = hold",
        "After: &amp; held",
        "After: <b>held</b>",
        "Before: full audit at https://evil.example/approve-audit",
        "Before: full audit at HTTP://evil.example",
        "Before: www.evil.example",
        "Before: ftp://evil.example",
        "Before: mailto:operator@example.org",
    ],
)
@pytest.mark.parametrize("field", ["title", "body"])
def test_unsafe_facts_refuse_before_storage(daemon, fact, field):
    req = request(**{field: fact if field == "title" else BODY + "\n" + fact})
    assert asks.check(req) is not None
    assert daemon.post_ask(req, ctl.Peer(123)).result == "refused"
    assert daemon.store.capacity_asks() == []
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_ascii_facts_preserved_without_sanitization(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    facts = [line[2:] for line in management.lines(row) if line.startswith("| ")]
    assert facts == [row.title, *BODY.split("\n")]


def test_legacy_oversize_precedes_invalid_request(daemon, monkeypatch):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    row = dataclasses.replace(
        row,
        presentation=None,
        render_version=None,
        management=None,
        title="",
        choices=json.dumps({"choices": ["Yes", "No"], "recommended": "A"}),
    )
    monkeypatch.setattr(binding, "MAX_INPUT_BYTES", 1)
    with pytest.raises(binding.Refusal) as error:
        binding.stored_inputs(row, 4000)
    assert error.value.code == "oversized"


def test_malformed_autobump_row_audits_only_when_due(daemon, monkeypatch):
    at = datetime(2026, 10, 18, tzinfo=UTC)
    ask_id = post(daemon).ask.summary.ask_id
    daemon.store.db.execute(
        "UPDATE asks SET management='{}', last_activity_at=? WHERE ask_id=?", (asks.stamp(at), ask_id)
    )
    daemon.s = dataclasses.replace(daemon.s, ask_bump_hours=12)
    daemon.may_post = lambda: True
    monkeypatch.setattr(asks, "now", lambda: at)
    daemon.audit.write.reset_mock()
    daemon.autobump()
    daemon.audit.write.assert_not_called()
    monkeypatch.setattr(asks, "now", lambda: at + timedelta(hours=13))
    daemon.autobump()
    assert daemon.audit.write.call_args.kwargs["skipped"] == [
        {"ask_id": ask_id, "why": "management card refused"}
    ]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("failure", ["invalid-id", "transport", "peer-refusal"])
def test_binding_cli_refusal_domain_is_requested_version(tmp_path, monkeypatch, capsys, version, failure):
    async def peer(*args, **kwargs):
        if failure == "transport":
            raise ctl.CtlUnavailable("offline")
        return binding.AskBindingReply("refused", "ask.binding.v1 unavailable", code="invalid-state")

    monkeypatch.setattr(ctl, "request", peer)
    result = cli.ask_binding_command(
        make_settings(tmp_path), "invalid/id" if failure == "invalid-id" else "abcd", version=version
    )
    assert result != 0
    raw = capsys.readouterr().out
    typ = binding.AskBindingReplyV2 if version == 2 else binding.AskBindingReply
    reply = msgspec.json.decode(raw, type=typ)
    assert reply.message == f"ask.binding.v{version} unavailable"
    assert reply.code == ("transport-unavailable" if failure == "transport" else "invalid-state")
    assert reply.binding is None


def test_legend_distinguishes_literal_b_from_none_of_these(daemon):
    row = daemon.store.ask(post(daemon).ask.summary.ask_id)
    card = "\n".join(management.lines(row))
    assert "B = Leave unchanged and held" in card
    assert "deny / \U0001f44e \u274c = none of these; request stays held." in card
    assert asks.map_reply(row, "B") == "B"
    assert asks.map_reply(row, "deny") == "none"
    assert asks.map_reaction(row, "\U0001f44e") == "none"
