"""Independent R1 regressions: private Unix transport/Store, never installed services.

Faults replace only explicit transport/audit boundaries. Production reconnect,
reader, intake, supervisor, locks and release code remain unmodified.
"""

import asyncio
import json
import uuid
from pathlib import Path

from test_admind_daemon import Harness
from test_admind_ingress_observation import counters, message
from test_admind_ingress_reconnect import stop, until

from heterodyne.admind.daemon import supervised


def test_terminal_replacement_reader_releases_admission_without_deadline_wait(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)  # Its private control socket intentionally does not exist.
        d = h.daemon
        d.reconnect.running = True
        d.reconnect.recovery_seconds = 5.0
        admitted = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        assert admitted.result == "accepted"
        operation = d.reconnect.operation
        assert operation is not None
        try:
            await until(lambda: d.reconnect.reader is not None and d.reconnect.reader.done())
            # Wait for real terminal reader evidence, then allow a generous local
            # scheduling window, strictly below the configured observation deadline.
            done, _ = await asyncio.wait({operation}, timeout=1.0)
            assert operation in done, "terminal reader must not retain all three locks until deadline"
            assert not d.reconnect.intent
            assert not any(lock.locked() for lock in (d.work_lock, d.transition_lock, d.custody_lock))
            assert admitted.operation is not None
            assert d.reconnect.records[admitted.operation.request_id].reason == "failed-observation"
        finally:
            await stop(operation)

    asyncio.run(scenario())


def test_unexpected_inbound_exit_can_be_restarted_by_actual_supervisor(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        start = d.reconnect._start_reader
        calls = 0
        fault_seen = asyncio.Event()

        def fail_first_start(deadline=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                fault_seen.set()
                raise RuntimeError("synthetic source start boundary fault")
            return start(deadline)

        d.reconnect._start_reader = fail_first_start
        supervisor = asyncio.create_task(
            supervised("inbound", d.inbound_loop, h.audit, base=0.001, cap=0.001)
        )
        try:
            await fault_seen.wait()
            # The first run has unwound synchronously by the time this waiter runs.
            assert not d.shutting_down, "unexpected supervisor exit is not daemon shutdown"
            await until(lambda: calls >= 2 and d.reading)
            assert not supervisor.done()
        finally:
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())


def test_decoded_intake_is_retained_while_reconnect_holds_worker_locks(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        d.reconnect.running = True
        admitted = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
        assert admitted.result == "accepted"
        operation = d.reconnect.operation
        assert operation is not None
        try:
            event = message(h)
            d.on_event(event)  # Actual decoded intake entry, before cutover can run.
            assert d.work.qsize() == 1, "worker gating must not discard decoded intake"
            assert d.work.get_nowait() is event
            observed = counters(h)
            assert observed["decoded_any"] == 1
            assert observed["enqueued_normal"] == 1
            assert observed["dropped_intake"] == 0
            d.work.task_done()
        finally:
            await stop(operation)

    asyncio.run(scenario())


def test_accepted_audit_failure_does_not_fabricate_transport_uncertainty(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        try:
            await until(lambda: d.reading)
            write = h.audit.write
            failed = False

            def fail_accepted(kind, **fields):
                nonlocal failed
                if kind == "ingress" and fields.get("action") == "reconnect-accepted" and not failed:
                    failed = True
                    raise OSError("synthetic private audit fault")
                return write(kind, **fields)

            h.audit.write = fail_accepted
            admitted = await d.reconnect.admit(d.ingress.instance, d.sub_gen, str(uuid.uuid4()))
            assert admitted.result == "accepted"
            await until(lambda: not d.reconnect.intent)
            assert failed
            assert not d.reconnect.teardown_unknown, "audit failure is not failed transport cessation"
            assert admitted.operation is not None
            assert d.reconnect.records[admitted.operation.request_id].reason != "failed-teardown"
            await until(lambda: d.reading)
        finally:
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())


def test_natural_teardown_unknown_is_retained_and_explicitly_audited(tmp_path: Path) -> None:
    async def scenario() -> None:
        h = Harness(tmp_path)
        d = h.daemon
        await h.fake.start()
        supervisor = asyncio.create_task(d.inbound_loop())
        close = None
        transport = None
        try:
            await until(lambda: d.reading)
            reader, transport = d.reconnect.reader, d.reconnect.transport
            assert reader is not None and transport is not None
            close = transport.close

            async def unknown_close(deadline=None):
                return False  # Explicit transport boundary: no positive cessation.

            transport.close = unknown_close
            reader.cancel()
            await until(lambda: d.reconnect.teardown_unknown)
            assert d.reconnect.paused and not d.reading
            assert d.reconnect.reader is reader, "unknown original must remain retained"
            audit_path = h.settings.state_dir / "audit.jsonl"
            records = (
                [json.loads(line) for line in audit_path.read_text().splitlines()]
                if audit_path.exists()
                else []
            )
            assert any(
                row.get("kind") == "subscribe" and row.get("action") == "teardown-unknown" for row in records
            ), "natural teardown uncertainty must have an explicit fixed audit signal"
        finally:
            if transport is not None and close is not None:
                transport.close = close
            await stop(supervisor)
            await h.fake.stop()

    asyncio.run(scenario())
