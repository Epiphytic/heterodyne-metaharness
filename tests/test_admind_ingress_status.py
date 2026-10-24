"""Disposable socket/status tests; no upstream daemon, Store or live authority."""

import asyncio
import os
import stat
from pathlib import Path
from unittest.mock import Mock

import msgspec
import pytest
from fakes.settings import make_settings

from heterodyne.admind import cli, ctl, ingress
from heterodyne.admind.audit import Audit


def snapshot(observer: ingress.Observation) -> ingress.Reply:
    return observer.snapshot(
        normal_depth=2,
        custody_depth=1,
        locks={"work": True},
        generation=7,
        acked=True,
        reading=False,
        observing=False,
    )


def view(observer: ingress.Observation) -> ingress.Snapshot:
    value = snapshot(observer).snapshot
    assert value is not None
    return value


def test_snapshot_has_fixed_metadata_only_and_honest_unknowns() -> None:
    observer = ingress.Observation(clock=lambda: 10.0)
    result = snapshot(observer).snapshot
    assert result is not None
    assert result.generation == 7 and result.acked and not result.reading
    assert result.locks[0] == ingress.Lock("work", "unknown", None)
    assert all(task.state == "unknown" for task in result.tasks)
    assert result.retained_bytes is None and result.byte_accounting == "unavailable"
    assert "durable_disposition_completion" in result.unavailable
    assert not result.upstream_delivery_verified and not result.history_replay_verified
    assert len(msgspec.json.encode(snapshot(observer))) < ingress.MAX_REPLY
    assert view(ingress.Observation()).instance != result.instance


def test_counters_saturate_and_keep_monotonic_ages() -> None:
    clock = Mock(side_effect=[1.0, 2.0, 3.0, 4.0])
    observer = ingress.Observation(clock=clock)
    observer._counts["decoded_any"] = ingress.MAX_COUNTER - 1
    observer.count("decoded_any")
    observer.count("decoded_any")
    result = snapshot(observer).snapshot
    assert result is not None
    counter = result.counters[0]
    assert counter.value == ingress.MAX_COUNTER and counter.overflowed
    assert counter.last_age_seconds == 1.0
    with pytest.raises((KeyError, ValueError)):
        observer.count("secret-token")  # type: ignore[arg-type]


def test_owner_clear_and_actual_task_terminal_states() -> None:
    async def body() -> None:
        observer = ingress.Observation(clock=lambda: 1.0)
        observer.owner("work", "normal")
        assert view(observer).locks[0].owner == "normal"
        observer.owner("work", None)
        assert view(observer).locks[0].owner == "unknown"
        task = asyncio.create_task(asyncio.sleep(0))
        observer.task("reader", task)
        observer.phase("reader", "reading")
        assert view(observer).tasks[0].state == "running"
        await task
        assert view(observer).tasks[0].state == "done"
        pending = asyncio.create_task(asyncio.sleep(100))
        observer.task("custody", pending)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert view(observer).tasks[2].state == "cancelled"
        with pytest.raises(ValueError, match="phase"):
            observer.phase("reader", "handling")

    asyncio.run(body())


def test_real_socket_poll_is_private_readonly_and_does_not_grow_audit(tmp_path: Path) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        audit = Audit(path.parent / "audit.jsonl")
        observer = ingress.Observation()
        server = ingress.server(path, audit, lambda: snapshot(observer))
        await server.start()
        try:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            for _ in range(3):
                reply = await ingress.request(path)
                assert reply.result == "ok" and reply.snapshot is not None
                assert reply.snapshot.normal_depth == 2
            assert not audit.path.exists() or audit.path.read_bytes() == b""
        finally:
            await server.close()
        assert not path.exists() and not server.handlers

    asyncio.run(body())


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b'{"schema":"admind.ingress.request.v1","op":"reconnect"}',
        b'{"schema":"admind.ingress.request.v1","op":"status","secret":"do-not-log"}',
        b"x" * 4097,
    ],
)
def test_actual_malformed_oversize_requests_never_call_snapshot_or_audit(
    tmp_path: Path, payload: bytes
) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        called = Mock(side_effect=AssertionError("secret snapshot must not run"))
        audit = Audit(path.parent / "audit.jsonl")
        server = ingress.server(path, audit, called)
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(payload + b"\n")
            await writer.drain()
            result = msgspec.json.decode(await reader.readline(), type=ingress.Reply)
            assert result == ingress.Reply("refused", "malformed")
            await ctl.hang_up(writer)
            called.assert_not_called()
            assert not audit.path.exists() or audit.path.read_bytes() == b""
        finally:
            await server.close()

    asyncio.run(body())


