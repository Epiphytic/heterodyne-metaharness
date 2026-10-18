"""R1 recovery regressions: disposable ports, no native effects."""

import pytest
import test_actions_filing as original
from test_actions_filing import landed
from test_actions_p1 import DIGEST, initial

from heterodyne.actions import requests as r
from heterodyne.actions.contracts import Clock
from heterodyne.actions.filing import BeforeIssue, Cleanup, Held, key

owner = original.owner


def test_all_pending_withdrawals_block_second_cleanup(owner):
    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    journal.execute(journal.prepare(key(value), "allocate", DIGEST).command_id)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(journal.prepare(key(value), "blocker", DIGEST).command_id)
    first = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    with pytest.raises(OSError):
        journal.execute(first.command_id)
    with pytest.raises(Held, match="pending"):
        journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))


def test_prepared_can_be_abandoned_without_port_call(owner):
    journal, ports, _ = owner
    value = landed(owner)
    command = journal.prepare(key(value), "groom", DIGEST, b"{}")
    calls = len(ports.calls)
    journal.abandon_prepared(command.command_id)
    assert journal.command(command.command_id)[1] == "refused-unissued"
    assert len(ports.calls) == calls
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
    journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled", cancellation=recovery))


def test_before_issue_allocation_is_terminal_without_recreate(owner):
    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)

    def refuse(command):
        raise BeforeIssue("fixture-proved-no-call")

    ports.invoke = refuse
    command = journal.prepare(key(value), "allocate", DIGEST)
    journal.execute(command.command_id)
    assert journal.request(key(value)).phase == "cancelled"
    assert journal.request(key(value)).create_issued is True
    with pytest.raises(Held):
        journal.prepare(key(value), "allocate", DIGEST)


