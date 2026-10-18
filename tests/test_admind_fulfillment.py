"""Offline fulfillment integration: real temporary SQLite, no processes, transport or native queue."""

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import msgspec
import pytest
from fakes.settings import make_settings

from heterodyne.admind import asks
from heterodyne.admind import fulfillment as f
from heterodyne.admind.store import AskRow, Store

AT = "2026-10-16T21:00:00+00:00"
MID = "a" * 64


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "state" / "store.db")
    yield result
    result.close()


def seed(store, ask_id="abcd", *, status="answered", kind="question"):
    row = AskRow(
        ask_id,
        kind,
        "controller",
        "Complete exact work",
        "Original context retained.",
        None,
        None,
        None,
        None,
        False,
        1,
        status,
        None,
        None,
        AT,
        AT,
    )
    with store.transaction():
        store.insert_ask(row, 42)
        if status == "answered":
            store.add_answer(ask_id, "answer", "Liam", hashlib.sha256(ask_id.encode()).hexdigest(), "A", "A")
    return row


def binding(store, ask_id="abcd"):
    actual = store.fulfillment.binding(ask_id)
    actual["consumed_answers"] = actual.pop("answer_ids")
    actual["requested_operation_sha256"] = "b" * 64
    actual["original_authority_sha256"] = "c" * 64
    return msgspec.convert(actual, type=f.Binding)


def completion(store, ask_id="abcd", **kwargs):
    return f.Completion(
        "operation-completion-v1",
        binding(store, ask_id),
        "controller",
        "operation:1",
        "completed",
        "d" * 64,
        [],
        **kwargs,
    )


def apply(store, receipt=None):
    return store.fulfillment.apply(receipt or completion(store), uid=os.getuid(), pid=123)


def declaration(store, ask_id="abcd"):
    return f.Declaration(
        "operator-confirmation-v1", binding(store, ask_id), "Completed exact requested work.", []
    )


def deliver(store, intent):
    rows = store.db.execute(
        "SELECT seq FROM outbox WHERE key LIKE ? ORDER BY seq", (f"fulfill-intent:{intent}:%",)
    ).fetchall()
    for i, (seq,) in enumerate(rows):
        store.db.execute("UPDATE outbox SET status='sent',message_id=? WHERE seq=?", (f"notice-{i}", seq))
    return "notice-0"


def test_release_capacity_preserves_original_dto_and_hourly_budget(store):
    row = seed(store)
    before = msgspec.json.encode(asks.view(row, False, store.answers(row.ask_id)))
    assert len(store.capacity_asks()) == 1
    posted = store.ask_posted_since("2020")
    result = apply(store)
    assert result.result == "applied"
    assert len(store.capacity_asks()) == 0
    assert store.ask_posted_since("2020") == posted
    assert store.ask("abcd").status == "answered"
    assert before == msgspec.json.encode(asks.view(store.ask("abcd"), False, store.answers("abcd")))
    assert [r.ask_id for r in store.asks_with_status("answered")] == [row.ask_id]
    assert [r.ask_id for r in store.inactive_asks()] == [row.ask_id]
    assert msgspec.json.decode(before, type=asks.AskView).summary.status == "answered"


def test_exact_retry_conflict_and_shared_core(store):
    seed(store)
    receipt = completion(store)
    first = apply(store, receipt)
    assert apply(store, receipt).view == first.view
    assert apply(store, receipt).result == "already-applied"
    seed(store, "bcde")
    assert apply(store, completion(store, "bcde")).result == "applied"
    seed(store, "cdef")
    changed = msgspec.structs.replace(completion(store, "cdef"), terminal_result_sha256="e" * 64)
    with pytest.raises(f.Invalid, match="operation-conflict"):
        apply(store, changed)
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone()[0] == 2
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("ask_store_id", "f" * 64),
        ("ask_sha256", "f" * 64),
        ("answers_sha256", "f" * 64),
        ("created_at", "wrong"),
    ],
)
def test_wrong_exact_binding_refuses(store, field, value):
    seed(store)
    receipt = completion(store)
    changed = msgspec.structs.replace(
        receipt, binding=msgspec.structs.replace(receipt.binding, **{field: value})
    )
    with pytest.raises(f.Invalid, match="stale"):
        apply(store, changed)
    assert len(store.capacity_asks()) == 1


