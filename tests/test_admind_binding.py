"""Binding evidence from disposable SQLite only; no native queue, agents or relay services."""
import asyncio
import dataclasses
import json
import sqlite3
from pathlib import Path

import msgspec
import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, make_settings, operator

from heterodyne.admind import approvals, asks, binding, cli, ctl
from heterodyne.admind.daemon import Admind
from heterodyne.admind.store import AskRow, Store

ID = "k7m2"
MID = "ab" * 32
AT = "2026-10-16T20:00:00+00:00"
POLICY = (operator("op", OPERATOR_HEX),)


@pytest.fixture
def store(tmp_path: Path):
    value = Store(tmp_path / "state" / "db.sqlite")
    try:
        yield value
    finally:
        value.close()


def seed(store: Store, **changes) -> AskRow:
    body = ("Bounded operation only. Preserve all existing approval and task gates; "
            "this test changes no external state.")
    row = AskRow(ID, "question", "controller", "Proceed?", body, None, None,
                 None, None, False, 1, "open", None, None, AT, AT,
                 question_form="choices", choices=json.dumps({"choices": ["Proceed", "Hold"],
                                                              "recommended": "B"}))
    row = dataclasses.replace(row, **changes)
    parts, truncated = asks.card_chunks(row, 4000)
    row = dataclasses.replace(row, card_parts=len(parts), truncated=truncated)
    store.insert_ask(row, None)
    for i, text in enumerate(parts):
        store.enqueue(f"ask:{ID}:{i}", text, None, ownership_kind="system")
    return row


def read(store: Store):
    return binding.read(store, ID, 4000, lambda: POLICY)


def sent(store: Store, prefix: str = f"ask:{ID}") -> None:
    for seq, in store.db.execute("SELECT seq FROM outbox WHERE key GLOB ?", (prefix + ":*",)):
        store.mark_sent(seq, MID)


def answer(store: Store, mid: str = MID, name: str = "op", key: str = OPERATOR_HEX,
           text: str = "A", choice: str | None = "A") -> None:
    store.claim_inbound(mid, sender_key=key)
    store.add_answer(ID, "answer", name, mid, text, choice)


def refused(store: Store, code: str) -> None:
    result = read(store)
    assert result.result == "refused" and result.code == code
    assert result.binding is None
    assert result.message == "ask.binding.v1 unavailable"


def test_binding_request_is_closed_and_distinct() -> None:
    request = msgspec.json.decode(b'{"type":"ask.binding.v1","ask_id":"K7M2"}', type=asks.AskRequest)
    assert isinstance(request, asks.AskBinding)
    assert asks.check(request) is None
    assert asks.describe(request) == {"op": "ask.binding.v1", "ask_id": ID}
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"type":"ask.binding.v1","ask_id":"k7m2","policy":[]}',
                            type=asks.AskRequest)
    assert asks.check(asks.AskBinding("../db")) is not None


def test_original_reply_bytes_unchanged() -> None:
    assert msgspec.json.encode(asks.AskReply("ok", "old")) == (
        b'{"result":"ok","message":"old","ask":null,"asks":null}')


def test_complete_input_digest_actual_recommendation_and_delivery(store: Store) -> None:
    row = seed(store)
    result = read(store)
    assert result.result == "ok" and result.binding is not None
    envelope = result.binding
    assert envelope.inputs.recommended == "B"
    assert envelope.inputs.choices == ["Proceed", "Hold"]
    assert envelope.inputs.body == row.body
    assert envelope.render_input_sha256 == binding.sha256(envelope.inputs)
    assert not envelope.delivered
    assert envelope.policy_sha256 == binding.sha256(envelope.policy)
    assert envelope.read_at_boottime_ns > 0
    assert len(envelope.boot_id) == 36
    sent(store)
    result = read(store)
    assert result.binding is not None and result.binding.delivered
    assert result.binding.card_parts[0].message_id == MID
    decoded = msgspec.json.decode(msgspec.json.encode(result), type=binding.AskBindingReply)
    assert decoded == result


