"""R2 pure facts only: no native source, owner durability or dispatch authority."""

from dataclasses import replace

import pytest
from test_delivery_policy import copy, proof, receipt, request, reserved, state

from heterodyne import delivery as d


def test_conflicting_confirmed_ids_are_retained_and_aggregate_holds():
    sent = d.mark_dispatch(reserved(state(frozen=copy(parts=("one",)))))
    confirmed = d.accept_receipt(sent, receipt(sent))
    conflict = d.accept_receipt(confirmed, receipt(sent, ("e" * 64,)))
    assert conflict.attempt.message_id == "f" * 64
    assert conflict.attempt.accepted_receipt == confirmed.attempt.accepted_receipt
    assert conflict.attempt.raw_ids == ("f" * 64, "e" * 64)
    assert conflict.attempt.contradicted
    assert d.copy_status((conflict,)) == "uncertain-held"
    assert d.accept_receipt(conflict, receipt(sent)) == conflict


@pytest.mark.parametrize("retry", [False, True])
def test_late_prior_proof_receipt_stays_on_original_attempt_and_blocks_replay(retry):
    first = reserved(state(frozen=copy(parts=("one",))))
    late = receipt(first, ("e" * 64,))
    proven = d.prove_unissued(first, proof(first))
    current = d.mark_dispatch(reserved(proven, 105)) if retry else proven
    held = d.accept_receipt(current, late)
    assert held.history[0].proof_fact == proven.attempt.proof_fact
    assert held.history[0].raw_ids == ("e" * 64,)
    assert held.history[0].contradicted
    if retry:
        assert held.attempt.raw_ids == ()
        held = d.accept_receipt(held, receipt(held))
    assert d.copy_status((held,)) == "uncertain-held"
    with pytest.raises(d.PolicyError):
        reserved(held, 1000)


@pytest.mark.parametrize("kind", ["reserved-no-dispatch", "prewrite-refusal"])
def test_reconstruction_cannot_drop_dispatch_mark_with_bare_proof(kind):
    sent = d.mark_dispatch(reserved())
    fact = d.NonDispatchProof(sent.attempt.identity, kind)
    with pytest.raises(d.PolicyError):
        replace(sent, history=(replace(sent.attempt, phase="proven_unissued", proof=kind, proof_fact=fact),))


def test_continuation_requires_exact_confirmed_same_copy_root():
    part = state(1)
    with pytest.raises(d.PolicyError) as caught:
        d.reserve(part, 100, d.Gates(True, True, True, part.binding))
    assert caught.value.code == "parent-incomplete"


def test_wrong_root_never_becomes_complete():
    frozen = copy()
    root = d.mark_dispatch(reserved(state(frozen=frozen)))
    root = d.accept_receipt(root, receipt(root))
    child = replace(state(1, frozen=frozen), reply_target="a" * 64)
    with pytest.raises(d.PolicyError) as caught:
        d.reserve(child, 100, d.Gates(True, True, True, child.binding, root))
    assert caught.value.code == "parent-incomplete"


@pytest.mark.parametrize("op", [lambda s: reserved(s), lambda s: d.mark_dispatch(state())])
def test_ordinary_contention_has_explicit_code(op):
    with pytest.raises(d.PolicyError) as caught:
        op(reserved())
    assert (caught.value.tag, caught.value.code) == ("held", "contention")


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("source_token", "new", "source-changed"),
        ("registration_epoch", "new", "trust-denied"),
        ("reply_target", "a" * 64, "parent-incomplete"),
    ],
)
def test_current_gate_drift_holds_instead_of_terminal_refusal(field, value, code):
    part = state()
    with pytest.raises(d.PolicyError) as caught:
        d.reserve(part, 100, d.Gates(True, True, True, replace(part.binding, **{field: value})))
    assert (caught.value.tag, caught.value.code) == ("held", code)


def test_unsealed_recovery_is_unsupported():
    with pytest.raises(d.PolicyError) as caught:
        request(reason="recovery", occurrence="arbitrary-reconcile-epoch")
    assert caught.value.code == "unsupported-policy"


def test_reminder_cannot_reserve_without_real_durable_owner_budget():
    reminder = replace(copy(), request=request(reason="reminder", occurrence="logical-1"))
    with pytest.raises(d.PolicyError) as caught:
        reserved(state(frozen=reminder))
    assert caught.value.code == "unsupported-policy"


def test_replace_recomputes_derived_source_token():
    old = copy()
    req = replace(old.request, source=replace(old.request.source, revision="rev-2"), occurrence="rev-2")
    new = replace(old, request=req)
    assert new.source_token != old.source_token
    assert new.source_token == d._digest(d.asdict(req.source))
    assert state(frozen=new).binding.source_token == new.source_token


def test_late_proof_contradiction_preserves_prior_cancellation_record():
    first = reserved(state(frozen=copy(parts=("one",))))
    cancelled = d.cancel(d.prove_unissued(first, proof(first)))
    held = d.accept_receipt(cancelled, receipt(first))
    assert held.cancelled
    assert held.attempt.proof_fact == cancelled.attempt.proof_fact
    assert d.part_status(held) == "uncertain-held"
    assert d.copy_status((held,)) == "uncertain-held"


def test_prior_contradiction_prevents_already_reserved_retry_dispatch():
    first = reserved()
    next_part = reserved(d.prove_unissued(first, proof(first)), 105)
    held = d.accept_receipt(next_part, receipt(first))
    with pytest.raises(d.PolicyError) as caught:
        d.mark_dispatch(held)
    assert caught.value.code == "recovery-required"
    assert held.attempt.phase == "reserved"


def test_ordinal_claim_cannot_hide_duplicate_across_prior_attempt():
    sent = d.mark_dispatch(reserved())
    claim = d.ReceiptClaim("f" * 64, sent.owner_store_uuid, sent.copy.copy_id, sent.index, 2)
    held = d.accept_receipt(sent, receipt(sent), (claim,))
    assert d.part_status(held) == "uncertain-held"
    assert held.attempt.raw_ids == ("f" * 64,)


def test_foreign_receipt_cannot_pollute_original_proof_diagnosis():
    first = reserved()
    current = d.mark_dispatch(reserved(d.prove_unissued(first, proof(first)), 105))
    foreign = receipt(first, ("e" * 64,), account="b" * 64)
    result = d.accept_receipt(current, foreign)
    assert result.history[0] == current.history[0]
    assert result.attempt.raw_ids == ()
    assert result.attempt.uncertainty == "invalid-receipt"


def test_explicit_source_override_survives_copy_replace_without_claiming_authentication():
    snapshot = "synthetic-owner-snapshot"
    old = replace(copy(), source_token_override=snapshot)
    assert old.source_token == snapshot
    assert replace(old, copy_id="d" * 32).source_token == old.source_token
