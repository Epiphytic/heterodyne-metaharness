"""R1 causal regression seams; isolated Stores and Unix fixtures only."""

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fakes.settings import OPERATOR_HEX, make_settings
from test_admind_daemon import Harness
from test_admind_ingress_observation import counters, message
from test_admind_ingress_reconnect import stop, until

from heterodyne.admind import cli, ingress
from heterodyne.admind.ingress_reconnect import Operation
from heterodyne.marmot.control import ControlError, Subscription


def test_failed_reader_cycle_has_bounded_safe_lifecycle_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        backoff = asyncio.Event()
        d._sleep = lambda _: backoff.wait()

        async def fail(*args):
            raise ControlError("private detail must not escape", "unverified")

        d.read_subscription = fail
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.ingress_snapshot().snapshot.tasks[0].phase == "backoff")
            rows = [json.loads(line) for line in h.audit.path.read_text().splitlines()]
            lifecycle = [row for row in rows if row.get("kind") == "subscribe"]
            assert len(lifecycle) == 1
            assert lifecycle[0]["action"] == "reconnect" and lifecycle[0]["code"] == "unverified"
            assert "private detail" not in h.audit.path.read_text()
        finally:
            await stop(supervisor)
    asyncio.run(scenario())


def test_reconnect_refusals_and_failure_audited_but_status_quiet(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        path = tmp_path / "ingress.sock"
        server = ingress.server(path, h.audit, d.ingress_snapshot, d.ingress_reconnect)
        await server.start()
        try:
            for _ in range(3):
                assert (await ingress.request(path)).result == "ok"
            assert not h.audit.path.exists() or not h.audit.path.read_bytes()
            request_id = str(uuid.uuid4())
            req = ingress.Request("admind.ingress.request.v1", "reconnect", d.ingress.instance,
                                  d.sub_gen, request_id)
            async with d.work_lock:
                assert (await ingress.request(path, req)).operation.result == "busy"
            stale = ingress.Request(req.schema, req.op, "0" * 32, req.generation, str(uuid.uuid4()))
            assert (await ingress.request(path, stale)).operation.reason == "stale"
            d.reconnect.records[request_id] = Operation(d.ingress.instance, d.sub_gen + 1,
                                                      request_id, "failed", "shutdown")
            assert (await ingress.request(path, req)).operation.reason == "request-conflict"
            async def fail(*args):
                raise RuntimeError("not for audit")
            d.ingress_reconnect = fail
            server.handler = ingress.server(path, h.audit, d.ingress_snapshot, fail).handler
            assert (await ingress.request(path, req)).result == "unavailable"
            rows = [json.loads(line) for line in h.audit.path.read_text().splitlines()]
            results = [row.get("reason") for row in rows if row.get("action") == "reconnect-result"]
            assert results == ["busy", "stale", "request-conflict"]
            assert any(row.get("action") == "reconnect-handler-failed" for row in rows)
            size = h.audit.path.stat().st_size
            await ingress.request(path)
            assert h.audit.path.stat().st_size == size
            assert "not for audit" not in h.audit.path.read_text()
        finally:
            await server.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("custody", [False, True])
def test_stale_reader_retains_decoded_event_without_current_observation(
    tmp_path: Path, custody: bool
) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.authorise({OPERATOR_HEX})
        d.refresh_authority = Mock(return_value=True)
        event = message(h, text="!custody settle " + "a" * 24 if custody else "reply")
        generation = d.sub_gen
        d.reconnect.paused = True
        d.reconnect.invalidate()
        async def subscribe(*args, **kwargs):
            yield event
        d.client.subscribe = subscribe
        await d.read_subscription(generation, None, Subscription())
        queue = d.custody_work if custody else d.work
        assert queue.get_nowait() is event
        queue.task_done()
        observed = counters(h)
        assert observed["decoded_any"] == 1
        assert observed["enqueued_custody" if custody else "enqueued_normal"] == 1
        assert observed["claimed"] == 0 and not d.reading and not d.acked
        assert d.reconnect.paused and not d.reconnect.ready.is_set()
    asyncio.run(scenario())


def test_unknown_teardown_notice_once_and_exact_original_retained(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.post = Mock()
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        close = None
        try:
            await until(lambda: d.reading)
            reader, transport = d.reconnect.reader, d.reconnect.transport
            close = transport.close
            transport.close = AsyncMock(return_value=False)
            reader.cancel()
            await until(lambda: d.reconnect.teardown_unknown)
            assert d.reconnect.reader is reader and d.reconnect.transport is transport
            assert d.reconnect.paused and not d.reading
            assert d.post.call_count == 1
            text = d.post.call_args.args[1]
            assert "fresh request UUID" in text and "does not clear execution holds" in text
            # Retrying failed original closure cannot spam notices or create a replacement.
            result = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert result.result == "accepted"
            await until(lambda: not d.reconnect.intent)
            assert d.reconnect.reader is reader and d.reconnect.transport is transport
            assert d.post.call_count == 1
        finally:
            if close is not None:
                transport.close = close
            await stop(supervisor)
            await h.fake.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("selectors", [
    ["--instance", "X", "--generation", "5", "--request-id", str(uuid.uuid4())],
    ["--instance", "a" * 32, "--generation", "-1", "--request-id", str(uuid.uuid4())],
    ["--instance", "a" * 32, "--generation", "5", "--request-id", "ABCDEFAB-1234-1234-1234-ABCDEFABCDEF"],
])
def test_invalid_selectors_are_usage_error_without_transport(selectors, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_settings_only", lambda: make_settings(tmp_path))
    request = Mock(side_effect=AssertionError("must not contact endpoint"))
    monkeypatch.setattr(ingress, "request", request)
    assert cli.main(["ingress", "reconnect", *selectors]) == 2
    assert capsys.readouterr().err == "admind: invalid ingress reconnect selectors\n"
    request.assert_not_called()


def test_generation_advances_once_per_actual_natural_replacement(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d._sleep = AsyncMock()
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            reader, generation = d.reconnect.reader, d.sub_gen
            reader.cancel()
            await until(lambda: d.reconnect.reader is not reader and d.reading)
            assert d.sub_gen == generation + 1
            # Completion no longer allocates an Event with no waiting producer.
            assert not hasattr(d.ingress_capacity, "changed")
        finally:
            await stop(supervisor)
            await h.fake.stop()
    asyncio.run(scenario())