@pytest.mark.parametrize(
    "status",
    ["open", "deciding", "uncertain", "approved", "denied", "stale", "superseded", "cancelled", "blocked"],
)
def test_ineligible_status_never_fulfilled(store, status):
    seed(store, status=status)
    b = msgspec.structs.replace(binding(store), consumed_answers=[MID])
    receipt = f.Completion("operation-completion-v1", b, "controller", "op", "completed", "d" * 64, [])
    with pytest.raises(f.Invalid):
        apply(store, receipt)


def test_wrong_uid_and_nonterminal_schema(store):
    seed(store)
    with pytest.raises(f.Invalid, match="unverified-peer"):
        store.fulfillment.apply(completion(store), uid=os.getuid() + 1, pid=123)
    raw = msgspec.json.encode(completion(store)).replace(b'"completed"', b'"issued"')
    with pytest.raises(f.Invalid):
        f.parse(raw, f.Completion)


def test_atomic_rollback_before_commit(store, monkeypatch):
    seed(store)

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(store, "enqueue", crash)
    with pytest.raises(RuntimeError):
        apply(store)
    assert store.fulfillment.stored("abcd") is None
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_audit").fetchone()[0] == 0
    assert len(store.capacity_asks()) == 1


def test_restart_rebuilds_conflict_after_old_writer_new_answer(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    seed(store)
    receipt = completion(store)
    apply(store, receipt)
    identity = store.get("ask_store_id")
    store.close()
    with sqlite3.connect(path) as old:
        old.execute(
            "INSERT INTO ask_answers VALUES ('abcd',2,'answer','Liam',?,'new instructions',?,NULL)",
            ("f" * 64, AT),
        )
        old.execute("UPDATE asks SET status='cancelled' WHERE ask_id='abcd'")
    restored = Store(path)
    try:
        restored.fulfillment.rebuild()  # daemon startup/roll-forward, not each routine CLI open
        assert restored.get("ask_store_id") == identity
        assert restored.fulfillment.view("abcd")["lifecycle"] == "conflict"
        assert len(restored.capacity_asks()) == 1
        row = restored.ask("abcd")
        assert row is not None and row.status == "cancelled"
        with pytest.raises(f.Invalid, match="conflict"):
            apply(restored, receipt)
    finally:
        restored.close()


def test_store_mutation_guards_preserve_answer_and_status(store):
    seed(store)
    apply(store)
    with pytest.raises(ValueError, match="already fulfilled"):
        store.add_answer("abcd", "answer", "Liam", "f" * 64, "B")
    with pytest.raises(ValueError, match="already fulfilled"):
        store.set_ask("abcd", "cancelled")
    assert len(store.answers("abcd")) == 1


def test_concurrent_exact_apply_is_once(store):
    seed(store)
    receipt = completion(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply(store, receipt).result, range(2)))
    assert sorted(results) == ["already-applied", "applied"]
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_operator_route_no_machine_receipt_and_duplicate(store):
    seed(store)
    result = store.fulfillment.propose(
        f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=350
    )
    intent = result.view["intent_id"]
    assert len(store.capacity_asks()) == 1
    with pytest.raises(f.Invalid, match="unbound"):
        store.fulfillment.confirm("unrelated", principal="operator", message_id="f" * 64)
    target = deliver(store, intent)
    result = store.fulfillment.confirm(target, principal="operator", message_id="f" * 64)
    assert result.view["route"] == "operator"
    assert "controller_receipt_sha256" not in result.view["attestation"]
    assert (
        store.fulfillment.confirm(target, principal="operator", message_id="e" * 64).result
        == "already-applied"
    )
    assert len(store.capacity_asks()) == 0


def test_stale_replace_dispose_and_delivered_tombstone(store):
    seed(store)
    req = f.Propose("abcd", declaration(store))
    first = store.fulfillment.propose(req, uid=os.getuid(), chunk_chars=350)
    deliver(store, first.view["intent_id"])
    second = store.fulfillment.propose(
        msgspec.structs.replace(req, replace_intent=first.view["digest"]), uid=os.getuid(), chunk_chars=350
    )
    with pytest.raises(f.Invalid, match="stale-intent"):
        store.fulfillment.confirm("notice-0", principal="operator", message_id="e" * 64)
    with store.transaction():
        store.fulfillment.retire("abcd", second.view["digest"], "operator-withdrawal")
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone()[0] == 2


