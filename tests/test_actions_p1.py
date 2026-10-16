"""Pure P1 reducers plus private temporary SQLite fixtures. No native/transport/model ports."""

import sqlite3

import msgspec
import pytest

from heterodyne.actions.contracts import ActionConfig, Intent, Origin, PushBranch, PushFacts, Repository
from heterodyne.actions.decisions import (
    DecisionObservation,
    begin_decision,
    finalize_decision,
    issue_decision,
    read_decision,
    settle_heart_proposal,
)
from heterodyne.actions.dispatch import Clock, consume_claim, prepare, ready, uncertain
from heterodyne.actions.fake_ports import FakeEffect, FakeQueue, FakeSession, UnavailablePorts
from heterodyne.actions.journal import FixtureJournal, create_fixture, upgrade_fixture
from heterodyne.actions.requests import (
    Refused,
    groom,
    issue_allocation,
    new_request,
    observe_allocation,
    observe_filed,
    observe_stage,
    observe_sufficient,
    pin_ask,
    settle_request,
)

SHA = "a" * 40
DIGEST = "b" * 64
REPO = Repository("product", "product-id", "origin", "sha1", DIGEST)
CONFIG = ActionConfig((REPO,))
ORIGIN = Origin("heterodyne", "btq-efddg", "session-1", 1, "product")
PUSH = PushBranch("product", "refs/heads/feature", "refs/heads/main", SHA)
FACTS = PushFacts(REPO, ORIGIN.bead, PUSH.from_ref, PUSH.to_ref, SHA)


def initial(sequence=1, origin=ORIGIN):
    return new_request(origin, sequence, Intent(1, PUSH), FACTS, CONFIG, SHA)


def allocated():
    return observe_allocation(issue_allocation(initial()), ("btq-ab123",), lookup_ok=True, elapsed_seconds=1)


def staged(phase="landed"):
    value = allocated()
    for operation in ("blocker", "park", "land"):
        value = observe_stage(
            value,
            operation,
            key=value.key,
            approval_id=value.approval_id,
            origin=value.origin,
            source=SHA,
            readback_ok=True,
        )
        if value.phase == phase:
            return value
    return value


def filed():
    value = pin_ask(staged(), b'{"full":"native fixture ask"}', DIGEST, revision=1)
    value = observe_sufficient(value, digest=DIGEST, policy_revision=DIGEST, source=SHA, sufficient=True)
    return observe_filed(value, digest=DIGEST, revision=1, native_readback=True)


def event(value="approve"):
    return DecisionObservation(
        "event-1",
        "marmot-fixture",
        "operator-fixture",
        "marmot:fixture",
        "fixture-time",
        "btq-ab123",
        "execution",
        DIGEST,
        value,
        current_principal=True,
        surface_bound=True,
        authority_current=True,
    )


def begun(value="approve"):
    return begin_decision(filed(), event(value), expected_purpose="execution", current_digest=DIGEST)


def committed(value="approve"):
    result = read_decision(
        issue_decision(begun(value), fresh_authority=True, current_digest=DIGEST), event(value), read_ok=True
    )
    for operation in ("close", "pins", "finish"):
        result = finalize_decision(
            result, operation, readback=event(value), current_digest=DIGEST, native_readback=True
        )
    return result


def persist_decision(db, value="approve", approval_id="btq-ab123"):
    """Drive the actual journal/reducers; a constructor boolean is never durable consent."""
    observation = msgspec.structs.replace(event(value), approval_id=approval_id)
    request = msgspec.structs.replace(filed(), approval_id=approval_id)
    result = begin_decision(request, observation, expected_purpose="execution", current_digest=DIGEST)
    outcome = db.add_decision(result)
    if outcome == "duplicate":
        return db.get("decision", approval_id)
    result = db.transition(
        "decision",
        result.approval_id,
        result.version,
        lambda v: issue_decision(v, fresh_authority=True, current_digest=DIGEST),
    )
    result = db.transition(
        "decision", result.approval_id, result.version, lambda v: read_decision(v, observation, read_ok=True)
    )
    for operation in ("close", "pins", "finish"):
        result = db.transition(
            "decision",
            result.approval_id,
            result.version,
            lambda v, operation=operation: finalize_decision(
                v, operation, readback=observation, current_digest=DIGEST, native_readback=True
            ),
        )
    return result


