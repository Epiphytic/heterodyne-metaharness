"""Enabled legacy candidate: disposable real sockets/SQLite, never deployment authority."""

import asyncio
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import asynccontextmanager

import msgspec
import pytest
from test_admind_fulfillment import completion, seed
from test_admind_r1 import Unit, run

from heterodyne.admind import asks, ctl, operations
from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_cli as cli
from heterodyne.admind.cli import build_parser
from heterodyne.admind.store import Store


@pytest.fixture
def unit(tmp_path, monkeypatch):
    value = Unit(tmp_path, fulfillment_enabled=True)
    yield value
    value.store.close()


@asynccontextmanager
async def socket_server(unit):
    server = ctl.JsonSocketServer(
        unit.settings.state_dir / f.SOCKET,
        unit.daemon.on_ask_wire,
        unit.audit,
        kind="ask",
        max_request=asks.MAX_REQUEST,
        max_reply=asks.MAX_REPLY,
        request_type=asks.AskRequest | f.Request | operations.Request,
        check=unit.daemon.check_ask_wire,
        refused=asks.refused,
        failed=asks.failed,
        describe=lambda req: (
            asks.describe(req) if isinstance(req, asks.AskRequest) else {"op": type(req).__name__}
        ),
    )
    await server.start()
    try:
        yield server
    finally:
        await server.close()


def arguments(*args):
    return build_parser().parse_args(["ask", "fulfillment", *args, "--json"])


async def call_cli(unit, capsys, *args):
    result = await asyncio.to_thread(cli.command, unit.settings, arguments(*args))
    return result, json.loads(capsys.readouterr().out)


def migrate_quiesced(unit):
    """Fixture setup with no serving socket or open daemon Store during migration."""
    from heterodyne.admind.fulfillment_projection import migrate

    path = unit.store.path
    unit.store.close()
    offline = Store(path)
    try:
        migrate(offline, apply=True)
    finally:
        offline.close()
    unit.store = Store(path)
    unit.build()


def receipt_file(unit, tmp_path):
    path = tmp_path / "completion.json"
    path.write_bytes(msgspec.json.encode(completion(unit.store)))
    return str(path)


def test_enabled_candidate_requires_explicit_disposable_migration(unit, capsys):
    assert unit.settings.fulfillment_enabled is True
    seed(unit.store)
    before = unit.store.db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()

    async def before_migration():
        async with socket_server(unit) as server:
            code, reply = await call_cli(unit, capsys, "get", "abcd")
            assert code == 1 and reply["result"] == "unavailable"
            assert reply["message"] == "fulfillment-migration-required"
            refused = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Disposable operation. " * 5, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert refused.result == "refused"
            assert refused.message == "fulfillment-migration-required"
            assert unit.store.db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before

    run(before_migration())
    path = unit.store.path
    unit.store.close()  # No serving socket or open daemon Store during the offline migration CLI.
    code = cli.command(unit.settings, arguments("migrate", "--plan"))
    assert code == 0
    capsys.readouterr()
    planned = Store(path)
    try:
        assert planned.db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == before
    finally:
        planned.close()
    code = cli.command(unit.settings, arguments("migrate", "--apply"))
    assert code == 0
    capsys.readouterr()
    unit.store = Store(path)
    unit.build()

    async def after_migration():
        async with socket_server(unit):
            code, reply = await call_cli(unit, capsys, "get", "abcd")
            assert code == 0 and reply["result"] == "ok"

    run(after_migration())


def test_real_cli_completion_capacity_history_restart_and_idempotency(unit, tmp_path, capsys):
    seed(unit.store)
    migrate_quiesced(unit)
    original = msgspec.json.encode(asks.view(unit.store.ask("abcd"), False, unit.store.answers("abcd")))
    receipt = receipt_file(unit, tmp_path)
    assert unit.store.capacity_count() == 1

    async def body():
        async with socket_server(unit) as server:
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "applied"
            assert reply["view"]["authority"]["uid"] == os.getuid()
            assert unit.store.capacity_count() == 0
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "already-applied"
            legacy = await ctl.request(server.path, asks.AskGet("abcd"), 5, reply_type=asks.AskReply)
            assert legacy.ask is not None
            assert msgspec.json.encode(legacy.ask) == original
        path = unit.store.path
        unit.store.close()
        unit.store = Store(path)
        unit.build()
        async with socket_server(unit):
            code, reply = await call_cli(unit, capsys, "get", "abcd")
            assert code == 0 and reply["view"]["lifecycle"] == "fulfilled"
            assert unit.store.capacity_count() == 0
            assert len(unit.store.answers("abcd")) == 1

    run(body())


