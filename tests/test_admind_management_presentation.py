"""Offline source-owned management presentation contract; never task authority."""

import dataclasses
import json
from unittest.mock import Mock

import msgspec
import pytest
from fakes.settings import OPERATOR_HEX, make_settings, operator

from heterodyne.admind import asks, binding, ctl, ingress
from heterodyne.admind.daemon import Admind
from heterodyne.admind.store import Store

URL = "https://github.com/example/project/blob/" + "a" * 40 + "/audit.json"
META = {"request_id": "b" * 64, "descriptor_sha256": "c" * 64, "audit_url": URL}
BODY = (
    "Before: blocked; unassigned\nAfter: open; unassigned\n"
    "Why: prerequisite repair complete.\nRemaining holds: none.\n"
    "Effect: normal pickup; no claim, worker start or review-budget reset."
)


def request(**changes):
    data = {
        "type": "post",
        "kind": "question",
        "title": "Reopen btq-example",
        "body": BODY,
        "form": "choices",
        "choices": ["Perform this exact change", "Leave unchanged and held"],
        "recommended": "A",
        "presentation": "task-management-consent-v1",
        "render_version": 2,
        "management": META,
    }
    data.update(changes)
    return msgspec.json.decode(json.dumps(data), type=asks.AskPost)


@pytest.fixture
def daemon(tmp_path):
    d = Admind.__new__(Admind)
    d.store = Store(tmp_path / "state" / "db.sqlite")
    d.s = make_settings(tmp_path)
    d.audit = Mock()
    d.latched = lambda: False
    d.ingress = ingress.Observation()

    # Use actual transactional outbox API; no network, fake authority or native mutations.
    def post_parts(prefix: str, parts: list[str], lane: int = 1, *, owner: tuple[str, ...] | None) -> None:
        for i, text in enumerate(parts):
            d.store.enqueue(f"{prefix}:{i}", text, None, ownership_kind="system")

    d.post_parts = post_parts
    yield d
    d.store.close()


def post(daemon, **changes):
    req = request(**changes)
    assert asks.check(req) is None
    return daemon.post_ask(req, ctl.Peer(123))


def test_label_exact_persistence_and_closed_v2_binding(daemon):
    reply = post(daemon)
    assert reply.result == "posted"
    assert reply.presentation == "task-management-consent-v1" and reply.render_version == 2
    row = daemon.store.ask(reply.ask.summary.ask_id)
    assert row.kind == "question"  # Label grants no native approve-bead authority.
    text, truncated = asks.question_card(row)
    assert not truncated
    assert text.startswith("APPROVAL: Task management\n")
    assert "operation consent" in text and "unverified" in text
    assert "QUESTION" not in text and "❓" not in text
    assert text.count("A = Perform this exact change") == 1
    assert text.count("B = Leave unchanged and held") == 1
    assert "Only a verified management binding authorizes this change." in text
    assert URL in text and len(text) <= 3200 and len(text.splitlines()) <= 36
    result = binding.read(daemon.store, row.ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))
    assert isinstance(result.binding, binding.EnvelopeV2)
    assert result.binding.version == 2
    assert result.binding.inputs.management.descriptor_sha256 == META["descriptor_sha256"]
    assert result.binding.inputs.presentation == reply.presentation
    assert not result.binding.delivered
    assert result.binding.render_input_sha256 == binding.sha256(result.binding.inputs)
    assert [p.text for p in result.binding.card_parts] == asks.full_chunks(row, 4000)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(msgspec.json.encode(result), type=binding.AskBindingReply)


@pytest.mark.parametrize(
    "changes",
    [
        {"recommended": "B"},
        {"kind": "merge"},
        {"presentation": None},
        {"render_version": None},
        {"render_version": 1},
        {"render_version": 3},
        {"management": None},
        {"management": dict(META, audit_url="https://github.com/example/project/blob/main/audit.json")},
        {"management": dict(META, request_id="bad")},
        {"choices": ["Yes", "No"]},
        {"body": BODY + "\n\u202eevil"},
        {"body": BODY + "\n[hidden](https://evil.example)"},
    ],
)
def test_closed_contract_refuses_without_task_or_outbox(daemon, changes):
    req = request(**changes)
    assert asks.check(req) is not None
    assert daemon.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


@pytest.mark.parametrize("body", ["A fact.\n" * 37, "A" * 3200, BODY + "\n" + "nsec1" + "q" * 58])
def test_real_daemon_budget_and_redaction_refuses_before_insert(daemon, body):
    reply = daemon.post_ask(request(body=body), ctl.Peer(123))
    assert reply.result == "refused"
    assert daemon.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_metadata_tamper_cannot_reproduce_delivery(daemon):
    reply = post(daemon)
    row = daemon.store.ask(reply.ask.summary.ask_id)
    daemon.store.db.execute(
        "UPDATE asks SET management=?", (json.dumps(dict(META, descriptor_sha256="d" * 64)),)
    )
    result = binding.read(daemon.store, row.ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))
    assert result.result == "refused" and result.binding is None