def test_create_ack_loss_unique_adoption_no_second_create():
    value, queue = issue_allocation(initial()), FakeQueue()
    with pytest.raises(TimeoutError):
        queue.create(value.key, "btq-ab123", lose_ack=True)
    value = observe_allocation(value, queue.lookup(value.key), lookup_ok=True, elapsed_seconds=1)
    assert value.phase == "allocated" and queue.create_calls == 1
    assert value.envelope.idempotency_key == "btq-ab123"
    with pytest.raises(Refused):
        issue_allocation(value)


@pytest.mark.parametrize("ids,ok", [((), True), ((), False), (("btq-ab123", "btq-cd456"), True)])
def test_unknown_zero_or_duplicate_creation_never_retries(ids, ok):
    value = issue_allocation(initial())
    for elapsed in (1, 5, 30):
        value = observe_allocation(value, ids, lookup_ok=ok, elapsed_seconds=elapsed)
    assert value.phase == "allocation-unknown" and value.allocation_lookups == 3
    with pytest.raises(Refused):
        observe_allocation(value, ids, lookup_ok=ok, elapsed_seconds=31)
    with pytest.raises(Refused):
        issue_allocation(value)


def test_previously_seen_duplicate_cannot_be_erased_by_later_unique_lookup():
    value = observe_allocation(
        issue_allocation(initial()), ("btq-ab123", "btq-cd456"), lookup_ok=True, elapsed_seconds=1
    )
    value = observe_allocation(value, ("btq-ab123",), lookup_ok=True, elapsed_seconds=5)
    assert value.phase == "allocation-unknown" and value.shell_ids == ("btq-ab123", "btq-cd456")


@pytest.mark.parametrize("operation", ["park", "land"])
def test_native_stages_cannot_skip_blocker_or_park(operation):
    value = allocated()
    with pytest.raises(Refused):
        observe_stage(
            value,
            operation,
            key=value.key,
            approval_id=value.approval_id,
            origin=value.origin,
            source=SHA,
            readback_ok=True,
        )


def test_native_stages_bind_exact_original_generation_and_requested_source():
    value = allocated()
    for override in (
        {"origin": msgspec.structs.replace(ORIGIN, generation=2)},
        {"source": "c" * 40},
        {"approval_id": "btq-cd456"},
        {"readback_ok": False},
    ):
        kwargs = dict(
            key=value.key, approval_id=value.approval_id, origin=ORIGIN, source=SHA, readback_ok=True
        )
        with pytest.raises(Refused):
            observe_stage(value, "blocker", **(kwargs | override))
    session = FakeSession(ORIGIN.session_key, 2)
    with pytest.raises(Refused):
        session.park(ORIGIN.session_key, 1)


def test_complete_filing_and_stale_digest_source_policy_refused():
    value = pin_ask(staged(), b"whole native ask fixture", DIGEST, revision=1)
    for wrong in (
        {"digest": "c" * 64},
        {"source": "c" * 40},
        {"policy_revision": "unknown"},
        {"sufficient": False},
    ):
        kwargs = dict(digest=DIGEST, policy_revision=DIGEST, source=SHA, sufficient=True)
        with pytest.raises(Refused):
            observe_sufficient(value, **(kwargs | wrong))
    assert filed().phase == "filed"
    with pytest.raises(Refused):
        observe_filed(value, digest=DIGEST, revision=1, native_readback=True)


def test_groom_limit_and_source_change_clears_whole_ask_and_judgement():
    value = pin_ask(staged(), b"whole ask", DIGEST, revision=1)
    for revision in (2, 3):
        value = groom(value, Intent(1, PUSH, "corrected"), FACTS, SHA, origin=ORIGIN)
        assert value.phase == "blocker-pending" and value.ask_bytes is None and value.context_digest is None
        assert value.policy_revision is None and value.request_revision == revision
        for operation in ("park", "land"):
            value = observe_stage(
                value,
                operation,
                key=value.key,
                approval_id=value.approval_id,
                origin=ORIGIN,
                source=SHA,
                readback_ok=True,
            )
        value = pin_ask(value, b"corrected whole ask", DIGEST, revision=revision)
    with pytest.raises(Refused):
        groom(value, Intent(1, PUSH), FACTS, SHA, origin=ORIGIN)


@pytest.mark.parametrize("field", ["current_principal", "surface_bound", "authority_current"])
def test_decision_does_not_trust_actor_or_digest_alone(field):
    with pytest.raises(Refused):
        begin_decision(
            filed(),
            msgspec.structs.replace(event(), **{field: False}),
            expected_purpose="execution",
            current_digest=DIGEST,
        )