def test_real_cli_completion_preserves_history_without_answered_queue_cap(unit, tmp_path, capsys):
    seed(unit.store)
    migrate_quiesced(unit)
    for index in range(19):
        seed(unit.store, f"legacy-{index}")
    assert unit.store.capacity_count() == 20
    original = unit.store.db.execute("SELECT ask_id,status FROM asks ORDER BY ask_id").fetchall()
    posted = unit.store.ask_posted_since("2020")
    receipt = receipt_file(unit, tmp_path)

    async def body():
        async with socket_server(unit) as server:
            admitted = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Future disposable operation. " * 4, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert admitted.result == "posted"
            assert unit.store.capacity_count() == 21
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "applied"
            assert unit.store.capacity_count() == 20
            assert len(unit.store.capacity_asks()) == 20
            assert all(unit.store.ask(ask_id).status == status for ask_id, status in original)
            assert unit.store.ask_posted_since("2020") == posted + 1
            assert unit.store.db.execute("SELECT COUNT(*) FROM ask_answers").fetchone() == (20,)
            new_post = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Future disposable operation. " * 4, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert new_post.result == "posted", new_post.message
            assert unit.store.capacity_count() == 21

    run(body())


@pytest.mark.parametrize("changed", ["answers", "management", "approval"])
def test_real_daemon_refuses_stale_v2_and_approval_completion(unit, tmp_path, capsys, changed):
    seed(unit.store, kind="approval" if changed == "approval" else "question")
    migrate_quiesced(unit)
    receipt = receipt_file(unit, tmp_path)
    if changed == "answers":
        unit.store.add_answer("abcd", "answer", "Liam", "e" * 64, "no", "no")
    elif changed == "management":
        unit.store.db.execute("UPDATE asks SET presentation='{}',render_version=2,management='{}'")

    async def body():
        async with socket_server(unit):
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 1 and reply["result"] == "refused"
            assert (
                reply["message"]
                == {
                    "answers": "stale",
                    "management": "unsupported-management-fulfillment",
                    "approval": "unsupported",
                }[changed]
            )
            assert unit.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (0,)
            assert unit.store.capacity_count() == 1
            assert len(unit.store.answers("abcd")) == (2 if changed == "answers" else 1)

    run(body())