def test_pending_bounds_no_hourly_or_active_cap_dependency(store):
    seed(store)
    proposal = f.Propose("abcd", declaration(store))
    first = store.fulfillment.propose(proposal, uid=os.getuid(), chunk_chars=1000)
    assert store.fulfillment.propose(proposal, uid=os.getuid(), chunk_chars=1000).view == first.view
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone()[0] == 1
    store.db.execute("UPDATE ask_fulfillment_intents SET created_at='2020-01-01T00:00:00+00:00'")
    deliver(store, first.view["intent_id"])
    with pytest.raises(f.Invalid, match="stale-intent"):
        store.fulfillment.confirm("notice-0", principal="operator", message_id="e" * 64)
    assert apply(store).result == "applied"  # independent controller recovery remains available


@pytest.mark.parametrize(
    "raw",
    [b'{"ask_id":"abcd","ask_id":"efgh"}', b'{"ask_id":NaN}', b"\xff", b'{"ask_id":"abcd","extra":true}'],
)
def test_closed_json(raw):
    with pytest.raises(f.Invalid):
        f.parse(raw, f.Get)


def test_file_nofollow_and_cap(tmp_path):
    target = tmp_path / "receipt"
    target.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(OSError):
        f.read_file(link)
    target.write_bytes(b"x" * (f.MAX_FILE + 1))
    with pytest.raises(f.Invalid, match="oversized"):
        f.read_file(target)


def test_canonical_golden_and_store_identity(store):
    assert f.canonical({"z": ["é", 1], "a": None}) == b'{"a":null,"z":["\xc3\xa9",1]}'
    assert f.digest("fixture", {"a": 1}) == "c9c09a8e978a56e423369adfde31d4336dd1a138907a0078fd2555f25492ade0"


def test_conflicted_receipt_blocks_attempt_before_any_write(store):
    from heterodyne.admind.approvals import Attempt

    seed(store)
    apply(store)
    store.db.execute("UPDATE asks SET status='open' WHERE ask_id='abcd'")
    attempt = Attempt("abcd", "f" * 64, "approve", "Liam", "ref", "digest", None)
    assert store.begin_attempt(attempt) is False
    assert store.ask("abcd").status == "open"
    assert store.db.execute("SELECT COUNT(*) FROM ask_attempts").fetchone()[0] == 0


def test_confirmation_notice_has_request_and_consumed_context(store):
    seed(store)
    result = store.fulfillment.propose(
        f.Propose("abcd", declaration(store)), uid=os.getuid(), chunk_chars=300
    )
    notice = store.db.execute(
        "SELECT notice FROM ask_fulfillment_intents WHERE intent_id=?", (result.view["intent_id"],)
    ).fetchone()[0]
    assert "Original context retained." in notice
    assert "Consumed response context: Liam: A" in notice
    assert "No machine proof" in notice
    assert "does not authorize execution" in notice


@pytest.mark.parametrize("change", ["secret", "oversized", "too-many-lines"])
def test_unsafe_confirmation_material_refuses_atomically(store, change):
    seed(store)
    value = "sk-" + "Q" * 30 if change == "secret" else "x" * 3600 if change == "oversized" else "line\n" * 41
    store.db.execute("UPDATE asks SET body=? WHERE ask_id='abcd'", (value,))
    req = f.Propose("abcd", declaration(store))
    with pytest.raises(f.Invalid, match="notice-redaction-or-bounds"):
        store.fulfillment.propose(req, uid=os.getuid(), chunk_chars=300)
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillment_intents").fetchone()[0] == 0
    assert not store.pending()


def test_pending_cap_does_not_block_apply_or_dispose(store):
    import itertools

    ids = ["".join(chars) for chars in itertools.islice(itertools.product("abcdefgh", repeat=4), 41)]
    for ask_id in ids:
        seed(store, ask_id)
    for ask_id in ids[:40]:
        store.fulfillment.propose(
            f.Propose(ask_id, declaration(store, ask_id)), uid=os.getuid(), chunk_chars=1000
        )
    with pytest.raises(f.Invalid, match="pending-cap"):
        store.fulfillment.propose(
            f.Propose(ids[40], declaration(store, ids[40])), uid=os.getuid(), chunk_chars=1000
        )
    assert apply(store, completion(store, ids[40])).result == "applied"
    current = store.fulfillment.view(ids[0])["pending"]
    assert store.fulfillment.retire(ids[0], current["digest"], "operator-withdrawal").result == "retired"


