"""R1 pure reducer regressions; synthetic facts never authorize a native effect."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from test_delivery_policy import copy, proof, receipt, request, reserved, state

from heterodyne import delivery as d


@pytest.mark.parametrize("ids", [None, 7, "f" * 64, {"id": "f" * 64}, ["f" * 64]])
def test_malformed_receipt_shape_is_closed_uncertainty(ids):
    sent = d.mark_dispatch(reserved())
    result = d.accept_receipt(sent, receipt(sent, ids))
    assert result.attempt == replace(
        sent.attempt, phase="uncertain", uncertainty="invalid-receipt", uncertainties=("invalid-receipt",)
    )
    assert sent.attempt.phase == "dispatch-marked"
    assert result.history[:-1] == sent.history[:-1]


def test_uncertainty_and_correlated_raw_evidence_survive_later_empty_and_foreign_receipts():
    sent = d.mark_uncertain(d.mark_dispatch(reserved()), "recovered-dispatch-mark")
    first = d.accept_receipt(sent, receipt(sent, ("e" * 64, "f" * 64)))
    second = d.accept_receipt(first, receipt(first, ()))
    foreign = receipt(second, ("a" * 64,), identity=replace(second.attempt.identity, ordinal=2))
    last = d.accept_receipt(second, foreign)
    assert last.attempt.raw_ids == first.attempt.raw_ids
    assert last.attempt.uncertainties == ("recovered-dispatch-mark", "invalid-receipt")
    assert first.attempt.raw_ids == ("e" * 64, "f" * 64)
    assert sent.attempt.raw_ids == ()


def test_retained_uncertainty_stays_bounded_under_many_different_bad_results():
    sent = d.mark_dispatch(reserved())
    for i in range(100):
        sent = d.accept_receipt(sent, receipt(sent, (str(i) * 20000, "x")))
    assert len(sent.attempt.raw_ids) <= 64
    assert sum(len(v.encode()) for v in sent.attempt.raw_ids) <= 16384
    assert len(sent.attempt.uncertainties) <= 4
    assert sent.attempt.raw_ids[0].startswith("0")


@pytest.mark.parametrize(
    "reason,occurrence", [("reminder", "logical-1"), ("digest", "2026-10-19"), ("recovery", "sealed-loss")]
)
def test_noncard_reason_cannot_gain_ask_route(reason, occurrence):
    with pytest.raises(d.PolicyError):
        replace(
            copy(),
            request=replace(
                request(reason=reason, occurrence=occurrence),
                source=replace(request().source, canonical_id="k7m2"),
            ),
            route=d.CanonicalRoute("ask", "k7m2"),
        )


def test_repeat_and_details_native_request_must_equal_frozen_occurrence():
    for kind in ("askr", "askd"):
        with pytest.raises(d.PolicyError):
            replace(
                copy(),
                request=replace(
                    request(reason="join", occurrence="a" * 64),
                    source=replace(request().source, canonical_id="k7m2"),
                ),
                route=d.CanonicalRoute(kind, "k7m2", "b" * 64),
            )


def test_ask_route_binds_actual_canonical_id_and_only_initial_update():
    with pytest.raises(d.PolicyError) as caught:
        replace(copy(), route=d.CanonicalRoute("ask", "zzzz"))
    assert (caught.value.tag, caught.value.code) == ("refused", "binding-conflict")


def test_frozen_payload_hashes_are_cached_across_all_part_transitions():
    frozen = copy()
    with patch.object(d, "_digest", side_effect=AssertionError("rehashed frozen payload")):
        first = state(frozen=frozen)
        first = d.mark_dispatch(reserved(first))
        d.accept_receipt(first, receipt(first))
        assert frozen.parts_hash
        assert frozen.native_key(1)


def test_all_cancelled_and_all_exhausted_have_distinct_aggregate_status():
    frozen = copy()
    assert d.copy_status(tuple(d.cancel(state(i, frozen=frozen)) for i in range(2))) == "cancelled"
    parts = []
    for i in range(2):
        current = state(i, frozen=frozen)
        for due in (100, 105, 130):
            current = reserved(current, due)
            current = d.prove_unissued(current, proof(current))
        parts.append(current)
    assert d.copy_status(tuple(parts)) == "exhausted-held"


def test_supersession_never_hides_unresolved_dispatch():
    frozen = copy()
    sent = d.supersede(d.mark_dispatch(reserved(state(frozen=frozen))))
    assert d.part_status(sent) == "superseded-uncertain-held"
    assert d.copy_status((sent, state(1, frozen=frozen))) == "superseded-uncertain-held"


def test_held_errors_expose_closed_codes_without_message_parsing():
    for gates, code in [
        (d.Gates(False, True, True), "source-changed"),
        (d.Gates(True, False, True), "trust-denied"),
        (d.Gates(True, True, False), "parent-incomplete"),
    ]:
        with pytest.raises(d.PolicyError) as caught:
            d.reserve(state(), 100, gates)
        assert caught.value.tag == "held"
        assert caught.value.code == code


def test_dispatch_mark_can_settle_only_explicit_correlated_prewrite_fact():
    sent = d.mark_dispatch(reserved())
    with pytest.raises(d.PolicyError):
        d.prove_unissued(sent, proof(sent))
    positive = replace(proof(sent), write_not_invoked=True, binding=sent.attempt.binding)
    settled = d.prove_unissued(sent, positive)
    assert settled.attempt.phase == "proven_unissued"
    assert reserved(settled, 105).attempt.identity.ordinal == 2
    for changed in (
        replace(positive, identity=replace(positive.identity, ordinal=2)),
        replace(positive, kind="reserved-no-dispatch"),
        replace(positive, binding=replace(sent.attempt.binding, **{"source_token": "foreign"})),
    ):
        with pytest.raises(d.PolicyError):
            d.prove_unissued(sent, changed)
    with pytest.raises(d.PolicyError):
        d.prove_unissued(d.mark_uncertain(sent, "response-lost"), positive)


def test_reply_source_and_trust_binding_are_exact_and_immutable_on_retry():
    current = reserved(state(1))
    assert current.attempt.binding.reply_target == "f" * 64
    for field, value in [
        ("reply_target", "a" * 64),
        ("source_token", "foreign"),
        ("registration_epoch", "foreign"),
    ]:
        wrong = replace(current.attempt.binding, **{field: value})
        sent = d.mark_dispatch(current)
        assert d.accept_receipt(sent, replace(receipt(sent), binding=wrong)).attempt.phase == "uncertain"
        with pytest.raises(d.PolicyError):
            d.reserve(state(1), 100, d.Gates(True, True, True, wrong))
    with pytest.raises(d.PolicyError):
        reserved(replace(state(1), reply_target=None))
    settled = d.prove_unissued(current, proof(current))
    with pytest.raises(d.PolicyError):
        replace(settled, reply_target="a" * 64)


def test_reminder_lifetime_budget_survives_refresh_but_payload_identity_does_not():
    old = request(reason="reminder", occurrence="logical-1")
    refreshed = replace(old, source=replace(old.source, revision="rev-2", canonical_id="new-ask"))
    assert old.key != refreshed.key
    assert old.reminder_key == refreshed.reminder_key
    budget = d.ReminderBudget(old.reminder_key)
    used = d.consume_reminder(budget, old)
    assert not budget.consumed and used.consumed
    with pytest.raises(d.PolicyError):
        d.consume_reminder(used, refreshed)
    with pytest.raises(d.PolicyError):
        d.consume_reminder(budget, request())


def test_immutable_attempt_payload_and_source_binding_cannot_be_replaced_in_place():
    original = reserved()
    for changed in (
        replace(original.copy, parts=("different", "second")),
        replace(original.copy, request=replace(original.copy.request, renderer="different")),
        replace(
            original.copy,
            request=replace(
                original.copy.request, source=replace(original.copy.request.source, canonical_id="different")
            ),
        ),
    ):
        with pytest.raises(d.PolicyError):
            replace(original, copy=changed)


def test_foreign_account_group_key_and_text_do_not_pollute_retained_diagnosis():
    original = d.mark_uncertain(d.mark_dispatch(reserved()), "response-lost")
    for name, value in (
        ("account", "b" * 64),
        ("group", "cc"),
        ("native_key", "other"),
        ("text_hash", "a" * 64),
    ):
        foreign = receipt(original, ("e" * 64,), **{name: value})
        result = d.accept_receipt(original, foreign)
        assert result.attempt.phase == "uncertain"
        assert result.attempt.raw_ids == ()
        assert result.attempt.uncertainties == ("response-lost", "invalid-receipt")


@pytest.mark.parametrize("missing", [None, {}, 17])
def test_missing_receipt_dto_is_uncertain_not_decoder_exception(missing):
    dispatched = d.mark_dispatch(reserved())
    result = d.accept_receipt(dispatched, missing)
    assert result.attempt.phase == "uncertain"
    assert result.attempt.uncertainties == ("invalid-receipt",)


def test_same_legacy_native_key_cannot_silently_dedup_changed_revision_payload():
    original = copy(route=d.CanonicalRoute("ask", "k7m2"))
    new_source = replace(original.request.source, revision="new-revision")
    updated = replace(
        original,
        copy_id="d" * 32,
        request=replace(original.request, source=new_source, reason="update", occurrence="new-revision"),
    )
    assert original.request.key != updated.request.key
    assert original.native_key(0) == updated.native_key(0)
    with pytest.raises(d.PolicyError, match="integrity"):
        d.check_native_collision(original, updated)
    d.check_native_collision(original, replace(original, copy_id="d" * 32))
    fresh = replace(
        updated,
        request=replace(updated.request, source=replace(updated.request.source, canonical_id="p7m2")),
        route=d.CanonicalRoute("ask", "p7m2"),
    )
    d.check_native_collision(original, fresh)


def test_positive_settlements_retain_full_correlated_fact_and_receipt():
    reserved_part = reserved()
    fact = proof(reserved_part)
    assert d.prove_unissued(reserved_part, fact).attempt.proof_fact == fact
    dispatched = d.mark_dispatch(reserved_part)
    retained = receipt(dispatched)
    confirmed = d.accept_receipt(dispatched, retained)
    assert confirmed.attempt.accepted_receipt == retained
    assert dispatched.attempt.accepted_receipt is None


def test_nondeciding_source_cannot_take_canonical_control_card_route():
    good = copy(route=d.CanonicalRoute("ask", "k7m2"))
    with pytest.raises(d.PolicyError, match="binding-conflict"):
        replace(good, request=replace(good.request, source=replace(good.request.source, kind="task")))