def test_first_lookup_waits_from_actual_issue(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    now[0] = Clock("fixture-boot", 10_000_000_000, 10_000_000_000)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    with pytest.raises(Held, match="deadline"):
        journal.reconcile(command.command_id)
    assert not ports.reads


def test_failed_read_normalizes_issuing(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    journal.issue(command.command_id, ports.current(command))
    ports.fail = True
    now[0] = Clock("fixture-boot", 1_000_000_000, 1_000_000_000)
    with pytest.raises(OSError):
        journal.reconcile(command.command_id)
    assert journal.command(command.command_id)[1] == "uncertain"


def test_cleanup_shape_refused_before_effect(owner):
    journal, ports, _ = owner
    value = landed(owner)
    before = len(ports.calls)
    with pytest.raises(Held, match="cleanup"):
        journal.prepare(key(value), "withdraw", DIGEST, b"garbage")
    assert len(ports.calls) == before


def test_provider_cannot_use_generic_mutation_port(owner):
    journal, ports, _ = owner
    value = landed(owner)
    command = journal.prepare(key(value), "provider", DIGEST)
    before = len(ports.calls)
    with pytest.raises(Held, match="provider"):
        journal.execute(command.command_id)
    assert len(ports.calls) == before


def test_split_readback_rejects_duplicates_without_rewinding():
    value = r.issue_allocation(initial())
    consumed = r.consume_allocation_lookup(value, elapsed_seconds=1)
    assert consumed.allocation_lookups == 1
    with pytest.raises(r.Refused):
        r.adopt_allocation_readback(consumed, ("btq-ab123", "btq-ab123"), lookup_ok=True)


def test_unavailable_reads_do_not_consume_cleanup_reserve(owner):
    import msgspec

    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    journal.execute(journal.prepare(key(value), "allocate", DIGEST).command_id)
    command = journal.prepare(key(value), "blocker", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    ports.fail = False
    ports.read = lambda current: msgspec.structs.replace(ports.observation(current), outcome="unavailable")
    count = journal.db.execute("SELECT count(*) FROM events").fetchone()[0]
    for _ in range(200):
        journal.reconcile(command.command_id)
    assert journal.db.execute("SELECT count(*) FROM events").fetchone()[0] == count
    journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))


def test_lint_refusal_is_owned_no_call_receipt_not_pending(owner):
    import asyncio

    import msgspec
    from gatekeeper_fixtures import privacy, seal
    from test_actions_p2 import SPEC, Provider, inputs

    from heterodyne.gatekeeper import contracts as g
    from heterodyne.gatekeeper.validate import data, native_lint_gap

    journal, _, _ = owner
    value = landed(owner)
    _, request = inputs()
    request = seal(data(msgspec.structs.replace(request, lint=g.Failed(native_lint_gap(True)))))
    provider = Provider(request)
    command_id, receipt = asyncio.run(
        journal.assess(key(value), request, privacy(), SPEC, provider, policy_revision=DIGEST)
    )
    assert provider.calls == []
    assert receipt.attempt is None
    assert journal.command(command_id)[1] == "refused-unissued"
    assert isinstance(journal.projection(command_id, request, privacy(), SPEC), g.NeedsGrooming)
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
    journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled", cancellation=recovery))


def test_three_failed_restart_reads_still_allow_authenticated_abandonment(owner):
    from heterodyne.actions.filing import FilingJournal

    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    journal.issue(command.command_id, ports.current(command))
    path = journal.db.execute("PRAGMA database_list").fetchone()[2]
    journal.db.close()
    from pathlib import Path

    journal = FilingJournal.open(Path(path), clock=lambda: now[0], nonce=lambda: "f" * 32, ports=ports)
    try:
        assert journal.command(command.command_id)[1] == "uncertain"
        ports.fail = True
        for seconds in (1, 6, 36):
            now[0] = Clock("fixture-boot", seconds * 1_000_000_000, seconds * 1_000_000_000)
            with pytest.raises(OSError):
                journal.reconcile(command.command_id)
        current = journal.request(key(value))
        recovery = r.AbandonmentRecovery(
            current.key,
            current.origin,
            current.version,
            (),
            DIGEST,
            "fixture-authenticated",
            True,
            True,
            True,
        )
        cleanup = journal.prepare_cleanup(
            key(value), DIGEST, Cleanup("reconciled-abandoned", abandonment=recovery)
        )
        assert cleanup.request.allocation_lookups == 3
    finally:
        journal.db.close()


def test_split_legacy_reducer_preserves_due_count_and_versions():
    value = r.issue_allocation(initial())
    with pytest.raises(r.Refused, match="not-due"):
        r.consume_allocation_lookup(value, elapsed_seconds=0)
    combined = r.observe_allocation(value, ("btq-ab123",), lookup_ok=True, elapsed_seconds=1)
    consumed = r.consume_allocation_lookup(value, elapsed_seconds=1)
    adopted = r.adopt_allocation_readback(consumed, ("btq-ab123",), lookup_ok=True)
    assert combined.version == value.version + 1
    assert adopted.allocation_lookups == combined.allocation_lookups == 1
    assert adopted.phase == combined.phase == "allocated"
    assert adopted.shell_ids == combined.shell_ids


def test_cleanup_recovery_shape_is_rejected_before_any_call(owner):
    from heterodyne.actions.filing import encoded

    journal, ports, _ = owner
    value = landed(owner)
    recovery = r.CancellationRecovery(
        value.key, value.origin, value.version, value.shell_ids, DIGEST, "fixture-event"
    )
    calls = len(ports.calls)
    with pytest.raises(Held, match="shape"):
        journal.prepare(key(value), "withdraw", DIGEST, encoded(Cleanup("denied", cancellation=recovery)))
    assert len(ports.calls) == calls


def test_terminal_slots_survive_full_ordinary_budget(owner):
    import msgspec

    from heterodyne.actions.filing import encoded

    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    journal.execute(journal.prepare(key(value), "allocate", DIGEST).command_id)
    value = journal.request(key(value))
    with journal.transaction():
        while journal.db.execute("SELECT lifecycle FROM requests").fetchone()[0] < 124:
            journal.append_event(key(value), "fixture-bounded-history", b"{}")
    with pytest.raises(Held, match="capacity"):
        with journal.transaction():
            journal.append_event(key(value), "ordinary", b"{}")
    command = journal.prepare_cleanup(key(value), DIGEST, Cleanup("cancelled"))
    issued = journal.issue(command.command_id, ports.current(command))
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
        command.command_id,
        msgspec.structs.replace(
            ports.observation(issued),
            blockers_clear=True,
            native_terminal=True,
            reducer_request=encoded(terminal),
        ),
    )
    assert journal.request(key(value)) == terminal
    assert journal.db.execute("SELECT lifecycle,active FROM requests").fetchone() == (127, 0)