def test_reactions_and_details_keep_existing_grammar(daemon):
    reply = post(daemon)
    row = daemon.store.ask(reply.ask.summary.ask_id)
    for emoji in ["👍", "👍🏽", "✅", "❤", "♥"]:
        assert asks.map_reaction(row, emoji) == "A"
    for emoji in ["👎", "❌"]:
        assert asks.map_reaction(row, emoji) == "none"
    details = "\n".join(asks.full_chunks(row, 4000, details=True))
    assert "lgtm" in details and "skin tones" in details and "rejected" in details


def test_native_final_chunk_framing_is_inside_budget(daemon):
    # Base card fits; actual continuation-marker overhead at real chunk size causes refusal.
    daemon.s = dataclasses.replace(daemon.s, chunk_chars=170)
    reply = daemon.post_ask(request(body="A complete material fact.\n" * 21), ctl.Peer(123))
    assert reply.result == "refused"
    assert daemon.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_binding_v2_delivered_authenticated_history_and_closed_reader(daemon):
    reply = post(daemon)
    ask_id = reply.ask.summary.ask_id
    for (seq,) in daemon.store.db.execute("SELECT seq FROM outbox"):
        daemon.store.mark_sent(seq, "ab" * 32)
    daemon.store.claim_inbound("cd" * 32, sender_key=OPERATOR_HEX)
    daemon.store.add_answer(ask_id, "answer", "op", "cd" * 32, "approved", "A")
    value = binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))
    assert isinstance(value.binding, binding.EnvelopeV2)
    assert value.binding.delivered and value.binding.answers[0].current_policy
    assert value.binding.answers[0].choice == "A"
    wire = msgspec.json.decode(msgspec.json.encode(value), type=binding.WireBindingReply)
    assert binding.decode_reply(wire) == value
    malformed = json.loads(msgspec.json.encode(value))
    malformed["binding"]["version"] = 3
    with pytest.raises(ValueError):
        binding.decode_reply(msgspec.json.decode(json.dumps(malformed), type=binding.WireBindingReply))
    malformed["binding"]["version"] = 2
    malformed["binding"]["inputs"]["unexpected"] = "not bound"
    with pytest.raises(ValueError):
        binding.decode_reply(msgspec.json.decode(json.dumps(malformed), type=binding.WireBindingReply))


def test_version2_cli_is_explicit_and_legacy_reader_stays_closed(daemon, monkeypatch, capsys):
    from heterodyne.admind import cli

    reply = post(daemon)
    ask_id = reply.ask.summary.ask_id
    native = binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),))

    async def rpc(path, req, timeout, **kwargs):
        return msgspec.json.decode(msgspec.json.encode(native), type=kwargs["reply_type"])

    monkeypatch.setattr(cli.ctl, "request", rpc)
    assert cli.ask_binding_command(daemon.s, ask_id, version=2) == 0
    assert json.loads(capsys.readouterr().out)["binding"]["version"] == 2


def test_cli_post_metadata_complete_without_inference(tmp_path):
    from heterodyne.admind import cli

    args = cli.build_parser().parse_args(
        [
            "ask",
            "post",
            "--kind",
            "question",
            "--title",
            "Operation",
            "--presentation",
            "task-management-consent-v1",
            "--render-version",
            "2",
            "--request-id",
            META["request_id"],
            "--descriptor-sha256",
            META["descriptor_sha256"],
            "--audit-url",
            URL,
            "--choice",
            "Perform this exact change",
            "--choice",
            "Leave unchanged and held",
            "--recommended",
            "A",
            "--body-file",
            str(tmp_path / "body"),
        ]
    )
    (tmp_path / "body").write_text(BODY)
    req = cli._ask_post(args)
    assert req is not None and req.management is not None
    assert req.management.audit_url == URL and asks.check(req) is None
    plain = msgspec.json.decode(
        json.dumps({"type": "post", "kind": "question", "title": "APPROVAL: Task management", "body": BODY}),
        type=asks.AskPost,
    )
    assert plain.presentation is None