@pytest.mark.parametrize("peer_uid", [None, -1])
def test_server_refuses_missing_or_foreign_uid_without_snapshot(tmp_path: Path, peer_uid: int | None) -> None:
    called = Mock(side_effect=AssertionError("not admitted"))
    server = ingress.server(tmp_path / "ingress.sock", Audit(tmp_path / "audit.jsonl"), called)

    async def body() -> ingress.Reply:
        return await server.handler(
            ingress.Request("admind.ingress.request.v1", "status"), ctl.Peer(123, peer_uid)
        )

    reply = asyncio.run(body())
    assert reply == ingress.Reply("refused", "peer")
    called.assert_not_called()


def test_actual_server_peer_gate_prevents_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        called = Mock(side_effect=AssertionError("not admitted"))
        server = ingress.server(path, Audit(path.parent / "audit.jsonl"), called)
        await server.start()
        try:
            monkeypatch.setattr(
                ctl.platform, "peer_credentials_checked", lambda _sock: (1, os.getuid() + 1, 1)
            )
            reply = await ctl.request(
                path,
                ingress.Request("admind.ingress.request.v1", "status"),
                reply_type=ingress.Reply,
                max_reply=ingress.MAX_REPLY,
            )
            assert reply == ingress.Reply("refused", "peer")
            called.assert_not_called()
        finally:
            await server.close()

    asyncio.run(body())


def test_client_rejects_actual_socket_when_peer_check_is_foreign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        server = ingress.server(
            path, Audit(path.parent / "audit.jsonl"), lambda: snapshot(ingress.Observation())
        )
        await server.start()
        try:
            monkeypatch.setattr(
                ctl.platform, "peer_credentials_checked", lambda _sock: (1, os.getuid() + 1, 1)
            )
            with pytest.raises(ctl.CtlUnavailable, match="server-peer"):
                await ingress.request(path)
        finally:
            await server.close()

    asyncio.run(body())


def test_status_failure_payload_never_leaks_or_creates_audit(tmp_path: Path) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        audit = Audit(path.parent / "audit.jsonl")
        server = ingress.server(path, audit, Mock(side_effect=RuntimeError("secret-identity-123")))
        await server.start()
        try:
            reply = await ingress.request(path)
            assert reply == ingress.Reply("unavailable", "unavailable")
            assert "secret" not in msgspec.json.encode(reply).decode()
            assert not audit.path.exists() or audit.path.read_bytes() == b""
        finally:
            await server.close()

    asyncio.run(body())


def test_status_client_whole_operation_timeout_includes_send(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(ctl, "request", never)
    monkeypatch.setattr(ingress, "CLIENT_SECONDS", 0.01)
    with pytest.raises(ctl.CtlUnavailable, match="ingress-unavailable"):
        asyncio.run(ingress.request(Path("unused")))


def test_close_cancels_silent_clients_without_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ctl, "CLOSE_SECONDS", 0.01)

    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        audit = Audit(path.parent / "audit.jsonl")
        server = ingress.server(path, audit, lambda: snapshot(ingress.Observation()))
        await server.start()
        reader, writer = await asyncio.open_unix_connection(str(path))
        await asyncio.sleep(0)
        await server.close()
        assert await reader.read() == b""
        await ctl.hang_up(writer)
        assert not server.handlers and not path.exists()
        assert not audit.path.exists() or audit.path.read_bytes() == b""

    asyncio.run(body())


def test_socket_symlink_is_not_followed(tmp_path: Path) -> None:
    path = tmp_path / "s" / ingress.SOCKET
    path.parent.mkdir()
    original = tmp_path / "precious"
    original.write_text("unchanged")
    path.symlink_to(original)
    server = ingress.server(path, Audit(path.parent / "audit.jsonl"), lambda: snapshot(ingress.Observation()))
    with pytest.raises(FileExistsError):
        asyncio.run(server.start())
    assert original.read_text() == "unchanged" and path.is_symlink()


def test_cli_status_settings_only_and_fixed_unavailable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_settings_only", lambda: make_settings(tmp_path))
    monkeypatch.setattr(cli, "_load", Mock(side_effect=AssertionError("must not construct Store")))
    assert cli.main(["ingress", "status", "--json"]) == cli.EX_UNAVAILABLE
    assert capsys.readouterr().err == "admind: ingress status unavailable\n"
    for args in (["ingress", "reconnect"], ["ingress", "status"], ["ingress", "status", "--json", "secret"]):
        with pytest.raises(SystemExit) as error:
            cli.build_parser().parse_args(args)
        assert error.value.code == 2