def test_wrong_purpose_digest_or_unfiled_shell_has_no_decision_route():
    for request, observed, digest, purpose in [
        (allocated(), event(), DIGEST, "execution"),
        (filed(), event(), "c" * 64, "execution"),
        (filed(), event(), DIGEST, "permission"),
    ]:
        with pytest.raises(Refused):
            begin_decision(request, observed, expected_purpose=purpose, current_digest=digest)


def test_decision_ack_loss_readback_and_pins_after_close_are_required():
    queue = FakeQueue()
    value = issue_decision(begun(), fresh_authority=True, current_digest=DIGEST)
    with pytest.raises(TimeoutError):
        queue.decide(event(), lose_ack=True)
    with pytest.raises(Refused):
        issue_decision(value, fresh_authority=True, current_digest=DIGEST)
    with pytest.raises(Refused):
        read_decision(value, None, read_ok=False)
    value = read_decision(value, queue.read_decision("btq-ab123"), read_ok=True)
    assert value.phase == "native-written" and queue.decision_writes == 1
    with pytest.raises(Refused):
        finalize_decision(value, "pins", readback=event(), current_digest=DIGEST, native_readback=True)
    queue.close_decision(event())
    value = finalize_decision(value, "close", readback=event(), current_digest=DIGEST, native_readback=True)
    assert value.phase == "native-closed"  # Closed alone is still NOT committed.
    queue.finish_pins(event())
    value = finalize_decision(value, "pins", readback=event(), current_digest=DIGEST, native_readback=True)
    value = finalize_decision(value, "finish", readback=event(), current_digest=DIGEST, native_readback=True)
    assert value.phase == "committed"