def test_backup_preserves_identity_and_receipt(tmp_path):
    source = Store(tmp_path / "source" / "db")
    backup = tmp_path / "backup" / "db"
    backup.parent.mkdir()
    try:
        seed(source)
        receipt = completion(source)
        first = apply(source, receipt)
        with sqlite3.connect(backup) as target:
            source.db.backup(target)
    finally:
        source.close()
    restored = Store(backup)
    try:
        assert restored.fulfillment.view("abcd")["lifecycle"] == "fulfilled"
        assert apply(restored, receipt).view == first.view
        assert restored.capacity_asks() == []
    finally:
        restored.close()


def test_unknown_schema_migration_leaves_original_data(tmp_path):
    path = tmp_path / "state" / "db"
    original = Store(path)
    seed(original)
    original.set("ask_fulfillment_schema", "future")
    original.close()
    with pytest.raises(f.Invalid, match="unsupported-schema"):
        Store(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM asks").fetchone()[0] == "answered"
        assert db.execute("SELECT COUNT(*) FROM ask_answers").fetchone()[0] == 1
        assert db.execute("SELECT value FROM kv WHERE key='ask_fulfillment_schema'").fetchone()[0] == "future"


def test_daemon_local_capability_uid_latch_and_work_lock(tmp_path, monkeypatch):
    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    u = Unit(tmp_path)
    try:
        seed(u.store)
        request = f.Apply(completion(u.store))
        assert (
            run(u.daemon.on_fulfillment(request, ctl.Peer(123, os.getuid(), os.getgid()))).result
            == "unavailable"
        )
        monkeypatch.setattr(f, "ENABLED", True)
        assert run(u.daemon.on_fulfillment(request, ctl.Peer(123))).result == "refused"
        assert (
            run(u.daemon.on_fulfillment(request, ctl.Peer(123, os.getuid() + 1, os.getgid()))).result
            == "refused"
        )
        u.daemon.latch("fixture")

        async def body():
            async with u.daemon.work_lock:
                return await u.daemon.on_fulfillment(request, ctl.Peer(123, os.getuid(), os.getgid()))

        assert run(body()).result == "applied"
        listed = run(u.daemon.on_ask(asks.AskList(), ctl.Peer(123)))
        assert [row.ask_id for row in listed.asks] == ["abcd"]
        assert listed.asks[0].status == "answered"
    finally:
        u.store.close()


@pytest.mark.parametrize("mode", ["allowed", "stranger", "latched", "disabled", "unbound"])
def test_real_inbound_confirmation_uses_normal_auth_gates(tmp_path, monkeypatch, mode):
    from fakes.settings import OPERATOR_HEX
    from test_admind_r1 import STRANGER, Unit, inbound, run

    from heterodyne.marmot.control import ReplyTo

    u = Unit(tmp_path)
    try:
        seed(u.store)
        proposed = u.store.fulfillment.propose(
            f.Propose("abcd", declaration(u.store)), uid=os.getuid(), chunk_chars=350
        )
        assert proposed.view is not None
        intent = proposed.view["intent_id"]
        target = deliver(u.store, intent)
        monkeypatch.setattr(f, "ENABLED", mode != "disabled")
        if mode == "latched":
            u.daemon.latch("fixture")
        event = inbound("!fulfill", "f" * 64, STRANGER if mode == "stranger" else OPERATOR_HEX)
        event = msgspec.structs.replace(event, reply_to=ReplyTo("other" if mode == "unbound" else target))
        run(u.daemon.on_message(event))
        assert u.store.fulfillment.protected("abcd") is (mode == "allowed")
        if mode == "allowed":
            receipt = u.store.fulfillment.stored("abcd")
            assert receipt is not None
            authority = receipt["authority"]
            assert isinstance(authority, dict)
            assert authority["message_id"] == "f" * 64
            assert authority["principal"] == OPERATOR_HEX
            run(u.daemon.on_message(event))
            assert u.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone()[0] == 1
    finally:
        u.store.close()


def test_reconcile_lost_reply_queries_before_retry_and_continuation_is_exact(store, tmp_path, monkeypatch):
    import argparse

    from heterodyne.admind import ctl
    from heterodyne.admind import fulfillment_cli as cli

    seed(store)
    receipt = completion(store)
    path = tmp_path / "receipts.json"
    path.write_bytes(f.canonical([f.builtins(receipt)]))
    args = argparse.Namespace(receipts=path, limit=20, apply=True, continuation=None)
    calls = []
    lost = True

    def send(_settings, req):
        nonlocal lost
        calls.append(type(req).__name__)
        if isinstance(req, f.Get):
            return f.Reply("ok", "view", store.fulfillment.view(req.ask_id))
        result = apply(store, req.receipt)
        if lost:
            lost = False
            raise ctl.CtlUnavailable("lost reply")
        return result

    monkeypatch.setattr(cli, "send", send)
    first = cli.reconcile(make_settings(tmp_path), args)
    assert first["results"] == [{"ask_id": "abcd", "outcome": "unavailable"}]
    assert calls == ["Get", "Apply"]
    args.continuation = first["continuation"]
    second = cli.reconcile(make_settings(tmp_path), args)
    assert second["results"] == [{"ask_id": "abcd", "outcome": "already-applied"}]
    assert calls == ["Get", "Apply", "Get"]
    args.continuation = "0" * 64 + ":0"
    with pytest.raises(f.Invalid, match="continuation"):
        cli.reconcile(make_settings(tmp_path), args)


def test_existing_socket_serves_both_strict_dtos_with_kernel_uid(tmp_path, monkeypatch):
    import asyncio

    from test_admind_r1 import Unit, run

    from heterodyne.admind import ctl

    u = Unit(tmp_path)
    seed(u.store)
    monkeypatch.setattr(f, "ENABLED", True)

    async def body():
        server = ctl.JsonSocketServer(
            tmp_path / "ask.sock",
            u.daemon.on_ask_wire,
            u.audit,
            kind="ask",
            max_request=asks.MAX_REQUEST,
            max_reply=asks.MAX_REPLY,
            request_type=asks.AskRequest | f.Request,
            check=lambda req: (
                f.check(req) if isinstance(req, f.Apply | f.Propose | f.Dispose | f.Get) else asks.check(req)
            ),
            refused=asks.refused,
            failed=asks.failed,
            describe=lambda req: {"op": type(req).__name__},
        )
        await server.start()
        try:
            result = await ctl.request(server.path, f.Apply(completion(u.store)), 5, reply_type=f.Reply)
            assert result.result == "applied"
            assert result.view is not None
            authority = result.view["authority"]
            assert isinstance(authority, dict) and authority["uid"] == os.getuid()
            result = await ctl.request(server.path, asks.AskGet("abcd"), 5, reply_type=asks.AskReply)
            assert result.ask is not None
            assert result.ask.summary.status == "answered"
            assert len(result.ask.answers) == 1
            reader, writer = await asyncio.open_unix_connection(server.path)
            writer.write(b'{"type":"fulfillment.get","ask_id":"abcd","ask_id":"efgh"}\n')
            await writer.drain()
            reply = msgspec.json.decode(await reader.readline(), type=f.Reply)
            assert reply.result == "refused"
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()

    try:
        run(body())
    finally:
        u.store.close()


def test_real_cli_parser_and_fixed_errors(store, tmp_path, monkeypatch, capsys):
    from heterodyne.admind import cli, fulfillment_cli

    seed(store)
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(f.canonical(f.builtins(completion(store))))
    args = cli.build_parser().parse_args(["ask", "fulfillment", "apply", "--receipt", str(receipt), "--json"])
    monkeypatch.setattr(fulfillment_cli, "send", lambda _settings, req: apply(store, req.receipt))
    assert fulfillment_cli.command(make_settings(tmp_path), args) == 0
    assert '"result":"applied"' in capsys.readouterr().out
    receipt.write_text('{"schema":"untrusted-secret-sentinel"}')
    assert fulfillment_cli.command(make_settings(tmp_path), args) == 1
    output = capsys.readouterr()
    assert "untrusted-secret-sentinel" not in output.err
    assert "invalid fulfillment input" in output.err


def test_answer_first_stales_receipt_and_apply_first_refuses_answer(tmp_path):
    from test_admind_r1 import Unit, run

    u = Unit(tmp_path)
    try:
        seed(u.store)
        stale = completion(u.store)
        run(u.say("!answer abcd Additional actual answer"))
        assert len(u.store.answers("abcd")) == 2
        with pytest.raises(f.Invalid, match="stale"):
            apply(u.store, stale)
        assert apply(u.store).result == "applied"
        run(u.say("!answer abcd Must remain original"))
        assert len(u.store.answers("abcd")) == 2
        assert any("Already fulfilled" in text for text in u.texts())
    finally:
        u.store.close()