@pytest.mark.parametrize("instance", ["secret-identifier", "x" * (ingress.MAX_REPLY + 1)])
def test_actual_reply_schema_and_capacity_do_not_expose_supplied_identifiers(
    tmp_path: Path, instance: str
) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        invalid = msgspec.structs.replace(view(ingress.Observation()), instance=instance)
        server = ingress.server(
            path, Audit(path.parent / "audit.jsonl"), lambda: ingress.Reply("ok", "snapshot", invalid)
        )
        await server.start()
        try:
            if len(instance) > ingress.MAX_REPLY:
                assert await ingress.request(path) == ingress.Reply("unavailable", "unavailable")
            else:
                with pytest.raises(ctl.CtlInvalidReply, match="ingress-schema"):
                    await ingress.request(path)
        finally:
            await server.close()

    asyncio.run(body())


@pytest.mark.parametrize(
    "result,reason",
    [
        ("ok", "peer"),
        ("refused", "snapshot"),
        ("refused", "unavailable"),
        ("unavailable", "peer"),
        ("unavailable", "malformed"),
    ],
)
def test_invalid_result_reason_pairs_refused(tmp_path: Path, result: str, reason: str) -> None:
    async def body() -> None:
        path = tmp_path / "s" / ingress.SOCKET
        raw = {
            "schema": "admind.ingress.reply.v1",
            "result": result,
            "reason": reason,
            "snapshot": msgspec.to_builtins(view(ingress.Observation())) if result == "ok" else None,
        }
        reply = msgspec.convert(raw, type=ingress.Reply)
        server = ingress.server(path, Audit(path.parent / "audit.jsonl"), lambda: reply)
        await server.start()
        try:
            with pytest.raises(ctl.CtlInvalidReply, match="ingress-schema"):
                await ingress.request(path)
        finally:
            await server.close()

    asyncio.run(body())


def test_total_deadline_does_not_wait_for_graceful_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def body() -> None:
        writers = []

        async def silent(reader, writer):
            writers.append(writer)
            await reader.read()
            writer.close()
            await writer.wait_closed()

        opened = []
        open_connection = asyncio.open_unix_connection

        async def open_with_blocked_close(*args, **kwargs):
            reader, writer = await open_connection(*args, **kwargs)
            close_wait = Mock()

            async def blocked_close():
                close_wait()
                await asyncio.Event().wait()

            writer.wait_closed = blocked_close
            abort = Mock(wraps=writer.transport.abort)
            writer.transport.abort = abort
            opened.append((close_wait, abort))
            return reader, writer

        path = tmp_path / "silent.sock"
        server = await asyncio.start_unix_server(silent, path=path)
        monkeypatch.setattr(asyncio, "open_unix_connection", open_with_blocked_close)
        monkeypatch.setattr(ingress, "CLIENT_SECONDS", 0.03)
        started = asyncio.get_running_loop().time()
        try:
            with pytest.raises(ctl.CtlUnavailable, match="ingress-unavailable"):
                await ingress.request(path)
            assert asyncio.get_running_loop().time() - started < 0.15
            assert len(opened) == 1
            opened[0][0].assert_not_called()
            opened[0][1].assert_called_once_with()
            await asyncio.sleep(0)
        finally:
            server.close()
            await server.wait_closed()
            for writer in writers:
                writer.close()
                writer.transport.abort()
                await writer.wait_closed()
            await asyncio.sleep(0)

    asyncio.run(body())


def test_enum_tables_and_recovery_phase_are_closed() -> None:
    from typing import get_args

    assert ingress.COUNTERS == get_args(ingress.CounterName)
    assert ingress.TASKS == get_args(ingress.TaskName)
    assert ingress.LOCKS == get_args(ingress.LockName)
    assert ingress.OWNERS == frozenset(get_args(ingress.Owner))
    observer = ingress.Observation()
    observer.phase("recovery", "waiting_custody_lock")
    observer.owner("custody", "custody_control")
    observer.owner("work", "autobump")
    for phase in ("connecting", "closing", "waiting_observation"):
        with pytest.raises(ValueError, match="unknown ingress phase"):
            observer.phase("reader", phase)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown ingress owner"):
        observer.owner("work", "reconnect")  # type: ignore[arg-type]