@pytest.mark.parametrize(
    "revision",
    [
        "07637745fe75dc614109a0b8ba72d3489f262a97",
        "a73e91d367bd626556371f33a46e25f003243c6e",
    ],
)
def test_enabled_completion_actual_legacy_reader_and_writer_rollback(unit, tmp_path, capsys, revision):
    seed(unit.store)
    migrate_quiesced(unit)
    receipt = receipt_file(unit, tmp_path)
    answers = unit.store.answers("abcd")

    async def body():
        async with socket_server(unit):
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "applied"

    run(body())
    root = tmp_path / "legacy"
    root.mkdir()
    raw = subprocess.run(["git", "archive", revision, "src"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(root, filter="data")
    code = """
import json, pathlib, sys
from heterodyne.admind.store import Store
import heterodyne.admind.store as module
assert pathlib.Path(module.__file__).is_relative_to(pathlib.Path(sys.argv[2]))
s=Store(pathlib.Path(sys.argv[1]))
assert s.ask('abcd').status == 'answered'
assert len(s.answers('abcd')) == 1
assert s.db.execute('SELECT COUNT(*) FROM ask_fulfillments').fetchone() == (1,)
s.db.execute("UPDATE asks SET body='actual historical writer mutation' WHERE ask_id='abcd'")
s.close()
print(json.dumps({'legacy_reader':True,'legacy_writer':True}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(unit.store.path), str(root)],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root / "src"), "PYTHONNOUSERSITE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"legacy_reader": True, "legacy_writer": True}
    assert unit.store.capacity_count() == 1
    assert unit.store.answers("abcd") == answers
    assert unit.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (1,)


@pytest.mark.parametrize("damage", ["missing-trigger", "malformed-meta", "missing-meta-table"])
@pytest.mark.parametrize("kind", ["question", "merge"])
def test_damaged_projection_preserves_conservative_capacity_and_posting(unit, tmp_path, capsys, damage, kind):
    seed(unit.store)
    migrate_quiesced(unit)
    receipt = receipt_file(unit, tmp_path)
    if damage == "missing-trigger":
        unit.store.db.execute("DROP TRIGGER fq2_answers_insert")
    elif damage == "missing-meta-table":
        unit.store.db.execute("DROP TABLE fulfillment_projection_meta")
    else:
        unit.store.db.execute("UPDATE fulfillment_projection_meta SET scheme='unknown'")
    assert unit.store.capacity_count() == 1

    async def body():
        async with socket_server(unit) as server:
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 1 and reply["result"] == "refused"
            assert reply["message"] == "fulfillment-projection-unavailable"
            assert unit.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (0,)
            request = asks.AskPost(
                kind,
                title="Independent future work",
                body="Explicit disposable future operation. " * 4,
                form="yes-no" if kind == "question" else None,
                pr_url="https://github.com/example/project/pull/1" if kind == "merge" else None,
                head_sha="a" * 40 if kind == "merge" else None,
            )
            posted = await ctl.request(server.path, request, 5, reply_type=asks.AskReply)
            assert posted.result == "posted", posted.message
            assert unit.store.capacity_count() == 2
            assert unit.store.ask("abcd").status == "answered"
            assert len(unit.store.answers("abcd")) == 1

    run(body())


def test_unknown_base_schema_cannot_use_projection_fallback(unit):
    seed(unit.store)
    migrate_quiesced(unit)
    unit.store.db.execute("UPDATE kv SET value='unknown' WHERE key='ask_fulfillment_schema'")

    async def body():
        async with socket_server(unit) as server:
            reply = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Disposable future operation. " * 4, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert reply.result == "refused"
            assert reply.message == "unsupported-schema"
            assert unit.store.db.execute("SELECT COUNT(*) FROM asks").fetchone() == (1,)

    run(body())


@pytest.mark.parametrize("damage", ["missing-trigger", "malformed-meta", "missing-meta-table"])
@pytest.mark.parametrize("kind", ["question", "merge"])
def test_verified_receipt_damage_restores_conservative_posting(unit, tmp_path, capsys, damage, kind):
    from heterodyne.admind.fulfillment_projection import verify_pass

    seed(unit.store)
    migrate_quiesced(unit)
    receipt = receipt_file(unit, tmp_path)

    async def body():
        async with socket_server(unit) as server:
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "applied"
            verify_pass(unit.store)
            assert unit.store.capacity_count() == 0
            if damage == "missing-trigger":
                unit.store.db.execute("DROP TRIGGER fq2_answers_insert")
            elif damage == "missing-meta-table":
                unit.store.db.execute("DROP TABLE fulfillment_projection_meta")
            else:
                unit.store.db.execute("UPDATE fulfillment_projection_meta SET scheme='unknown'")
            assert unit.store.capacity_count() == 1
            code, reply = await call_cli(unit, capsys, "apply", "--receipt", receipt)
            assert code == 0 and reply["result"] == "already-applied"
            assert unit.store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (1,)
            posted = await ctl.request(
                server.path,
                asks.AskPost(
                    kind,
                    title="Independent future work",
                    body="Explicit disposable future operation. " * 4,
                    form="yes-no" if kind == "question" else None,
                    pr_url="https://github.com/example/project/pull/1" if kind == "merge" else None,
                    head_sha="a" * 40 if kind == "merge" else None,
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert posted.result == "posted", posted.message
            assert unit.store.capacity_count() == 2
            assert unit.store.ask("abcd").status == "answered"
            assert len(unit.store.answers("abcd")) == 1

    run(body())


@pytest.mark.parametrize(
    "table", ["fulfillment_generations", "fulfillment_verifications", "fulfillment_dirty"]
)
def test_missing_trigger_target_table_fails_post_without_repair(unit, table):
    seed(unit.store)
    migrate_quiesced(unit)
    unit.store.db.execute(f"DROP TABLE {table}")  # noqa: S608 - closed literal parameter set
    before = unit.store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()

    async def body():
        async with socket_server(unit) as server:
            reply = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Disposable future operation. " * 4, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert reply.result == "failed"
            assert unit.store.db.execute("SELECT COUNT(*) FROM asks").fetchone() == (1,)
            assert (
                unit.store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
                == before
            )

    run(body())


def test_missing_meta_fragments_migration_refuses_without_schema_repair(unit):
    from heterodyne.admind.fulfillment_projection import migrate_connection

    seed(unit.store)
    migrate_quiesced(unit)
    unit.store.db.execute("DROP TABLE fulfillment_projection_meta")
    before = unit.store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
    for apply in (False, True):
        with pytest.raises(f.Invalid, match="^fulfillment-projection-unavailable$"):
            migrate_connection(unit.store.db, apply=apply)
        assert (
            unit.store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )
        assert not unit.store.db.in_transaction


def test_projection_sqlite_error_remains_failed_post(unit, monkeypatch):
    seed(unit.store)
    migrate_quiesced(unit)

    def broken_projection():
        raise sqlite3.OperationalError("disposable projection read failure")

    monkeypatch.setattr(unit.store.fulfillment, "require_projection", broken_projection)

    async def body():
        async with socket_server(unit) as server:
            reply = await ctl.request(
                server.path,
                asks.AskPost(
                    "question", title="Future work", body="Disposable future operation. " * 4, form="yes-no"
                ),
                5,
                reply_type=asks.AskReply,
            )
            assert reply.result == "failed"
            assert unit.store.db.execute("SELECT COUNT(*) FROM asks").fetchone() == (1,)

    run(body())
