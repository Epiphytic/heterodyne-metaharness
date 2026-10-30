"""Actual protected-directory journal I/O; admitted/authenticated inputs are synthetic."""

import os
import shutil
import tempfile
from pathlib import Path

import msgspec
import pytest
from test_deployment_outage import command, packet
from test_deployment_step1 import TARGET

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment import digest
from heterodyne.actions.deployment_host_store import _ProtectedStore
from heterodyne.actions.deployment_outage_journal import _OutageJournal


@pytest.fixture
def journal():
    root = Path(tempfile.mkdtemp(prefix=".outage-journal-test-", dir=Path.cwd()))
    store = _ProtectedStore(root, os.geteuid() + 1)
    first = _OutageJournal(store)
    try:
        yield store, first
    finally:
        first.close()
        store.close()
        shutil.rmtree(root)


def test_exact_intent_restart_reconciliation_and_one_use(journal):
    store, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    first.record_capsule(p, (TARGET,), 3)
    assert first.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256) == "disable_stop"
    second = _OutageJournal(store)
    try:
        with pytest.raises(InvalidAction, match="forward-hold"):
            second.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
        with pytest.raises(InvalidAction, match="reconciliation-hold"):
            second.complete(p, (TARGET,), p.outage.transitions[0].before_sha256)
        for ordinal, step in enumerate(p.outage.transitions):
            if ordinal:
                assert second.issue(p, (TARGET,), 4, step.before_sha256) == step.step
            second.complete(p, (TARGET,), step.after_sha256)
        with pytest.raises(InvalidAction, match="forward-hold"):
            second.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
        assert second.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 8
    finally:
        second.close()


def test_cancel_unknown_keeps_target_and_accepts_retained_completion(journal):
    store, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    first.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
    c = command(p, expected_observation_sha256=p.outage.transitions[0].after_sha256)
    first.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
    with pytest.raises(InvalidAction, match="one-use"):
        first.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
    second = _OutageJournal(store)
    try:
        # Durable acknowledgement remains possible after cancellation; it grants
        # neither another forward step nor a false terminal state.
        second.complete(p, (TARGET,), p.outage.transitions[0].after_sha256)
        assert second.db.execute("SELECT phase,ordinal FROM jobs").fetchone() == ("recovery-hold", 1)
        with pytest.raises(InvalidAction, match="forward-hold"):
            second.issue(p, (TARGET,), 5, p.outage.transitions[1].before_sha256)
        r = msgspec.structs.replace(p.release, execution_id="other", approval_id="defg", nonce="nonce2")
        other = msgspec.structs.replace(p, release=r, outage=msgspec.structs.replace(
            p.outage, execution_id=r.execution_id, nonce=r.nonce,
            release_sha256=digest(r),
        ))
        with pytest.raises(InvalidAction, match="target-conflict"):
            second.record_capsule(other, (TARGET,), 3)
    finally:
        second.close()


def test_busy_wrong_observation_and_expiry_have_no_effect(journal):
    store, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    with pytest.raises(InvalidAction, match="unexpected-before"):
        first.issue(p, (TARGET,), 4, "a" * 64)
    with pytest.raises(InvalidAction, match="forward-window"):
        first.issue(p, (TARGET,), 90, p.outage.transitions[0].before_sha256)
    second = _OutageJournal(store)
    try:
        second.db.execute("BEGIN IMMEDIATE")
        with pytest.raises(InvalidAction, match="journal-unavailable"):
            first.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
        second.db.rollback()
        assert first.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert first.db.execute("SELECT phase FROM jobs").fetchone()[0] == "ready"
    finally:
        second.close()


def another(p):
    r = msgspec.structs.replace(p.release, execution_id="other", approval_id="defg", nonce="nonce2")
    return msgspec.structs.replace(p, release=r, outage=msgspec.structs.replace(
        p.outage, execution_id=r.execution_id, nonce=r.nonce, release_sha256=digest(r),
    ))