def test_query_does_not_write_even_when_sqlite_denies_all_writes(store: Store) -> None:
    seed(store)
    answer(store)
    before = list(store.db.iterdump())
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
    store.db.set_authorizer(
        lambda action, *args: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
    try:
        assert read(store).result == "ok"
    finally:
        store.db.set_authorizer(None)
    assert list(store.db.iterdump()) == before
    assert not store.db.in_transaction


@pytest.mark.parametrize("status", asks.ACTIVE + asks.TERMINAL)
def test_all_actual_ask_statuses_are_observations(store: Store, status: str) -> None:
    seed(store)
    store.db.execute("UPDATE asks SET status=?", (status,))
    result = read(store)
    assert result.binding is not None and result.binding.status == status


@pytest.mark.parametrize("status", ["delayed", "suppressed", "quarantined", "failed"])
def test_unsent_guard_status_is_not_a_delivery_receipt(store: Store, status: str) -> None:
    seed(store)
    store.db.execute("UPDATE outbox SET status=?", (status,))
    result = read(store)
    assert result.binding is not None
    assert not result.binding.delivered and result.binding.card_parts[0].status == status


@pytest.mark.parametrize(("sql", "params", "code"), [
    ("DELETE FROM outbox", (), "incomplete"),
    ("UPDATE outbox SET text='forged card'", (), "render-mismatch"),
    ("UPDATE asks SET truncated=1", (), "truncated"),
    ("UPDATE asks SET choices=?", ('{"choices":[7],"recommended":"B"}',), "invalid-state"),
    ("UPDATE outbox SET status='sent', message_id='not-a-native-id'", (), "incomplete"),
    ("UPDATE asks SET body=?", ("x" * (binding.MAX_INPUT_CHARS + 1),), "oversized"),
    ("UPDATE outbox SET text=?", ("x" * (binding.MAX_BYTES // 2 + 1),), "oversized"),
    ("UPDATE outbox SET text=?", ("nsec1" + "q" * 58,), "redacted"),
])
def test_refusals_are_typed_bounded_and_never_echo_data(store: Store, sql, params, code) -> None:
    seed(store)
    store.db.execute(sql, params)
    refused(store, code)
    assert len(msgspec.json.encode(read(store))) < 200


def test_extra_chunk_cannot_be_hidden(store: Store) -> None:
    seed(store)
    store.enqueue(f"ask:{ID}:99", "extra", None, ownership_kind="system")
    refused(store, "incomplete")


def test_missing_is_not_empty_complete_history(store: Store) -> None:
    refused(store, "missing")


def test_answer_history_keeps_contrary_and_revoked_actors(store: Store) -> None:
    seed(store)
    answer(store)
    answer(store, "bc" * 32, "former", SECOND_HEX, "B: do not proceed", "B")
    result = read(store)
    assert result.binding is not None
    first, contrary = result.binding.answers
    assert first.current_policy and first.message_id == MID and first.choice == "A"
    assert not contrary.current_policy and contrary.choice == "B"
    assert contrary.sender_key == SECOND_HEX and contrary.operator == "former"
    assert result.binding.answer_history_complete


@pytest.mark.parametrize("sql", [
    "DELETE FROM inbound", "UPDATE inbound SET sender_key=NULL",
    "UPDATE ask_answers SET seq=2", "UPDATE ask_answers SET at='not-a-time'",
])
def test_history_cannot_claim_completeness_without_provenance(store: Store, sql: str) -> None:
    seed(store)
    answer(store)
    store.db.execute(sql)
    assert read(store).result == "refused"


def test_history_limits_checked_before_materializing(store: Store) -> None:
    seed(store)
    for number in range(asks.MAX_ANSWERS + 1):
        answer(store, f"{number:064x}")
    refused(store, "oversized")


def test_policy_failure_and_mid_read_change_have_no_cached_fallback(store: Store) -> None:
    seed(store)
    def unavailable():
        raise OSError("must not echo private path")
    failed = binding.read(store, ID, 4000, unavailable)
    assert failed.code == "policy-unavailable" and "private" not in failed.message
    rows = iter([POLICY, (operator("other", SECOND_HEX),)])
    changed = binding.read(store, ID, 4000, lambda: next(rows))
    assert changed.code == "policy-unavailable"
    assert binding.read(store, ID, 4000, lambda: POLICY * 2).code == "policy-unavailable"


def test_clock_failure_has_no_wall_clock_substitute(store: Store, monkeypatch) -> None:
    seed(store)
    def unavailable():
        raise binding.Refusal("clock-unavailable")
    monkeypatch.setattr(binding, "clock", unavailable)
    refused(store, "clock-unavailable")


def test_full_repeat_proves_delivery_separately(store: Store) -> None:
    row = seed(store)
    request = "ef" * 32
    parts = asks.full_chunks(row, 4000)
    store.db.execute("INSERT INTO ask_repeats(ask_id,request,parts,created_at) VALUES(?,?,?,?)",
                     (ID, request, len(parts), AT))
    prefix = f"askr:{ID}:{request}"
    for number, text in enumerate(parts):
        store.enqueue(f"{prefix}:{number}", text, None, ownership_kind="system")
    sent(store, prefix)
    result = read(store)
    assert result.binding is not None and result.binding.delivered
    assert result.binding.delivery_source == "repeat"
    assert all(p.key.startswith(prefix) for p in result.binding.card_parts)


def test_decision_attempt_not_misreported_as_native_success(store: Store) -> None:
    seed(store)
    store.claim_inbound(MID, sender_key=OPERATOR_HEX)
    assert store.begin_attempt(approvals.Attempt(ID, MID, "approve", "op", "marmot:id:" + MID,
                                                "cd" * 32, "bounded reason"))
    result = read(store)
    assert result.binding is not None
    attempt, = result.binding.decision_attempts
    assert attempt.settled is None and attempt.exit_status is None
    assert attempt.message_id == MID and attempt.current_policy
    assert result.binding.answers == []
    assert result.binding.status == "deciding"


def test_daemon_uses_serialized_binding_and_normalizes_id(store: Store, tmp_path: Path) -> None:
    seed(store)
    daemon = Admind.__new__(Admind)
    daemon.store = store
    daemon.s = make_settings(tmp_path)
    daemon.load_operators = lambda: POLICY
    async def exercise():
        daemon.ask_post_lock = asyncio.Lock()
        await daemon.ask_post_lock.acquire()
        task = asyncio.create_task(daemon.on_ask(asks.AskBinding("K7M2"), ctl.Peer(1)))
        await asyncio.sleep(0)
        assert not task.done()
        daemon.ask_post_lock.release()
        return await task
    assert asyncio.run(exercise()).result == "ok"


def test_cli_uses_typed_daemon_rpc_only(tmp_path: Path, monkeypatch, capsys) -> None:
    settings = make_settings(tmp_path)
    async def request(path, req, timeout, **kwargs):
        assert path == settings.state_dir / asks.ASK_SOCKET
        assert isinstance(req, asks.AskBinding) and req.ask_id == ID
        assert kwargs["reply_type"] is binding.AskBindingReply
        return binding.AskBindingReply("refused", "ask.binding.v1 unavailable", code="incomplete")
    monkeypatch.setattr(cli.ctl, "request", request)
    assert cli.ask_binding_command(settings, ID) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "incomplete" and output["binding"] is None


def seed_approval(store: Store) -> AskRow:
    native = approvals.Readout(
        format=1, busy=False, id="btq-ab12c", status="open", labels=["kind:approval"],
        kind_approval=True, digest="cd" * 32, posted_digest=None, gaps=[], design_review_valid=True,
        adr_revision=None, approvers=["op"], decision=None, approved_by=None, approved_digest=None,
        denied_by=None, denied_digest=None, via=None, via_ref=None, decided=False, gate_valid=None,
        gate_reasons=[], gate_preview=approvals.GatePreview(True, []), links=[],
        readout={"title": ["title:", "  │ Approval test"],
                 "ask": ["  effect:", "    - │ bounded synthetic effect"],
                 "description": ["description:", "  │ Preserve all gates."]}, purpose="design")
    row = AskRow(ID, "approval", "controller", "Approval test", "", None, None, native.id,
                 native.digest, False, 1, "open", None, None, AT, AT)
    card = asks.approval_card(row, native, 4000, (2, 1))
    assert isinstance(card, asks.ApprovalCard)
    row = dataclasses.replace(row, body=asks.approval_body(native, card), card_parts=len(card.card_chunks))
    store.insert_ask(row, None)
    for i, text in enumerate(card.card_chunks):
        store.enqueue(f"ask:{ID}:{i}", text, None, ownership_kind="system")
    return row


def test_approval_binds_actual_stored_readout_and_card_without_new_decision(store: Store) -> None:
    row = seed_approval(store)
    sent(store)
    result = read(store)
    assert result.binding is not None and result.binding.delivered
    assert result.binding.inputs.bead == row.bead and result.binding.inputs.digest == row.digest
    assert json.loads(result.binding.inputs.body)["readout"]["digest"] == row.digest
    assert "2" in result.binding.card_parts[0].text
    assert result.binding.answers == [] and result.binding.decision_attempts == []


@pytest.mark.parametrize("mutation", ["missing-readout", "wrong-bead", "changed-card"])
def test_approval_stored_proof_is_closed_and_matches_outbox(store: Store, mutation: str) -> None:
    row = seed_approval(store)
    value = json.loads(row.body)
    if mutation == "missing-readout":
        del value["readout"]
    elif mutation == "wrong-bead":
        value["readout"]["id"] = "btq-wrong"
    else:
        value["details"][0] += "\nchanged"
    store.db.execute("UPDATE asks SET body=?", (json.dumps(value),))
    assert read(store).result == "refused"


def test_cli_parser_requires_json_and_does_not_take_evidence_paths() -> None:
    parsed = cli.build_parser().parse_args(["ask", "binding", ID, "--json"])
    assert parsed.ask_command == "binding" and parsed.ask_id == ID
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ask", "binding", ID])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["ask", "binding", ID, "--json", "--policy", "untrusted.json"])


def test_snapshot_refuses_nested_writer_and_releases_after_exception(store: Store) -> None:
    seed(store)
    with store.transaction():
        assert read(store).code == "invalid-state"
    with pytest.raises(ValueError), store.read_snapshot():
        raise ValueError("synthetic failure")
    assert not store.db.in_transaction
    assert read(store).result == "ok"


def test_approval_cannot_rebind_forged_matching_details_and_outbox(store: Store) -> None:
    row = seed_approval(store)
    value = json.loads(row.body)
    value["details"][0] = value["details"][0].replace("Preserve all gates.", "Remove all gates.")
    store.db.execute("UPDATE asks SET body=?", (json.dumps(value),))
    store.db.execute("UPDATE outbox SET text=?", (value["details"][0],))
    refused(store, "render-mismatch")


@pytest.mark.parametrize(("form", "choices", "recommended"), [
    ("yes-no", [], "no"), (None, [], None),
])
def test_supported_nonchoice_forms_keep_actual_recommendation(
        store: Store, form, choices, recommended) -> None:
    # Legacy unstructured questions remain readable when the actual legacy posting policy allows them.
    seed(store, question_form=form,
         choices=None if form is None else json.dumps({"choices": choices, "recommended": recommended}))
    result = read(store)
    assert result.binding is not None
    assert result.binding.inputs.form == form and result.binding.inputs.recommended == recommended


def test_snapshot_stays_consistent_with_independent_wal_writer(store: Store) -> None:
    seed(store)
    other = sqlite3.connect(store.db.execute("PRAGMA database_list").fetchone()[2], isolation_level=None)
    try:
        with store.read_snapshot() as view:
            before = view.execute("SELECT title FROM asks").fetchone()[0]
            other.execute("UPDATE asks SET title='Changed concurrently'")
            assert view.execute("SELECT title FROM asks").fetchone()[0] == before
        assert store.db.execute("SELECT title FROM asks").fetchone()[0] == "Changed concurrently"
    finally:
        other.close()


@pytest.mark.parametrize("kind", ["question", "merge"])
@pytest.mark.parametrize("repeat", [False, True])
def test_chunk_boundary_redaction_is_not_complete_evidence(store: Store, repeat: bool, kind: str) -> None:
    # Longer than the AWS token boundary: safe whole, but a cut can expose a token suffix.
    from heterodyne.admind.redact import redact
    token = "AKIA" + "ABCDEFGHIJKLMNOPQRST"
    base = seed(store) if kind == "question" else seed(
        store, kind="merge", question_form=None, choices=None,
        pr_url="https://github.com/example/project/pull/1", head_sha="ab" * 20)
    for pad in range(400):
        row = dataclasses.replace(base, body="w" * (120 + pad) + " " + token)
        parts, truncated = asks.card_chunks(row, 200)
        assert redact(asks.context(row)) == asks.context(row)
        if any("<redacted" in part for part in parts):
            break
    else:
        pytest.fail("fixture did not expose post-chunk redaction")
    if repeat:
        with pytest.raises(binding.Refusal) as exc:
            binding._expected(row, 200, True)
        assert exc.value.code == "redacted"
    else:
        store.db.execute("UPDATE asks SET body=?,card_parts=?,truncated=?", (row.body, len(parts), truncated))
        store.db.execute("DELETE FROM outbox")
        for i, part in enumerate(parts):
            store.enqueue(f"ask:{ID}:{i}", part, None, ownership_kind="system")
        result = binding.read(store, ID, 200, lambda: POLICY)
        assert result.code == "redacted" and result.binding is None


def test_binding_exposes_native_outcome_and_current_attempt(store: Store) -> None:
    seed(store)
    store.claim_inbound(MID, sender_key=OPERATOR_HEX)
    assert store.begin_attempt(approvals.Attempt(ID, MID, "approve", "op", "marmot:id:" + MID,
                                                "cd" * 32, None))
    result = read(store)
    assert result.binding is not None
    assert result.binding.current_attempt == MID
    assert result.binding.outcome is None and result.binding.decided_by is None
    assert store.close_attempt(ID, MID, expect_status="deciding", settled="recorded", exit_status=0,
                               new_status="approved", outcome="Approved", decided_by="op")
    result = read(store)
    assert result.binding is not None
    assert result.binding.current_attempt is None
    assert result.binding.outcome == "Approved" and result.binding.decided_by == "op"
    assert result.binding.decision_attempts[0].settled == "recorded"


@pytest.mark.parametrize("kind", ["answer", "note"])
def test_overlong_history_is_oversized(store: Store, kind: str) -> None:
    seed(store)
    if kind == "answer":
        answer(store, text="w" * (asks.MAX_ANSWER + 1))
    else:
        store.claim_inbound(MID, sender_key=OPERATOR_HEX)
        store.begin_attempt(approvals.Attempt(ID, MID, "approve", "op", "ref", "cd" * 32,
                                              "w" * (asks.MAX_ANSWER + 1)))
    refused(store, "oversized")


@pytest.mark.parametrize("failure", ["invalid-id", "old", "unavailable", "malformed", "timeout"])
def test_cli_always_emits_fixed_typed_refusal(tmp_path: Path, monkeypatch, capsys, failure: str) -> None:
    async def request(*args, **kwargs):
        if failure == "old":
            return binding.AskBindingReply("refused", "untrusted daemon text")
        if failure == "unavailable":
            raise ctl.CtlUnavailable("private details")
        if failure == "malformed":
            raise ctl.CtlInvalidReply("private details")
        raise TimeoutError("private details")
    monkeypatch.setattr(ctl, "request", request)
    result = cli.ask_binding_command(make_settings(tmp_path), "invalid/id" if failure == "invalid-id" else ID)
    assert result in (1, 69)
    output = capsys.readouterr()
    reply = json.loads(output.out)
    assert reply["result"] == "refused" and reply["code"] is not None
    assert reply["message"] == "ask.binding.v1 unavailable" and reply["binding"] is None
    assert "private" not in output.out and "untrusted" not in output.out


@pytest.mark.parametrize("payload", [b"{", b"", b"x" * 1000,
    b'{"result":"refused","message":"future","code":"future-version"}'])
def test_real_socket_bad_framing_and_schema_are_typed(tmp_path: Path, payload: bytes) -> None:
    async def exercise():
        async def respond(reader, writer):
            await reader.readline()
            writer.write(payload + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        path = tmp_path / "ask.sock"
        server = await asyncio.start_unix_server(respond, path=str(path))
        async with server:
            with pytest.raises(ctl.CtlInvalidReply):
                await ctl.request(path, asks.AskBinding(ID), .1,
                                  reply_type=binding.AskBindingReply, max_reply=500)
    asyncio.run(exercise())


def test_connected_response_timeout_is_distinct_from_unreachable(tmp_path: Path) -> None:
    async def exercise():
        finished = asyncio.Event()
        async def respond(reader, writer):
            await reader.readline()
            await reader.read()
            writer.close()
            await writer.wait_closed()
            finished.set()
        path = tmp_path / "ask.sock"
        server = await asyncio.start_unix_server(respond, path=str(path))
        async with server:
            with pytest.raises(ctl.CtlResponseTimeout):
                await ctl.request(path, asks.AskBinding(ID), .01, reply_type=binding.AskBindingReply)
            await asyncio.wait_for(finished.wait(), 1)
        with pytest.raises(ctl.CtlUnavailable) as exc:
            await ctl.request(tmp_path / "absent.sock", asks.AskBinding(ID))
        assert not isinstance(exc.value, (ctl.CtlInvalidReply, ctl.CtlResponseTimeout))
    asyncio.run(exercise())


def test_binding_reader_does_not_occupy_event_loop(store: Store, tmp_path: Path, monkeypatch) -> None:
    import threading
    entered, release = threading.Event(), threading.Event()
    def slow_read(*args):
        entered.set()
        assert release.wait(2)
        return binding.AskBindingReply("refused", "ask.binding.v1 unavailable", code="incomplete")
    monkeypatch.setattr(binding, "read", slow_read)
    daemon = object.__new__(Admind)
    daemon.store, daemon.s, daemon.load_operators = store, make_settings(tmp_path), lambda: POLICY
    async def exercise():
        daemon.ask_post_lock = asyncio.Lock()
        task = asyncio.create_task(daemon.on_ask(asks.AskBinding(ID), ctl.Peer(1)))
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            assert not task.done() and daemon.ask_post_lock.locked()
        finally:
            release.set()
        assert (await task).result == "refused"
    asyncio.run(exercise())


def test_missing_approval_digest_is_invalid_state(store: Store) -> None:
    seed_approval(store)
    store.db.execute("UPDATE asks SET digest=NULL")
    refused(store, "invalid-state")


def test_dangling_current_attempt_is_incomplete(store: Store) -> None:
    seed(store)
    store.db.execute("UPDATE asks SET attempt=?", (MID,))
    refused(store, "incomplete")


@pytest.mark.parametrize("text", ["Approve manifest " + "cd" * 32, "A\r\n"])
def test_free_prose_digest_or_controls_never_gain_identifier_exemption(store: Store, text: str) -> None:
    seed(store)
    answer(store, text=text)
    before = list(store.db.iterdump())
    refused(store, "redacted")
    assert list(store.db.iterdump()) == before


def test_later_posting_rules_fail_closed_for_legacy_formless_card(store: Store, monkeypatch) -> None:
    seed(store, question_form=None, choices=None)
    assert read(store).result == "ok"
    monkeypatch.setattr(asks, "REQUIRE_QUESTION_FORM", True)
    refused(store, "invalid-state")


def test_truncated_original_stays_refused_after_full_repeat(store: Store) -> None:
    row = seed(store, body="Ordinary context.\n" * 400)
    assert row.truncated
    request = "ef" * 32
    parts = asks.full_chunks(row, 4000)
    store.db.execute("INSERT INTO ask_repeats VALUES(?,?,?,?)", (ID, request, len(parts), AT))
    prefix = f"askr:{ID}:{request}"
    for number, text in enumerate(parts):
        store.enqueue(f"{prefix}:{number}", text, None, ownership_kind="system")
    sent(store, prefix)
    refused(store, "truncated")