def test_legacy_schema_writer_and_atomic_metadata_failure(tmp_path, monkeypatch):
    import contextlib
    import sqlite3

    from heterodyne.admind import store as native_store

    path = tmp_path / "state" / "db.sqlite"
    path.parent.mkdir()
    # Exact predecessor schema, not a production DB or native queue.
    with sqlite3.connect(path) as db:
        db.executescript(native_store.SCHEMA)
    original = Store.transaction

    @contextlib.contextmanager
    def fault(self):
        with original(self):
            yield
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(asks)")}
            if {"presentation", "render_version", "management"} <= cols:
                raise OSError("injected migration failure")

    with monkeypatch.context() as m:
        m.setattr(Store, "transaction", fault)
        with pytest.raises(OSError, match="injected migration failure"):
            Store(path)
    with sqlite3.connect(path) as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(asks)")}
        assert not {"presentation", "render_version", "management"} & cols
    db = Store(path)
    try:
        row = native_store.AskRow(
            "k7m2",
            "question",
            "local",
            "Legacy question",
            BODY,
            None,
            None,
            None,
            None,
            False,
            1,
            "open",
            None,
            None,
            "now",
            "now",
        )
        db.insert_ask(row, None)
        saved = db.ask("k7m2")
        assert saved is not None
        assert saved.presentation is None and saved.render_version is None
        # A true positional predecessor writer fails wholly when nullable columns are added.
        count = len(list(db.db.execute("PRAGMA table_info(asks)")))
        with pytest.raises(sqlite3.OperationalError):
            db.db.execute(
                "INSERT INTO asks VALUES (" + ",".join("?" * (count - 3)) + ")",  # noqa: S608 - integer count
                [None] * (count - 3),
            )
        assert db.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.parametrize("bad", ["\x00", "\t", "\r", "\u2066", "\u200b", "\ud800", "`code`", "*bold*"])
def test_hostile_material_refuses_unchanged(daemon, bad):
    if bad == "\ud800":
        with pytest.raises(msgspec.DecodeError):
            request(body=BODY + bad)
        direct = msgspec.structs.replace(request(), body=BODY + bad)
        assert asks.check(direct) is not None
    else:
        assert asks.check(request(body=BODY + bad)) is not None


def test_exact_whole_card_character_boundary_includes_real_id(daemon):
    reply = post(daemon)
    row = daemon.store.ask(reply.ask.summary.ask_id)
    text, _ = asks.question_card(row)
    room = 3200 - len(text)
    larger = dataclasses.replace(row, body=row.body + "x" * room)
    assert len(asks.question_card(larger)[0]) == 3200
    assert len(asks.card_chunks(larger, 4000)[0][0]) == 3200
    with pytest.raises(asks.management.Refusal):
        asks.card_chunks(dataclasses.replace(larger, body=larger.body + "x"), 4000)


def test_native_approve_bead_rejects_management_metadata(daemon):
    # A same-UID caller can copy a presentation tag. That cannot become a native design/execution approval.
    assert asks.check(request(kind="approval", bead="btq-example")) is not None
    assert asks.check(request(kind="question", title="Task-management operation")) is None
    reply = post(daemon, title="Task-management operation")
    assert reply.ask.summary.kind == "question"
    assert daemon.store.db.execute("SELECT count(*) FROM asks WHERE kind='approval'").fetchone()[0] == 0


def test_missing_post_metadata_is_not_old_daemon_fallback(daemon, monkeypatch):
    from heterodyne.admind import cli

    async def rpc(path, req, timeout, **kwargs):
        return asks.AskReply("posted", "old unbound reply")

    monkeypatch.setattr(cli.ctl, "request", rpc)
    with pytest.raises(ctl.CtlInvalidReply, match="not acknowledged"):
        cli._ask_request(daemon.s, request())


def test_partial_metadata_unknown_versions_and_tampered_delivery_refuse(daemon):
    reply = post(daemon)
    ask_id = reply.ask.summary.ask_id
    daemon.store.db.execute("UPDATE asks SET render_version=3 WHERE ask_id=?", (ask_id,))
    assert (
        binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),)).result == "refused"
    )
    daemon.store.db.execute("UPDATE asks SET render_version=2, management=NULL WHERE ask_id=?", (ask_id,))
    assert (
        binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),)).result == "refused"
    )
    daemon.store.db.execute("UPDATE asks SET management=? WHERE ask_id=?", (json.dumps(META), ask_id))
    daemon.store.db.execute("UPDATE outbox SET text='forged operation'")
    assert (
        binding.read(daemon.store, ask_id, 4000, lambda: (operator("op", OPERATOR_HEX),)).result == "refused"
    )


def test_metadata_write_failure_rolls_back_ask_and_outbox(daemon):
    import sqlite3

    daemon.store.db.execute(
        "CREATE TRIGGER fail_management BEFORE UPDATE OF management ON asks "
        "BEGIN SELECT RAISE(ABORT, 'injected metadata failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected metadata failure"):
        post(daemon)
    assert daemon.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_card_outbox_failure_rolls_back_paired_metadata(daemon):
    import sqlite3

    daemon.store.db.execute(
        "CREATE TRIGGER fail_card BEFORE INSERT ON outbox "
        "BEGIN SELECT RAISE(ABORT, 'injected card failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected card failure"):
        post(daemon)
    assert daemon.store.db.execute("SELECT count(*) FROM asks").fetchone()[0] == 0
    assert daemon.store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
