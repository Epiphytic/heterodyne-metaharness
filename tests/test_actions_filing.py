"""Disposable P3 ownership tests; no native authority or actual queue transport."""

import asyncio
import itertools

import msgspec
import pytest
from gatekeeper_fixtures import privacy
from test_actions_p1 import DIGEST, SHA, initial
from test_actions_p2 import SPEC, Provider, inputs

from heterodyne.actions import judgement as j
from heterodyne.actions import requests as r
from heterodyne.actions.contracts import Clock
from heterodyne.actions.filing import Current, FilingJournal, Held, Observation, digest, encoded, key
from heterodyne.gatekeeper import contracts as g


def test_ports_missing_refuses_before_creating_journal(tmp_path):
    with pytest.raises(Held, match="capability"):
        FilingJournal.create(
            tmp_path / "filing.db", clock=lambda: Clock("fixture-boot", 0, 0), nonce=lambda: "n", ports=None
        )
    assert not (tmp_path / "filing.db").exists()


class FixturePorts:
    fixture_only = True

    def __init__(self):
        self.calls = []
        self.reads = []
        self.fail = False
        self.ids = ("btq-ab123",)

    def current(self, command):
        return Current(
            command.command_id,
            command.snapshot_sha256,
            command.policy_revision,
            command.request.origin,
            command.request.requested_source,
            command.request.origin.session_key,
            True,
            True,
        )

    def observation(self, command):
        return Observation(
            command.command_id,
            command.snapshot_sha256,
            command.policy_revision,
            command.request.origin,
            command.request.requested_source,
            "observed",
            shell_ids=self.ids,
            approval_id=command.request.approval_id,
            claim_holder=command.request.origin.session_key,
            blocked=True,
            parked=True,
            retained_claim=True,
            native_readback=True,
            whole_ask=command.request.ask_bytes,
            context_digest=command.request.context_digest,
            revision=command.request.request_revision,
            blocked_bead=command.request.origin.bead,
            blocker_ids=(command.request.approval_id,) if command.request.approval_id else (),
            stopped_sessions=(command.request.origin.session_key,),
            landed_repository=command.request.origin.repo_alias,
            landed_source=command.request.requested_source,
            landed_tree=SHA,
        )

    def invoke(self, command):
        self.calls.append(command)
        if self.fail:
            raise OSError("fixture lost acknowledgement")
        return self.observation(command)

    def read(self, command):
        self.reads.append(command)
        if self.fail:
            raise OSError("fixture read unavailable")
        return self.observation(command)


@pytest.fixture
def owner(tmp_path):
    ports = FixturePorts()
    now = [Clock("fixture-boot", 0, 0)]
    numbers = itertools.count()
    journal = FilingJournal.create(
        tmp_path / "filing.db", clock=lambda: now[0], nonce=lambda: f"{next(numbers):032x}", ports=ports
    )
    yield journal, ports, now
    journal.db.close()


def landed(owner):
    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    for operation in ("allocate", "blocker", "park", "land"):
        command = (
            journal.prepare_landing(key(value), DIGEST, tree=SHA)
            if operation == "land"
            else journal.prepare(key(value), operation, DIGEST)
        )
        journal.execute(command.command_id)
    value = journal.request(key(value))
    assert value.phase == "landed"
    return journal.pin(key(value), b'{"whole":"native opaque fixture"}', DIGEST)


def test_ordered_filing_uses_real_reducers_and_issuance_before_calls(owner):
    journal, ports, _ = owner
    action = landed(owner)
    _, request = inputs()
    provider = Provider(request)
    command_id, receipt = asyncio.run(
        journal.assess(key(action), request, privacy(), SPEC, provider, policy_revision=DIGEST)
    )
    assert receipt.attempt.nonce == command_id == provider.calls[0].nonce
    actual, state = journal.command(command_id)
    assert state == "observed"
    invocation = msgspec.json.decode(actual.payload, type=j.Invocation)
    assert invocation == provider.calls[0]
    assert journal.projection(command_id, request, privacy(), SPEC) == g.SufficientProposal()
    sufficient = journal.sufficient(command_id, request, privacy(), SPEC)
    command = journal.prepare(key(sufficient), "file", DIGEST)
    journal.execute(command.command_id)
    assert journal.request(key(action)).phase == "filed"
    assert [c.operation for c in ports.calls] == ["allocate", "blocker", "park", "land", "file"]
    with pytest.raises(Held):
        journal.execute(command.command_id)
    assert len(ports.calls) == 5


