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
