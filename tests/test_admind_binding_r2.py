"""R2 regressions: disposable WAL state and fake sockets; no native queue or transport."""
import json
import sqlite3
import threading

import pytest
from fakes.settings import make_settings
from test_admind_binding import ID, MID, POLICY, answer, read, seed, seed_approval
from test_admind_binding import store as binding_store

from heterodyne.admind import approvals, asks, binding, cli, ctl

store = binding_store


def test_real_snapshot_does_not_block_live_store_and_is_consistent(store, monkeypatch):
    seed(store)
    entered, release = threading.Event(), threading.Event()
    original = binding.delivery
    def delayed(db, row, size):
        entered.set()
        assert release.wait(2)
        return original(db, row, size)
    monkeypatch.setattr(binding, "delivery", delayed)
    result = []
    worker = threading.Thread(target=lambda: result.append(read(store)))
    worker.start()
    assert entered.wait(1)
    try:
        # A synchronous event-loop Store call must remain usable while the REAL snapshot is held.
        store.set("concurrent", "yes")
        store.db.execute("UPDATE asks SET status='cancelled'")
        assert store.get("concurrent") == "yes"
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    before = result[0].binding
    after = read(store).binding
    assert before is not None and before.status == "open"
    assert after is not None and after.status == "cancelled"


def test_render_hash_binds_chunk_configuration(store):
    seed(store)
    first = read(store)
    second = binding.read(store, ID, 3999, lambda: POLICY)
    assert first.binding and second.binding
    assert first.binding.render_input_sha256 != second.binding.render_input_sha256
    assert first.binding.inputs.chunk_chars == 4000


def test_approval_counts_are_explicit_render_inputs(store):
    seed_approval(store)
    envelope = read(store).binding
    assert envelope is not None
    assert envelope.inputs.approval_counts == (2, 1)
    assert binding.sha256(envelope.inputs) != binding.sha256(
        {**binding.msgspec.to_builtins(envelope.inputs), "approval_counts": [1, 1]})


@pytest.mark.parametrize("field", ["labels", "gaps", "approvers", "gate_reasons", "preview"])
def test_raw_stored_approval_prose_is_screened(store, field):
    row = seed_approval(store)
    value = json.loads(row.body)
    token = "native reason " + "ef" * 32
    if field == "preview":
        value["readout"]["gate_preview"]["reasons"] = [token]
    else:
        value["readout"][field] = [token]
    store.db.execute("UPDATE asks SET body=?", (json.dumps(value),))
    assert read(store).code == "redacted"


@pytest.mark.parametrize("code", ["transport-unavailable", "response-timeout", "invalid-reply"])
def test_daemon_cannot_spoof_client_transport_failure(tmp_path, monkeypatch, capsys, code):
    async def request(*args, **kwargs):
        return binding.AskBindingReply("refused", "ignored", code=code)
    monkeypatch.setattr(ctl, "request", request)
    assert cli.ask_binding_command(make_settings(tmp_path), ID) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-reply"


def test_cli_rejects_other_ask_envelope(store, tmp_path, monkeypatch, capsys):
    seed(store)
    reply = read(store)
    async def request(*args, **kwargs):
        return reply
    monkeypatch.setattr(ctl, "request", request)
    assert cli.ask_binding_command(make_settings(tmp_path), "b2c3") == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid-reply"


def test_max_native_unicode_answer_history_fits(store):
    seed(store)
    for i in range(asks.MAX_ANSWER_TOTAL // asks.MAX_ANSWER):
        answer(store, mid=f"{i:064x}", text="🌱" * asks.MAX_ANSWER)
    assert read(store).binding is not None


def test_settlement_stores_safe_outcome_atomically_without_losing_decision(store):
    seed(store)
    store.claim_inbound(MID, sender_key=POLICY[0].hex)
    attempt = approvals.Attempt(ID, MID, "approve", "op", "marmot:id:" + MID, "cd" * 32, None)
    assert store.begin_attempt(attempt)
    text = "Approved; gate rejected digest " + "ef" * 32
    assert store.close_attempt(ID, MID, expect_status="deciding", settled="recorded", exit_status=0,
                               new_status="approved", outcome=text, decided_by="op")
    envelope = read(store).binding
    assert envelope and envelope.status == "approved" and envelope.decided_by == "op"
    assert envelope.outcome is not None and "ef" * 32 not in envelope.outcome
    assert envelope.decision_attempts[0].settled == "recorded"


def test_all_repeats_reuse_one_original_and_one_repeat_render(store, monkeypatch):
    row = seed(store)
    original = binding._expected
    calls = []
    def count_expected(row, chars, repeat):
        calls.append(repeat)
        return original(row, chars, repeat)
    for i in range(binding.MAX_REPEATS):
        request = f"{i:064x}"
        parts = asks.full_chunks(row, 4000)
        store.db.execute("INSERT INTO ask_repeats VALUES(?,?,?,?)", (ID, request, len(parts), row.created_at))
        for j, text in enumerate(parts):
            store.enqueue(f"askr:{ID}:{request}:{j}", text, None, ownership_kind="system")
    monkeypatch.setattr(binding, "_expected", count_expected)
    assert read(store).binding is not None
    assert calls == [False, True]


def test_safe_outcome_and_attempt_are_one_transaction(store):
    seed(store)
    store.claim_inbound(MID, sender_key=POLICY[0].hex)
    attempt = approvals.Attempt(ID, MID, "approve", "op", "marmot:id:" + MID, "cd" * 32, None)
    assert store.begin_attempt(attempt)
    with pytest.raises(RuntimeError), store.transaction():
        assert store.close_attempt(ID, MID, expect_status="deciding", settled="recorded", exit_status=0,
                                   new_status="approved", outcome="gate " + "ef" * 32, decided_by="op")
        raise RuntimeError("crash before commit")
    envelope = read(store).binding
    assert envelope and envelope.status == "deciding" and envelope.outcome is None
    assert envelope.current_attempt == MID and envelope.decision_attempts[0].settled is None
    assert not store.close_attempt(ID, MID, expect_status="open", settled="recorded", exit_status=0,
                                   new_status="approved", outcome="wrong CAS", decided_by="op")
    row = store.ask(ID)
    assert row is not None and row.status == "deciding"


def test_unicode_ascii_escaped_approval_input_budget_is_bytes_not_characters(store):
    row = seed_approval(store)
    value = json.loads(row.body)
    value["readout"]["readout"]["description"] = ["🌱" * 24_000]
    value["details"].append("🌱" * 24_000)
    body = json.dumps(value, ensure_ascii=True)
    assert len(body.encode()) > binding.MAX_INPUT_CHARS
    from dataclasses import replace
    inputs = binding.stored_inputs(replace(row, body=body), 4000)
    assert inputs.body == body


def test_snapshot_cannot_write_native_state(store):
    seed(store)
    with store.read_snapshot() as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE asks SET status='cancelled'")
    envelope = read(store).binding
    assert envelope is not None and envelope.status == "open"