def test_first_decision_is_durable_and_conflicts_never_overwrite(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    db.add_decision(begun())
    db.add_decision(begun())
    with pytest.raises(Refused):
        db.add_decision(begun("deny"))
    db.close()
    db = FixtureJournal(path)
    assert db.get("decision", "btq-ab123").event.value == "approve"
    db.close()


def test_two_outstanding_per_bead_across_generations_and_sessions(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    assert db.add_request(initial()) == db.add_request(initial())
    db.add_request(initial(2, msgspec.structs.replace(ORIGIN, generation=2, session_key="session-2")))
    with pytest.raises(Refused, match="two-outstanding"):
        db.add_request(initial(3, msgspec.structs.replace(ORIGIN, generation=3)))
    db.close()


def test_cleanup_missing_stale_or_duplicate_shells_cannot_free_capacity():
    value = allocated()
    for overrides in (
        {"cleaned_shells": ()},
        {"blockers_clear": False},
        {"native_terminal": False},
        {"origin": msgspec.structs.replace(ORIGIN, generation=2)},
    ):
        kwargs = dict(
            key=value.key,
            origin=ORIGIN,
            cleaned_shells=value.shell_ids,
            blockers_clear=True,
            native_terminal=True,
        )
        with pytest.raises(Refused):
            settle_request(value, "cancelled", **(kwargs | overrides))


def test_stale_version_cannot_release_successor_or_duplicate_effect(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    key = db.add_request(initial())
    db.transition("request", key, 0, issue_allocation)
    with pytest.raises(Refused):
        db.transition("request", key, 0, issue_allocation)
    assert db.get("request", key).version == 1
    db.close()


def test_dispatch_claim_crash_restart_retains_lock_and_prevents_replay(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    clock = Clock("boot-one", 10**12, 10**15)
    value = prepare(
        "btq-ab123", 1, DIGEST, DIGEST, "attempt-1", "resource", "c" * 64, clock, fake_finalized_approval=True
    )
    persist_decision(db)
    db.add_dispatch(value)
    value = db.transition(
        "dispatch",
        value.approval_id,
        0,
        lambda v: ready(v, "d" * 64, clock, fresh_gates=True, startup_readback=True),
    )
    db.transition(
        "dispatch",
        value.approval_id,
        value.version,
        lambda v: consume_claim(v, "d" * 64, clock, fresh_gates=True),
    )
    db.close()  # Lost native acknowledgement after the durable claim.
    db = FixtureJournal(path)
    value = db.get("dispatch", "btq-ab123")
    assert value.claim_consumed and value.state == "executing"
    db.transition("dispatch", value.approval_id, value.version, uncertain)
    with pytest.raises(Refused):
        db.add_dispatch(msgspec.structs.replace(value, version=0, state="preparing"))
    other = prepare(
        "btq-cd456", 1, DIGEST, DIGEST, "attempt-2", "resource", "e" * 64, clock, fake_finalized_approval=True
    )
    persist_decision(db, approval_id=other.approval_id)
    with pytest.raises(Refused, match="resource-still-held"):
        db.add_dispatch(other)
    db.close()


def test_one_use_fake_effect_and_unavailable_production_ports():
    effect = FakeEffect()
    with pytest.raises(Refused):
        effect.perform("attempt", claim_consumed_readback=False)
    effect.perform("attempt", claim_consumed_readback=True)
    assert effect.reconcile("attempt") == "succeeded"
    with pytest.raises(Refused):
        effect.perform("attempt", claim_consumed_readback=True)
    with pytest.raises(Refused, match="adapter-unavailable"):
        UnavailablePorts.invoke("dispatch")


@pytest.mark.parametrize("stop", [1, 2, 3, 4, 5])
def test_fixture_migration_failure_rolls_back_exact_schema_and_preserves_request(tmp_path, stop):
    path = tmp_path / "fixture.sqlite"
    create_fixture(path, version=1)
    db = sqlite3.connect(path)
    before = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
    db.execute("INSERT INTO requests VALUES('saved','heterodyne','btq-efddg',7,1,X'1234')")
    db.commit()
    db.close()
    calls = []

    def checkpoint(_):
        calls.append(1)
        if len(calls) == stop:
            raise RuntimeError("synthetic-migration-crash")

    with pytest.raises(RuntimeError):
        upgrade_fixture(path, checkpoint)
    db = sqlite3.connect(path)
    assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert db.execute("SELECT version FROM meta").fetchall() == [(1,)]
    assert db.execute("SELECT version,data FROM requests").fetchall() == [(7, b"\x12\x34")]
    db.close()
    upgrade_fixture(path)
    db = FixtureJournal(path)
    assert db.db.execute("SELECT version,data FROM requests").fetchall() == [(7, b"\x12\x34")]
    db.close()


def test_foreign_partial_and_unknown_schema_not_implicitly_migrated(tmp_path):
    path = tmp_path / "foreign.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE secrets(value)")
    db.commit()
    db.close()
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(Refused):
        upgrade_fixture(path)
    assert path.read_bytes() == before


def test_denial_persists_across_generation_and_heart_proposal_is_separate(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    request, decision = filed(), committed("deny")
    assert persist_decision(db, "deny") == decision
    db.record_denial(request, decision)
    db.close()
    db = FixtureJournal(path)
    assert db.denied(request.denial_identity, ORIGIN.session_key)
    assert not db.denied(request.denial_identity, "other-session")
    db.close()
    approved = msgspec.structs.replace(committed(), heart_proposal_pending=True)
    settled = settle_heart_proposal(approved, proposal_readback=True)
    assert settled.event == approved.event and settled.phase == "committed"


def test_exhausted_allocation_recovery_requires_exact_current_authenticated_scope():
    from heterodyne.actions.requests import AllocationRecovery, recover_allocation

    value = issue_allocation(initial())
    for elapsed in (1, 5, 30):
        value = observe_allocation(value, ("btq-ab123", "btq-cd456"), lookup_ok=True, elapsed_seconds=elapsed)
    observation = AllocationRecovery(
        value.key,
        ORIGIN,
        "btq-ab123",
        value.shell_ids,
        ("btq-cd456",),
        value.version,
        DIGEST,
        "marmot-fixture-event",
        True,
        True,
        True,
    )
    for updates in (
        {"authenticated_current": False},
        {"exact_repair_scope": False},
        {"native_readback": False},
        {"expected_version": 0},
        {"closed_orphans": ()},
    ):
        with pytest.raises(Refused):
            recover_allocation(value, msgspec.structs.replace(observation, **updates))
    fixed = recover_allocation(value, observation)
    assert fixed.phase == "allocated" and fixed.shell_ids == value.shell_ids
    assert fixed.recovery_operator_event == observation.operator_event
    assert fixed.recovery_evidence == observation.evidence_digest
    assert fixed.recovery_closed_orphans == observation.closed_orphans
    assert fixed.envelope.idempotency_key == "btq-ab123"
    with pytest.raises(Refused):
        issue_allocation(fixed)


def test_denial_blocks_allocation_before_shell_across_generation_after_restart(tmp_path):
    path = tmp_path / "fixture.sqlite"
    db = FixtureJournal.create(path)
    request = filed()
    db.record_denial(request, persist_decision(db, "deny"))
    db.record_denial(request, persist_decision(db, "deny"))
    assert db.db.execute("SELECT count(*) FROM events WHERE kind='denial'").fetchone() == (1,)
    db.close()
    db = FixtureJournal(path)
    retry = initial(2, msgspec.structs.replace(ORIGIN, generation=2))
    assert retry.denial_identity == request.denial_identity
    with pytest.raises(Refused, match="same-session-denied-intent"):
        db.add_request(retry)
    assert db.db.execute("SELECT count(*) FROM requests").fetchone() == (0,)
    db.close()


def test_changed_intent_retains_prior_denial_relationship_before_allocation(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    request = filed()
    db.record_denial(request, persist_decision(db, "deny"))
    action = msgspec.structs.replace(PUSH, expected_sha="c" * 40)
    facts = msgspec.structs.replace(FACTS, source_sha="c" * 40)
    value = new_request(ORIGIN, 2, Intent(1, action), facts, CONFIG, SHA)
    assert value.denial_identity != request.denial_identity
    with pytest.raises(Refused, match="denial-history-required"):
        db.add_request(value)
    retained = msgspec.structs.replace(value, prior_denials=(request.denial_identity,))
    key = db.add_request(retained)
    assert db.get("request", key).prior_denials == (request.denial_identity,)
    db.close()


def test_two_connections_claim_retirement_race_has_only_one_transition(tmp_path):
    from heterodyne.actions.dispatch import retire_unconsumed

    path = tmp_path / "fixture.sqlite"
    left = FixtureJournal.create(path)
    right = FixtureJournal(path)
    clock = Clock("boot-one", 10**12, 10**15)
    value = prepare(
        "btq-ab123", 1, DIGEST, DIGEST, "attempt-1", "resource", "c" * 64, clock, fake_finalized_approval=True
    )
    persist_decision(left)
    left.add_dispatch(value)
    value = left.transition(
        "dispatch",
        value.approval_id,
        0,
        lambda v: ready(v, "d" * 64, clock, fresh_gates=True, startup_readback=True),
    )
    left.transition(
        "dispatch",
        value.approval_id,
        value.version,
        lambda v: consume_claim(v, "d" * 64, clock, fresh_gates=True),
    )
    with pytest.raises(Refused, match="stale-fixture-version"):
        right.transition(
            "dispatch",
            value.approval_id,
            value.version,
            lambda v: retire_unconsumed(v, "d" * 64, launcher_exited=True, no_children=True),
        )
    assert right.get("dispatch", value.approval_id).claim_consumed
    left.close()
    right.close()


def test_reducer_exception_rolls_back_record_and_event(tmp_path):
    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    key = db.add_request(initial())

    def broken(value):
        issue_allocation(value)
        raise RuntimeError("synthetic-reducer-interruption")

    with pytest.raises(RuntimeError):
        db.transition("request", key, 0, broken)
    assert db.get("request", key) == initial()
    assert db.db.execute("SELECT count(*) FROM events").fetchone() == (1,)
    db.close()


def test_reconciliation_success_is_one_cas_and_releases_only_settled_resource(tmp_path):
    from heterodyne.actions.dispatch import reconcile_lookup

    db = FixtureJournal.create(tmp_path / "fixture.sqlite")
    clock = Clock("boot-one", 10**12, 10**15)
    value = prepare(
        "btq-ab123", 1, DIGEST, DIGEST, "attempt-1", "resource", "c" * 64, clock, fake_finalized_approval=True
    )
    persist_decision(db)
    db.add_dispatch(value)
    for reducer in (
        lambda v: ready(v, "d" * 64, clock, fresh_gates=True, startup_readback=True),
        lambda v: consume_claim(v, "d" * 64, clock, fresh_gates=True),
        uncertain,
    ):
        value = db.transition("dispatch", value.approval_id, value.version, reducer)
    alias = prepare(
        "btq-cd456",
        1,
        DIGEST,
        DIGEST,
        "attempt-2",
        "different-alias",
        "e" * 64,
        clock,
        fake_finalized_approval=True,
    )
    persist_decision(db, approval_id=alias.approval_id)
    with pytest.raises(Refused, match="resource-still-held"):
        db.add_dispatch(alias)
    value = db.transition(
        "dispatch",
        value.approval_id,
        value.version,
        lambda v: reconcile_lookup(
            v, elapsed_seconds=1, effect="succeeded", cleanup_proven=True, evidence=DIGEST
        ),
    )
    assert value.settled
    db.add_dispatch(alias)
    assert db.db.execute("SELECT count(*) FROM resources").fetchone() == (3,)
    db.close()


def test_fixture_upgrade_missing_path_never_creates_and_bad_version_never_creates(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        upgrade_fixture(path)
    assert not path.exists()
    with pytest.raises(Refused, match="fixture-schema-version"):
        create_fixture(path, version=True)
    assert not path.exists()