def test_restart_never_replays_lost_create_and_spaced_reads_retain_unknown(owner, tmp_path):
    journal, ports, now = owner
    action = initial()
    journal.reserve(action)
    command = journal.prepare(key(action), "allocate", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    journal.db.close()
    reopened = FilingJournal.open(
        tmp_path / "filing.db", clock=lambda: now[0], nonce=lambda: "must-not-create", ports=ports
    )
    # Fixture keeps ownership of the replacement connection for cleanup.
    journal.db = reopened.db
    assert reopened.request(key(action)).create_issued
    with pytest.raises(Held):
        reopened.prepare(key(action), "allocate", DIGEST)
    for delta in (1, 5, 30):
        with pytest.raises(Held, match="deadline"):
            reopened.reconcile(command.command_id)
        now[0] = Clock("fixture-boot", now[0].mono_ns + delta * 1_000_000_000, 0)
        with pytest.raises(OSError):
            reopened.reconcile(command.command_id)
    assert reopened.request(key(action)).allocation_lookups == 3
    assert reopened.db.execute("SELECT active FROM requests").fetchone()[0] == 1
    with pytest.raises(Held, match="deadline"):
        reopened.reconcile(command.command_id)
    assert len(ports.calls) == 1
    assert len(ports.reads) == 3


def test_lost_create_unique_adoption_never_recreates(owner):
    journal, ports, now = owner
    action = initial()
    journal.reserve(action)
    command = journal.prepare(key(action), "allocate", DIGEST)
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    ports.fail = False
    now[0] = Clock("fixture-boot", 1_000_000_000, 0)
    journal.reconcile(command.command_id)
    assert journal.request(key(action)).approval_id == "btq-ab123"
    assert len(ports.calls) == len(ports.reads) == 1


def test_provider_lost_reply_retains_call_once(owner):
    journal, _, _ = owner
    action = landed(owner)
    _, request = inputs()

    class Lost:
        calls = 0

        async def judge(self, invocation):
            self.calls += 1
            raise OSError("lost reply")

    provider = Lost()
    with pytest.raises(Held, match="unknown"):
        asyncio.run(journal.assess(key(action), request, privacy(), SPEC, provider, policy_revision=DIGEST))
    assert provider.calls == 1
    with pytest.raises(Held, match="pending"):
        asyncio.run(journal.assess(key(action), request, privacy(), SPEC, provider, policy_revision=DIGEST))
    assert provider.calls == 1
    assert journal.db.execute("SELECT active FROM requests").fetchone()[0] == 1


@pytest.mark.parametrize("change", ("boot", "deadline", "source"))
def test_sufficiency_expires_or_rebinds_after_provider(owner, change):
    journal, _, now = owner
    action = landed(owner)
    _, request = inputs()
    command_id, _ = asyncio.run(
        journal.assess(key(action), request, privacy(), SPEC, Provider(request), policy_revision=DIGEST)
    )
    if change == "boot":
        now[0] = Clock("another-boot", 0, 0)
    elif change == "deadline":
        now[0] = Clock("fixture-boot", 60_000_000_000, 0)
    else:
        altered = msgspec.structs.replace(action, requested_source="c" * 40)
        journal.db.execute("UPDATE requests SET data=?", (encoded(altered),))
    with pytest.raises(Held):
        journal.sufficient(command_id, request, privacy(), SPEC)
    assert journal.request(key(action)).phase == "ask-pinned"


def test_independent_current_snapshot_refuses_without_port_call(owner):
    journal, ports, _ = owner
    action = initial()
    journal.reserve(action)
    command = journal.prepare(key(action), "allocate", DIGEST)
    current = msgspec.structs.replace(ports.current(command), source="c" * 40)
    with pytest.raises(Held, match="current"):
        journal.issue(command.command_id, current)
    assert not ports.calls
    assert journal.command(command.command_id)[1] == "prepared"


def test_supplied_nonce_is_exact_and_invalid_nonce_does_not_call_provider():
    action, request = inputs()
    provider = Provider(request)
    receipt = asyncio.run(
        j.assess(
            action,
            request,
            privacy(),
            SPEC,
            provider,
            clock=lambda: 0,
            boot_id="fixture-boot",
            nonce="d" * 32,
        )
    )
    assert receipt.attempt is not None
    assert receipt.attempt.nonce == provider.calls[0].nonce == "d" * 32
    with pytest.raises(r.Refused, match="nonce"):
        asyncio.run(
            j.assess(
                action,
                request,
                privacy(),
                SPEC,
                provider,
                clock=lambda: 0,
                boot_id="fixture-boot",
                nonce="bad\nnonce",
            )
        )
    assert len(provider.calls) == 1


def test_event_traffic_is_bounded_without_displacing_other_request(owner):
    journal, _, _ = owner
    action = initial()
    journal.reserve(action)
    for i in range(100):
        journal.record_detail(key(action), f"event-{i}", b"invalid synthetic event")
    assert journal.db.execute("SELECT count(*) FROM details").fetchone()[0] == 33
    assert journal.db.execute("SELECT count(*) FROM events").fetchone()[0] == 34
    journal.reserve(initial(sequence=2))
    with pytest.raises(Held):
        journal.reserve(initial(sequence=3))


@pytest.mark.parametrize("operation", ("blocker", "park", "land", "file"))
def test_restart_after_write_adopts_exact_readback_without_second_write(owner, operation):
    journal, ports, _ = owner
    action = initial()
    journal.reserve(action)
    for step in ("allocate", "blocker", "park", "land"):
        if step == operation:
            break
        journal.execute(
            (
                journal.prepare_landing(key(action), DIGEST, tree=SHA)
                if step == "land"
                else journal.prepare(key(action), step, DIGEST)
            ).command_id
        )
    if operation == "file":
        action = journal.pin(key(action), b'{"whole":"native opaque fixture"}', DIGEST)
        _, request = inputs()
        provider_id, _ = asyncio.run(
            journal.assess(key(action), request, privacy(), SPEC, Provider(request), policy_revision=DIGEST)
        )
        journal.sufficient(provider_id, request, privacy(), SPEC)
    command = (
        journal.prepare_landing(key(action), DIGEST, tree=SHA)
        if operation == "land"
        else journal.prepare(key(action), operation, DIGEST)
    )
    ports.fail = True
    with pytest.raises(OSError):
        journal.execute(command.command_id)
    calls = len(ports.calls)
    ports.fail = False
    observed = journal.reconcile(command.command_id)
    journal.observe(command.command_id, observed)
    assert journal.command(command.command_id)[1] == "observed"
    assert len(ports.calls) == calls
    assert len(ports.reads) == 1


def test_full_ask_drift_retains_filing_unknown(owner):
    journal, ports, _ = owner
    action = landed(owner)
    _, request = inputs()
    provider_id, _ = asyncio.run(
        journal.assess(key(action), request, privacy(), SPEC, Provider(request), policy_revision=DIGEST)
    )
    journal.sufficient(provider_id, request, privacy(), SPEC)
    command = journal.prepare(key(action), "file", DIGEST)
    issued = journal.issue(command.command_id, ports.current(command))
    wrong = msgspec.structs.replace(ports.observation(issued), whole_ask=b"partial ask")
    with pytest.raises(Held, match="file-readback"):
        journal.observe(command.command_id, wrong)
    assert journal.request(key(action)).phase == "sufficient"
    assert journal.command(command.command_id)[1] == "issuing"


def test_cleanup_only_releases_after_actual_p1_terminal_readback(owner):
    from heterodyne.actions.filing import Cleanup

    journal, ports, _ = owner
    action = initial()
    journal.reserve(action)
    journal.execute(journal.prepare(key(action), "allocate", DIGEST).command_id)
    current = journal.request(key(action))
    command = journal.prepare_cleanup(key(action), DIGEST, Cleanup("cancelled"))
    issued = journal.issue(command.command_id, ports.current(command))
    terminal = r.settle_request(
        current,
        "cancelled",
        key=current.key,
        origin=current.origin,
        cleaned_shells=current.shell_ids,
        blockers_clear=True,
        native_terminal=True,
    )
    observed = msgspec.structs.replace(
        ports.observation(issued),
        blockers_clear=True,
        native_terminal=True,
        reducer_request=encoded(terminal),
    )
    with pytest.raises(Held, match="reducer-proof"):
        journal.observe(command.command_id, msgspec.structs.replace(observed, reducer_request=b"{}"))
    assert journal.db.execute("SELECT active FROM requests").fetchone()[0] == 1
    journal.observe(command.command_id, observed)
    assert journal.request(key(action)) == terminal
    assert journal.db.execute("SELECT active FROM requests").fetchone()[0] == 0
    with pytest.raises(Held):
        journal.prepare(key(action), "allocate", DIGEST)


def request_for(action):
    from gatekeeper_fixtures import seal

    from heterodyne.gatekeeper.validate import prepare

    _, prototype = inputs()
    raw = msgspec.to_builtins(prototype)
    raw["revision"] = action.request_revision
    raw["native_context_digest"] = action.context_digest
    prepared = prepare(seal(raw), privacy())
    assert isinstance(prepared, g.Ready)
    return prepared.value


def test_groom_is_two_durable_same_session_revisions_and_fresh_judgement(owner):
    journal, ports, _ = owner
    action = landed(owner)
    chosen = dict(
        status="groom",
        gaps=[dict(field="context", detail="Explain target")],
        reason="Missing context",
        additions=[],
    )
    for revision in (1, 2):
        request = request_for(action)
        provider_id, _ = asyncio.run(
            journal.assess(
                key(action), request, privacy(), SPEC, Provider(request, chosen), policy_revision=DIGEST
            )
        )
        new_source = str(revision) * 40
        new_origin = msgspec.structs.replace(action.origin, generation=action.origin.generation + 1)
        groom = journal.prepare_groom(
            provider_id,
            request,
            privacy(),
            SPEC,
            action.intent,
            action.resolved,
            new_source,
            origin=new_origin,
        )
        issued = journal.issue(groom.command_id, ports.current(groom))
        observed = msgspec.structs.replace(
            ports.observation(issued), groomed_source=new_source, groomed_origin=new_origin
        )
        journal.observe(groom.command_id, observed)
        action = journal.request(key(action))
        assert action.groom_revisions == revision
        assert action.origin.session_key == initial().origin.session_key
        assert action.ask_bytes is None and action.policy_revision is None
        for stage in ("park", "land"):
            journal.execute(
                (
                    journal.prepare_landing(key(action), DIGEST, tree=SHA)
                    if stage == "land"
                    else journal.prepare(key(action), stage, DIGEST)
                ).command_id
            )
        action = journal.pin(key(action), encoded({"revision": revision}), str(revision) * 64)
    request = request_for(action)
    provider_id, _ = asyncio.run(
        journal.assess(
            key(action), request, privacy(), SPEC, Provider(request, chosen), policy_revision=DIGEST
        )
    )
    with pytest.raises(r.Refused):
        journal.prepare_groom(
            provider_id,
            request,
            privacy(),
            SPEC,
            action.intent,
            action.resolved,
            "3" * 40,
            origin=action.origin,
        )


def test_arbitrary_groom_payload_cannot_launch_session(owner):
    journal, ports, _ = owner
    action = landed(owner)
    command = journal.prepare(key(action), "groom", DIGEST, b"{}")
    with pytest.raises(Held, match="groom-owner"):
        journal.execute(command.command_id)
    assert all(call.operation != "groom" for call in ports.calls)


def test_reserved_overflow_id_cannot_poison_audit(owner):
    journal, _, _ = owner
    action = initial()
    journal.reserve(action)
    with pytest.raises(Held):
        journal.record_detail(key(action), "overflow", b"crafted")
    for i in range(100):
        journal.record_detail(key(action), f"invalid-{i}", b"synthetic")
    raw = journal.db.execute("SELECT data FROM details WHERE id='overflow'").fetchone()[0]
    aggregate = msgspec.json.decode(raw)
    assert aggregate["count"] == 68
    assert len(aggregate["samples"]) == 4
    assert aggregate["samples_sha256"] == digest(tuple(aggregate["samples"]))


def test_restart_refuses_partial_schema(owner, tmp_path):
    journal, ports, now = owner
    journal.db.execute("ALTER TABLE commands RENAME COLUMN lookups TO lost_lookup_count")
    with pytest.raises(Held, match="schema"):
        reopened = FilingJournal.open(
            tmp_path / "filing.db", clock=lambda: now[0], nonce=lambda: "a" * 32, ports=ports
        )
        reopened.db.close()


def test_readback_cannot_reclassify_possible_issue_as_unissued(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    journal.issue(command.command_id, ports.current(command))
    actual, _ = journal.command(command.command_id)
    false_absence = msgspec.structs.replace(ports.observation(actual), outcome="definitely-unissued")
    with pytest.raises(Held, match="unissued"):
        journal.observe(command.command_id, false_absence)
    assert journal.command(command.command_id)[1] == "issuing"


def test_malformed_boolean_observation_never_advances_reducer(owner):
    journal, ports, _ = owner
    value = initial()
    journal.reserve(value)
    allocation = journal.prepare(key(value), "allocate", DIGEST)
    journal.execute(allocation.command_id)
    blocker = journal.prepare(key(value), "blocker", DIGEST)
    journal.issue(blocker.command_id, ports.current(blocker))
    malformed = msgspec.structs.replace(ports.observation(blocker), blocked=1)
    with pytest.raises(Held, match="type"):
        journal.observe(blocker.command_id, malformed)
    assert journal.request(key(value)).phase == "allocated"


def test_global_capacity_is_reserved_before_any_allocation(owner):
    journal, ports, _ = owner
    prototype = initial()
    for index in range(64):
        origin = msgspec.structs.replace(prototype.origin, bead=f"btq-{index:05x}")
        value = r.new_request(
            origin,
            index + 1,
            prototype.intent,
            msgspec.structs.replace(prototype.resolved, source_bead=origin.bead),
            prototype.config,
            prototype.requested_source,
        )
        journal.reserve(value)
    origin = msgspec.structs.replace(prototype.origin, bead="btq-fffff")
    value = r.new_request(
        origin,
        65,
        prototype.intent,
        msgspec.structs.replace(prototype.resolved, source_bead=origin.bead),
        prototype.config,
        prototype.requested_source,
    )
    with pytest.raises(Held, match="capacity"):
        journal.reserve(value)
    assert journal.db.execute("SELECT count(*) FROM requests WHERE active=1").fetchone()[0] == 64
    assert ports.calls == []


def test_issue_boot_change_holds_before_port_call(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    now[0] = Clock("other-boot", 0, 0)
    with pytest.raises(Held, match="clock"):
        journal.execute(command.command_id)
    assert ports.calls == []
    assert journal.command(command.command_id)[1] == "prepared"


def test_multi_shell_readback_retains_every_shell_and_reservation(owner):
    journal, ports, now = owner
    value = initial()
    journal.reserve(value)
    command = journal.prepare(key(value), "allocate", DIGEST)
    ports.ids = ("btq-ab123", "btq-cd456")
    journal.execute(command.command_id)
    assert journal.request(key(value)).shell_ids == ports.ids
    assert journal.command(command.command_id)[1] == "uncertain"
    ports.ids = ("btq-ab123",)
    now[0] = Clock("fixture-boot", 1_000_000_000, 1_000_000_000)
    journal.reconcile(command.command_id)
    assert journal.request(key(value)).shell_ids == ("btq-ab123", "btq-cd456")
    assert journal.request(key(value)).phase == "allocation-unknown"
    assert len(ports.calls) == 1


def test_provider_result_retains_exact_owned_end_and_binding(owner):
    journal, _, now = owner
    action = landed(owner)
    _, request = inputs()

    class Advancing(Provider):
        async def judge(self, invocation):
            reply = await super().judge(invocation)
            now[0] = Clock("fixture-boot", 123, 456)
            return reply

    command_id, receipt = asyncio.run(
        journal.assess(key(action), request, privacy(), SPEC, Advancing(request), policy_revision=DIGEST)
    )
    row = journal.db.execute("SELECT result FROM commands WHERE id=?", (command_id,)).fetchone()
    result = msgspec.json.decode(bytes(row[0]))
    assert result["end"] == {"boot_id": "fixture-boot", "mono_ns": 123, "utc_ns": 456}
    assert result["command_sha256"] == digest(journal.command(command_id)[0])
    assert receipt.attempt.ended_ns == 123


def test_provider_command_changed_during_await_cannot_adopt_reply(owner):
    journal, _, _ = owner
    action = landed(owner)
    _, request = inputs()

    class Mutating(Provider):
        async def judge(self, invocation):
            reply = await super().judge(invocation)
            command, _ = journal.command(invocation.nonce)
            changed = msgspec.structs.replace(command, policy_revision="f" * 64)
            journal.db.execute("UPDATE commands SET data=? WHERE id=?", (encoded(changed), invocation.nonce))
            return reply

    with pytest.raises(Held):
        asyncio.run(
            journal.assess(key(action), request, privacy(), SPEC, Mutating(request), policy_revision=DIGEST)
        )
    assert journal.db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    assert (
        journal.db.execute("SELECT state FROM commands WHERE operation='provider'").fetchone()[0]
        != "observed"
    )


def test_reply_without_owned_end_or_receipt_cannot_restart_projection(owner):
    journal, _, _ = owner
    action = landed(owner)
    _, request = inputs()
    command_id, _ = asyncio.run(
        journal.assess(key(action), request, privacy(), SPEC, Provider(request), policy_revision=DIGEST)
    )
    # Interrupted predecessor format: a raw reply is not an owned completed assessment.
    journal.db.execute("DELETE FROM assessments WHERE id=?", (command_id,))
    journal.db.execute("UPDATE commands SET result=? WHERE id=?", (b"{}", command_id))
    with pytest.raises(Held, match="receipt-held"):
        journal.projection(command_id, request, privacy(), SPEC)


def test_committed_reply_but_receipt_write_failure_never_restarts_provider(owner, tmp_path):
    import sqlite3

    journal, ports, now = owner
    action = landed(owner)
    _, request = inputs()
    provider = Provider(request)

    def deny_receipt(action_code, table, column, database, source):
        return (
            sqlite3.SQLITE_DENY
            if action_code == sqlite3.SQLITE_INSERT and table == "assessments"
            else sqlite3.SQLITE_OK
        )

    journal.db.set_authorizer(deny_receipt)
    with pytest.raises(sqlite3.DatabaseError):
        asyncio.run(journal.assess(key(action), request, privacy(), SPEC, provider, policy_revision=DIGEST))
    journal.db.set_authorizer(None)
    command_id = provider.calls[0].nonce
    assert journal.command(command_id)[1] == "observed"
    assert journal.db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    restarted = FilingJournal.open(
        tmp_path / "filing.db", clock=lambda: now[0], nonce=lambda: "f" * 32, ports=ports
    )
    try:
        with pytest.raises(Held, match="receipt-held"):
            restarted.projection(command_id, request, privacy(), SPEC)
        with pytest.raises(Held):
            restarted.prepare(key(action), "provider", DIGEST)
    finally:
        restarted.db.close()
    assert len(provider.calls) == 1
