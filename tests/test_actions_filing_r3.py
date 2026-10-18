"""R3 pre-effect reducer and real disposable SQLite lock regressions."""

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import test_actions_filing as original
from test_actions_filing_r2 import allocated
from test_actions_p1 import DIGEST, initial

from heterodyne.actions import requests as r
from heterodyne.actions.contracts import Clock
from heterodyne.actions.filing import Cleanup, FilingJournal, Held, key

owner = original.owner


@pytest.mark.parametrize("phase", ["cancelled", "denied", "reconciled-abandoned"])
def test_invalid_withdrawal_reducer_refuses_before_preparation(owner, phase):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    now[0] = Clock("fixture-boot", 1_000_000_000, 0)
    with pytest.raises(OSError):
        journal.reconcile(command.command_id)
    value = journal.request(key(value))
    assert value.allocation_lookups == 1
    recovery = r.AbandonmentRecovery(
        value.key,
        value.origin,
        value.version,
        value.shell_ids,
        DIGEST,
        "fixture-event",
        authenticated_current=True,
        exact_repair_scope=True,
        native_readback=True,
    )
    cleanup = Cleanup(phase, abandonment=recovery if phase == "reconciled-abandoned" else None)
    with pytest.raises((r.Refused, Held)):
        journal.prepare_cleanup(key(value), DIGEST, cleanup)
    assert len(ports.calls) == 1
    assert journal.db.execute("SELECT count(*) FROM commands WHERE operation='withdraw'").fetchone()[0] == 0
    assert journal.request(key(value)) == value


def test_issue_rechecks_reducer_before_invoking_port(owner, monkeypatch):
    journal, ports, _ = owner
    value = allocated(owner)
    command = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    calls = list(ports.calls)

    def refuse(*args, **kwargs):
        raise r.Refused("fixture-reducer-refusal")

    monkeypatch.setattr(r, "settle_request", refuse)
    with pytest.raises(r.Refused, match="fixture-reducer-refusal"):
        journal.execute(command.command_id)
    assert ports.calls == calls
    assert journal.command(command.command_id)[1] == "prepared"
    assert journal.request(key(value)) == value


def test_competing_thread_lease_does_not_drop_real_sqlite_writer_lock(owner):
    journal, ports, now = owner
    path = journal.path
    second_db = sqlite3.connect(path, isolation_level=None)
    second = FilingJournal(second_db, lambda: now[0], lambda: "f" * 32, ports)
    errors = []

    def compete():
        try:
            with second.ownership():
                errors.append("unexpected-acquisition")
        except Held:
            errors.append("busy")

    probe = """import sqlite3,sys
try:
 db=sqlite3.connect(sys.argv[1],timeout=0,isolation_level=None)
 db.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError:
 sys.exit(17)
else:
 db.execute('ROLLBACK')
 db.close()
"""

    def locked():
        return subprocess.run([sys.executable, "-c", probe, str(path)], check=False).returncode == 17

    try:
        with journal.transaction():
            assert locked()
            worker = threading.Thread(target=compete)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert errors == ["busy"]
            assert locked(), "closing the competing lease descriptor released SQLite's writer lock"
    finally:
        second_db.close()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public"])
def test_owner_lock_rejects_unsafe_sidecar(owner, tmp_path, unsafe):
    journal, _, _ = owner
    lock = Path(str(journal.path) + ".owner.lock")
    target = tmp_path / "other"
    target.write_bytes(b"")
    target.chmod(0o600)
    if unsafe == "symlink":
        lock.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(target, lock)
    else:
        lock.write_bytes(b"")
        lock.chmod(0o644)
    with pytest.raises(Held, match="lock"):
        with journal.ownership():
            pytest.fail("unsafe lock accepted")


def test_lock_inode_is_stable_and_distinct_from_database(owner):
    journal, _, _ = owner
    lock = Path(str(journal.path) + ".owner.lock")
    with journal.ownership():
        identity = lock.stat()
        assert (identity.st_dev, identity.st_ino) != (journal.path.stat().st_dev, journal.path.stat().st_ino)
        assert identity.st_mode & 0o777 == 0o600
    with journal.ownership():
        assert lock.stat().st_ino == identity.st_ino
    assert lock.exists()


@pytest.mark.parametrize("name", ["run#1", "run?mode=ro", "run%23one"])
def test_open_reserved_uri_path_reopens_exact_journal(tmp_path, owner, name):
    _, ports, now = owner
    directory = tmp_path / name
    directory.mkdir()
    path = directory / "journal.db"
    journal = FilingJournal.create(path, clock=lambda: now[0], nonce=lambda: "e" * 32, ports=ports)
    value = initial()
    journal.reserve(value)
    journal.db.close()
    reopened = FilingJournal.open(path, clock=lambda: now[0], nonce=lambda: "f" * 32, ports=ports)
    try:
        assert reopened.path == path
        assert reopened.request(key(value)) == value
    finally:
        reopened.db.close()


def test_live_port_callback_cannot_mark_own_call_unknown(owner):
    journal, ports, _ = owner
    value = allocated(owner)
    command = journal.prepare(key(value), "blocker", DIGEST)
    invoke = ports.invoke

    def live(current):
        with pytest.raises(Held, match="live"):
            journal.mark_unknown(current)
        assert journal.command(current.command_id)[1] == "issuing"
        return invoke(current)

    ports.invoke = live
    journal.execute(command.command_id)
    assert journal.command(command.command_id)[1] == "observed"


def test_live_provider_callback_cannot_mark_own_call_unknown(owner):
    import asyncio

    from gatekeeper_fixtures import privacy
    from test_actions_p2 import SPEC, Provider, inputs

    journal, _, _ = owner
    value = original.landed(owner)
    _, request = inputs()
    inner = Provider(request)

    class LiveProvider:
        async def judge(self, invocation):
            command, state = journal.command(invocation.nonce)
            assert state == "issuing"
            with pytest.raises(Held, match="live"):
                journal.mark_unknown(command)
            assert journal.command(invocation.nonce)[1] == "issuing"
            return await inner.judge(invocation)

    command_id, receipt = asyncio.run(
        journal.assess(key(value), request, privacy(), SPEC, LiveProvider(), policy_revision=DIGEST)
    )
    assert receipt.reply is not None
    assert journal.command(command_id)[1] == "observed"
    assert len(inner.calls) == 1


def test_pinned_cleanup_requires_authenticated_typed_recovery(owner):
    journal, ports, _ = owner
    value = original.landed(owner)
    calls = list(ports.calls)
    recovery = r.CancellationRecovery(
        value.key,
        value.origin,
        value.version,
        value.shell_ids,
        DIGEST,
        "fixture-event",
        authenticated_current=False,
        exact_repair_scope=True,
        native_withdrawal_readback=True,
        cleanup_proven=True,
    )
    with pytest.raises(r.Refused):
        journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled", cancellation=recovery))
    assert ports.calls == calls
    assert journal.request(key(value)) == value
    assert journal.db.execute("SELECT count(*) FROM commands WHERE operation='withdraw'").fetchone()[0] == 0