def test_never_issued_failure_is_durable_and_only_releases_target(journal):
    store, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    receipt = first.fail_never_issued(p, (TARGET,))
    assert receipt.outcome == "failed-no-effect"
    second = _OutageJournal(store)
    try:
        assert second.db.execute("SELECT phase FROM jobs").fetchone() == ("failed-no-effect",)
        assert second.db.execute("SELECT phase FROM events").fetchone() == ("failed-no-effect",)
        with pytest.raises(InvalidAction, match="forward-hold"):
            second.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
        c = command(p)
        with pytest.raises(InvalidAction, match="recovery-terminal"):
            second.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
        second.record_capsule(another(p), (TARGET,), 3)
        reused = another(p)
        reused = msgspec.structs.replace(reused, release=msgspec.structs.replace(
            reused.release, execution_id="third", approval_id=p.release.approval_id,
        ))
        reused = msgspec.structs.replace(reused, outage=msgspec.structs.replace(
            reused.outage, execution_id="third", release_sha256=digest(reused.release),
        ))
        with pytest.raises(InvalidAction, match="one-use"):
            second.record_capsule(reused, (TARGET,), 3)
    finally:
        second.close()


@pytest.mark.parametrize("phase", ["issued", "completed", "recovery"])
def test_no_effect_proof_cannot_release_any_issued_or_recovery_target(journal, phase):
    _, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    first.issue(p, (TARGET,), 4, p.outage.transitions[0].before_sha256)
    if phase == "completed":
        first.complete(p, (TARGET,), p.outage.transitions[0].after_sha256)
    if phase == "recovery":
        c = command(p)
        first.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
    with pytest.raises(InvalidAction, match="never-issued-proof-unavailable"):
        first.fail_never_issued(p, (TARGET,))
    with pytest.raises(InvalidAction, match="target-conflict"):
        first.record_capsule(another(p), (TARGET,), 3)


def test_legacy_target_index_migrates_but_unknown_schema_refuses(journal):
    store, first = journal
    first.db.execute("DROP INDEX active_target")
    first.db.execute("CREATE UNIQUE INDEX active_target ON jobs(target) WHERE phase != 'succeeded'")
    second = _OutageJournal(store)
    second.close()
    first.db.execute("DROP INDEX active_target")
    first.db.execute("CREATE UNIQUE INDEX active_target ON jobs(target) WHERE phase != 'anything'")
    with pytest.raises(InvalidAction, match="index-schema"):
        _OutageJournal(store)


def test_cancel_before_first_intent_can_prove_no_effect_and_release(journal):
    store, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    c = command(p)
    first.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
    second = _OutageJournal(store)
    try:
        assert second.fail_never_issued(p, (TARGET,)).outcome == "failed-no-effect"
        assert second.db.execute("SELECT COUNT(*) FROM recovery").fetchone() == (1,)
        second.record_capsule(another(p), (TARGET,), 3)
    finally:
        second.close()


def test_current_schema_open_does_not_take_writer_lock(journal):
    store, first = journal
    first.db.execute("BEGIN IMMEDIATE")
    try:
        second = _OutageJournal(store)
        second.close()
    finally:
        first.db.rollback()


@pytest.mark.parametrize("mixed", [False, True])
def test_never_issued_safe_stop_retains_target_even_with_later_cancel(journal, mixed):
    _, first = journal
    p = packet()
    first.record_capsule(p, (TARGET,), 3)
    c = command(p, operation="safe-stop")
    first.request_recovery(p, (TARGET,), c, 101, c.expected_observation_sha256)
    if mixed:
        cancel = command(p, command_nonce="latercancel")
        first.request_recovery(p, (TARGET,), cancel, 102, cancel.expected_observation_sha256)
    with pytest.raises(InvalidAction, match="cancel-only"):
        first.fail_never_issued(p, (TARGET,))
    with pytest.raises(InvalidAction, match="target-conflict"):
        first.record_capsule(another(p), (TARGET,), 3)
