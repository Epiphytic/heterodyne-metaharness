"""R2 journal ownership and finite cleanup regressions; no real ports."""

from pathlib import Path

import msgspec
import pytest
import test_actions_filing as original
from test_actions_p1 import DIGEST, initial

from heterodyne.actions import requests as r
from heterodyne.actions.filing import BeforeIssue, Cleanup, FilingJournal, Held, encoded, key

owner = original.owner


def allocated(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    journal.execute(journal.prepare(key(value), "allocate", DIGEST).command_id)
    return journal.request(key(value))


def test_concurrent_open_cannot_reclassify_live_effect(owner):
    journal, ports, now = owner
    value = allocated(owner)
    command = journal.prepare(key(value), "blocker", DIGEST)
    invoke = ports.invoke

    def live(current):
        path = Path(journal.db.execute("PRAGMA database_list").fetchone()[2])
        with pytest.raises(Held, match="owner"):
            second = FilingJournal.open(path, clock=lambda: now[0], nonce=lambda: "f" * 32, ports=ports)
            second.db.close()
        assert journal.command(current.command_id)[1] == "issuing"
        with pytest.raises(Held, match="live"):
            journal.reconcile(current.command_id)
        return invoke(current)

    ports.invoke = live
    journal.execute(command.command_id)
    assert journal.command(command.command_id)[1] == "observed"


def test_withdraw_fences_older_reconciliation(owner):
    journal, ports, _ = owner
    value = allocated(owner)
    command = journal.prepare(key(value), "blocker", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    withdrawal = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    ports.fail = False
    with pytest.raises(Held, match="withdraw"):
        journal.reconcile(command.command_id)
    assert journal.request(key(value)) == withdrawal.request


@pytest.mark.parametrize("refusal", ["before", "abandon"])
def test_cleanup_retry_survives_full_ordinary_budget(owner, refusal):
    journal, ports, _ = owner
    value = allocated(owner)
    with journal.transaction():
        while journal.db.execute("SELECT lifecycle FROM requests").fetchone()[0] < 124:
            journal.append_event(key(value), "fixture", b"{}")
    first = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    if refusal == "before":

        def refuse(command):
            raise BeforeIssue("fixture")

        ports.invoke = refuse
        journal.execute(first.command_id)
    else:
        journal.abandon_prepared(first.command_id)
    retry = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    issued = journal.issue(retry.command_id, ports.current(retry))
    terminal = r.settle_request(
        value,
        "cancelled",
        key=value.key,
        origin=value.origin,
        cleaned_shells=value.shell_ids,
        blockers_clear=True,
        native_terminal=True,
    )
    journal.observe(
        retry.command_id,
        msgspec.structs.replace(
            ports.observation(issued),
            blockers_clear=True,
            native_terminal=True,
            reducer_request=encoded(terminal),
        ),
    )
    assert journal.request(key(value)) == terminal
    assert journal.db.execute("SELECT lifecycle FROM requests").fetchone()[0] <= 128


def test_orphan_provider_reply_allows_cleanup_without_overwrite(owner):
    journal, ports, _ = owner
    value = original.landed(owner)
    command = journal.prepare(key(value), "provider", DIGEST)
    original_reply = b'"retained original result"'
    journal.db.execute(
        "UPDATE commands SET state='observed',result=? WHERE id=?", (original_reply, command.command_id)
    )
    journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    assert (
        journal.db.execute("SELECT result FROM commands WHERE id=?", (command.command_id,)).fetchone()[0]
        == original_reply
    )


def test_before_issue_bookkeeping_failure_becomes_unknown(owner, monkeypatch):
    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)

    def refuse(current):
        raise BeforeIssue("fixture")

    def failed(current):
        raise r.Refused("fixture-bookkeeping")

    ports.invoke = refuse
    monkeypatch.setattr(r, "cancel_unissued_allocation", failed)
    with pytest.raises(r.Refused):
        journal.execute(command.command_id)
    assert journal.command(command.command_id)[1] == "uncertain"


def test_terminal_detail_cannot_spend_released_reservation(owner):
    journal, ports, _ = owner
    value = allocated(owner)
    terminal = r.settle_request(
        value,
        "cancelled",
        key=value.key,
        origin=value.origin,
        cleaned_shells=value.shell_ids,
        blockers_clear=True,
        native_terminal=True,
    )
    with journal.transaction():
        journal._save(value, terminal)
    with pytest.raises(Held, match="terminal"):
        journal.record_detail(key(value), "new-detail", b"late")


def test_provider_generic_readback_and_observation_refused(owner):
    journal, ports, _ = owner
    value = original.landed(owner)
    command = journal.prepare(key(value), "provider", DIGEST)
    journal.db.execute("UPDATE commands SET state='uncertain' WHERE id=?", (command.command_id,))
    with pytest.raises(Held, match="provider"):
        journal.reconcile(command.command_id)
    with pytest.raises(Held, match="provider"):
        journal.observe(command.command_id, ports.observation(command))
    assert not ports.reads


def test_actual_orphan_reply_has_authenticated_terminal_exit(owner):
    import asyncio

    from gatekeeper_fixtures import privacy
    from test_actions_p2 import SPEC, Provider, inputs

    journal, ports, _ = owner
    value = original.landed(owner)
    _, request = inputs()
    provider = Provider(request)
    command_id, _ = asyncio.run(
        journal.assess(key(value), request, privacy(), SPEC, provider, policy_revision=DIGEST)
    )
    original_result = journal.db.execute("SELECT result FROM commands WHERE id=?", (command_id,)).fetchone()[
        0
    ]
    journal.db.execute("DELETE FROM assessments WHERE id=?", (command_id,))
    recovery = r.CancellationRecovery(
        value.key,
        value.origin,
        value.version,
        value.shell_ids,
        DIGEST,
        "fixture-event",
        authenticated_current=True,
        exact_repair_scope=True,
        native_withdrawal_readback=True,
        cleanup_proven=True,
    )
    cleanup = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled", cancellation=recovery))
    issued = journal.issue(cleanup.command_id, ports.current(cleanup))
    terminal = r.settle_request(
        value,
        "cancelled",
        key=value.key,
        origin=value.origin,
        cleaned_shells=value.shell_ids,
        blockers_clear=True,
        native_terminal=True,
        recovery=recovery,
    )
    journal.observe(
        cleanup.command_id,
        msgspec.structs.replace(
            ports.observation(issued),
            blockers_clear=True,
            native_terminal=True,
            reducer_request=encoded(terminal),
        ),
    )
    assert journal.request(key(value)) == terminal
    assert (
        journal.db.execute("SELECT result FROM commands WHERE id=?", (command_id,)).fetchone()[0]
        == original_result
    )
    assert len(provider.calls) == 1


def test_provider_live_reply_survives_concurrent_open_and_readbacks(owner):
    import asyncio

    from gatekeeper_fixtures import privacy
    from test_actions_p2 import SPEC, Provider, inputs

    journal, ports, now = owner
    value = original.landed(owner)
    _, request = inputs()
    inner = Provider(request)

    class LiveProvider:
        async def judge(self, invocation):
            command_id = invocation.nonce
            path = Path(journal.db.execute("PRAGMA database_list").fetchone()[2])
            with pytest.raises(Held, match="owner"):
                FilingJournal.open(path, clock=lambda: now[0], nonce=lambda: "e" * 32, ports=ports)
            command, state = journal.command(command_id)
            assert state == "issuing"
            with pytest.raises(Held, match="provider"):
                journal.reconcile(command_id)
            with pytest.raises(Held, match="provider"):
                journal.observe(command_id, ports.observation(command))
            return await inner.judge(invocation)

    command_id, receipt = asyncio.run(
        journal.assess(key(value), request, privacy(), SPEC, LiveProvider(), policy_revision=DIGEST)
    )
    assert receipt.reply is not None
    assert journal.command(command_id)[1] == "observed"
    assert len(inner.calls) == 1


def test_lease_is_released_after_before_issue_bookkeeping_failure(owner, monkeypatch):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)

    def refuse(current):
        raise BeforeIssue("fixture")

    def failed(current):
        raise r.Refused("fixture-bookkeeping")

    ports.invoke = refuse
    monkeypatch.setattr(r, "cancel_unissued_allocation", failed)
    with pytest.raises(r.Refused):
        journal.execute(command.command_id)
    path = Path(journal.db.execute("PRAGMA database_list").fetchone()[2])
    other = FilingJournal.open(path, clock=lambda: now[0], nonce=lambda: "e" * 32, ports=ports)
    other.db.close()


def test_withdrawal_fences_direct_old_observation(owner):
    journal, ports, _ = owner
    value = allocated(owner)
    command = journal.prepare(key(value), "blocker", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    with pytest.raises(Held, match="withdraw"):
        journal.observe(command.command_id, ports.observation(command))


def test_three_proved_unissued_withdrawals_retain_finite_history(owner):
    journal, ports, _ = owner
    value = allocated(owner)
    with journal.transaction():
        while journal.db.execute("SELECT lifecycle FROM requests").fetchone()[0] < 124:
            journal.append_event(key(value), "fixture", b"{}")

    def refuse(command):
        raise BeforeIssue("fixture-retained")

    ports.invoke = refuse
    identities = []
    for _ in range(3):
        command = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
        identities.append(command.command_id)
        journal.execute(command.command_id)
    with pytest.raises(Held, match="attempt"):
        journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    rows = journal.db.execute(
        "SELECT id,state,data,result FROM commands WHERE operation='withdraw' ORDER BY rowid"
    ).fetchall()
    assert [row[0] for row in rows] == identities
    assert all(row[1] == "refused-unissued" and row[2] and row[3] for row in rows)
    assert journal.db.execute("SELECT lifecycle,active FROM requests").fetchone() == (127, 1)
