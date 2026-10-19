"""Offline contract oracles for the independent Plan6C pure slice; no owner is opened."""

from dataclasses import replace

import pytest

from heterodyne import delivery as d


def request(**changes):
    binding = d.Destination("codex", "ws", "queue", "repo", "epoch", "a" * 64, "bb")
    source = d.Source("owner", "question", "ask-origin", "logical-1", "rev-1")
    req = d.CopyRequest(source, binding, "initial", "rev-1", "renderer-1", "redactor-1", "config-1")
    return replace(req, **changes)


def copy(parts=("first", "second"), **changes):
    req = request()
    route = changes.get("route")
    if route is not None:
        req = replace(req, source=replace(req.source, canonical_id=route.ask_id))
        if route.kind != "ask":
            req = replace(
                req, reason="join" if route.kind == "askr" else "reissue", occurrence=route.request_id
            )
    return d.FrozenCopy(req, "c" * 32, "canonical-v1", parts, 200, 16384, **changes)


def state(index=0, opened=100.0, frozen=None):
    return d.PartState(frozen or copy(), "1" * 32, index, opened, reply_target="f" * 64 if index else None)


def reserved(s=None, now=100.0):
    current = s or state()
    root = None
    if current.index:
        root = d.mark_dispatch(reserved(state(frozen=current.copy)))
        root = d.accept_receipt(root, receipt(root))
    return d.reserve(current, now, d.Gates(True, True, True, current.binding, root))


def proof(s, kind: d.ProofKind = "prewrite-refusal"):
    return d.NonDispatchProof(s.attempt.identity, kind)


def receipt(s, ids=("f" * 64,), **changes):
    value = d.Receipt(
        s.attempt.identity,
        s.copy.request.destination.account,
        s.copy.request.destination.group,
        s.native_key,
        s.text_hash,
        ids,
        s.attempt.binding,
    )
    return replace(value, **changes)


class FakeOwner:
    """Disposable in-memory custody only, deliberately not a durable or authorized adapter."""

    def __init__(self):
        self.copies = {}

    def admit(self, frozen):
        old = self.copies.get(frozen.request.key)
        if old is not None:
            d.check_duplicate(old, frozen)
            return old
        self.copies[frozen.request.key] = frozen
        return frozen


def test_fake_owner_reuses_original_identity_but_conflicting_bytes_refuse():
    owner = FakeOwner()
    first = owner.admit(copy())
    assert owner.admit(replace(first, copy_id="d" * 32)) is first
    with pytest.raises(d.PolicyError, match="integrity"):
        owner.admit(replace(first, parts=("changed",)))


@pytest.mark.parametrize(
    "field,value", [("renderer", "r2"), ("redactor", "r2"), ("config", "c2"), ("reason", "mirror")]
)
def test_identity_binds_rendering_and_reason(field, value):
    assert request().key != request(**{field: value}).key


def test_identity_binds_source_revision_logical_identity_and_all_destination_fields():
    req = request()
    for field in ("producer", "kind", "canonical_id", "logical_id", "revision"):
        value = "task" if field == "kind" else "different"
        changed = replace(req.source, **{field: value})
        # Initial occurrence must equal the exact source revision.
        other = replace(req, source=changed, occurrence=changed.revision)
        assert req.key != other.key
    for field in ("agent", "workstream", "queue", "repository", "epoch", "account", "group"):
        value = "b" * 64 if field == "account" else "cc" if field == "group" else "other"
        assert req.key != replace(req, destination=replace(req.destination, **{field: value})).key


def test_copy_hash_is_order_sensitive_and_generation_free():
    original = copy()
    assert original.parts_hash != replace(original, parts=tuple(reversed(original.parts))).parts_hash
    assert original.request.key == replace(original, copy_id="d" * 32).request.key


@pytest.mark.parametrize(
    "changes",
    [
        {"parts": ()},
        {"parts": ("",)},
        {"parts": ("x" * 201,)},
        {"parts": ("é" * 100,), "frame_bytes": 199},
        {"parts": ("\ud800",)},
        {"parts": ["mutable"]},
        {"profile": "unknown"},
        {"copy_id": "C" * 32},
        {"chunk_chars": True},
    ],
)
def test_invalid_or_unbounded_frozen_copy_refuses(changes):
    with pytest.raises(d.PolicyError):
        replace(copy(), **changes)


def test_exact_profile_limits_count_final_utf8_bytes_without_rechunking():
    d.FrozenCopy(request(), "c" * 32, "canonical-v1", ("x",) * 1024, 200, 16384)
    with pytest.raises(d.PolicyError):
        d.FrozenCopy(request(), "c" * 32, "canonical-v1", ("x",) * 1025, 200, 16384)
    d.FrozenCopy(request(), "c" * 32, "canonical-v1", ("x" * 16384,) * 32, 16384, 16384)
    with pytest.raises(d.PolicyError):
        d.FrozenCopy(request(), "c" * 32, "canonical-v1", ("x" * 16384,) * 33, 16384, 16384)
    with pytest.raises(d.PolicyError):
        d.FrozenCopy(request(), "c" * 32, "canonical-v1", ("x" * 16385,), 20000, 20000)
    d.FrozenCopy(request(), "c" * 32, "display-v1", ("é" * 8192,) * 4, 8192, 16384)
    with pytest.raises(d.PolicyError):
        d.FrozenCopy(request(), "c" * 32, "display-v1", ("é" * 8192,) * 5, 8192, 16384)
    with pytest.raises(d.PolicyError):
        d.FrozenCopy(request(), "c" * 32, "display-v1", ("x",) * 21, 200, 16384)


def test_three_attempts_due_at_original_eligibility_0_5_30_and_stable_key():
    s = state()
    key = s.native_key
    identities = []
    for ordinal, now in enumerate((100.0, 105.0, 130.0), 1):
        if ordinal > 1:
            with pytest.raises(d.PolicyError, match="not-due"):
                d.reserve(s, now - 0.001, d.Gates(True, True, True))
        s = reserved(s, now)
        assert s.attempt.identity.ordinal == ordinal
        assert s.native_key == key
        identities.append(s.attempt.identity)
        s = d.prove_unissued(s, proof(s))
    assert len(set(identities)) == 3
    with pytest.raises(d.PolicyError, match="exhausted"):
        reserved(s, 999.0)
    assert d.part_status(s) == "exhausted-held"


def test_slow_previous_attempt_does_not_shift_deadline_or_overlap():
    s = reserved()
    with pytest.raises(d.PolicyError):
        reserved(s, 200.0)
    s = d.prove_unissued(s, proof(s))
    assert reserved(s, 200.0).attempt.identity.ordinal == 2


@pytest.mark.parametrize(
    "gates", [d.Gates(False, True, True), d.Gates(True, False, True), d.Gates(True, True, False)]
)
def test_current_source_trust_and_parent_each_gate_reservation(gates):
    before = state()
    with pytest.raises(d.PolicyError, match="held"):
        d.reserve(before, 100.0, gates)
    assert before.history == ()


def test_native_key_grammar_and_full_binding():
    s = state()
    assert s.native_key.startswith("d6c:") and len(s.native_key) == 52
    changed = replace(s.copy, request=replace(s.copy.request, redactor="new"))
    assert state(frozen=changed).native_key != s.native_key
    for route, expected in [
        (d.CanonicalRoute("ask", "k7m2"), "ask:k7m2:0"),
        (d.CanonicalRoute("askr", "k7m2", "b" * 64), "askr:k7m2:" + "b" * 64 + ":0"),
        (d.CanonicalRoute("askd", "k7m2", "b" * 64), "askd:k7m2:" + "b" * 64 + ":0"),
    ]:
        assert state(frozen=copy(route=route)).native_key == expected
    with pytest.raises(d.PolicyError):
        d.CanonicalRoute("askr", "k7m2", "x")


def test_dispatch_mark_uncertainty_never_replays_even_after_deadline():
    s = d.mark_dispatch(reserved())
    with pytest.raises(d.PolicyError):
        d.prove_unissued(s, proof(s))
    s = d.mark_uncertain(s, "response-lost")
    for now in (105.0, 130.0, 10000.0):
        with pytest.raises(d.PolicyError):
            reserved(s, now)
    assert d.part_status(s) == "uncertain-held"
    assert len(s.history) == 1


def test_proof_is_correlated_and_not_found_is_not_a_certificate():
    s = reserved()
    with pytest.raises(d.PolicyError):
        d.prove_unissued(s, replace(proof(s), identity=replace(s.attempt.identity, copy_id="d" * 32)))
    with pytest.raises(d.PolicyError):
        replace(proof(s), kind="not_found")
    assert s.attempt.phase == "reserved"


@pytest.mark.parametrize(
    "ids", [(), ("f" * 64, "e" * 64), ("f" * 64,) * 2, ("F" * 64,), ("x",), ("f" * 64,) * 65, ("x" * 16385,)]
)
def test_bad_receipts_are_uncertain_not_success_or_retry(ids):
    s = d.mark_dispatch(reserved())
    result = d.accept_receipt(s, receipt(s, ids))
    assert d.part_status(result) == "uncertain-held"
    with pytest.raises(d.PolicyError):
        reserved(result, 10000.0)


@pytest.mark.parametrize(
    "field,value",
    [("account", "b" * 64), ("group", "cc"), ("native_key", "different"), ("text_hash", "b" * 64)],
)
def test_receipt_must_match_exact_attempt_and_payload(field, value):
    s = d.mark_dispatch(reserved())
    assert d.part_status(d.accept_receipt(s, receipt(s, **{field: value}))) == "uncertain-held"


def test_receipt_wrong_ordinal_or_collision_is_uncertain():
    s = d.mark_dispatch(reserved())
    wrong = replace(s.attempt.identity, ordinal=2)
    assert d.part_status(d.accept_receipt(s, receipt(s, identity=wrong))) == "uncertain-held"
    claim = d.ReceiptClaim("f" * 64, "1" * 32, "d" * 32, 0)
    assert d.part_status(d.accept_receipt(s, receipt(s), (claim,))) == "uncertain-held"


def test_late_receipt_can_settle_uncertainty_but_never_resurrect_superseded_copy():
    s = d.mark_uncertain(d.mark_dispatch(reserved()), "response-lost")
    s = d.supersede(s)
    settled = d.accept_receipt(s, receipt(s))
    assert settled.attempt.phase == "confirmed"
    assert d.copy_status((settled, state(1, frozen=s.copy))) == "superseded"
    with pytest.raises(d.PolicyError):
        reserved(settled, 10000.0)


def test_cancellation_needs_never_issued_or_proven_unissued():
    assert d.cancel(state()).cancelled
    s = reserved()
    with pytest.raises(d.PolicyError):
        d.cancel(s)
    assert d.cancel(d.prove_unissued(s, proof(s))).cancelled
    s = d.mark_uncertain(d.mark_dispatch(s), "write-started")
    with pytest.raises(d.PolicyError):
        d.cancel(s)
    superseded = d.supersede(s)
    assert not superseded.cancelled
    assert superseded.history == s.history


def test_full_copy_requires_all_exact_parts_one_copy_and_distinct_receipts():
    frozen = copy()
    states = tuple(reserved(state(i, frozen=frozen)) for i in range(2))
    sent = tuple(d.mark_dispatch(s) for s in states)
    first = d.accept_receipt(sent[0], receipt(sent[0]))
    assert d.copy_status((first, states[1])) == "partial-held"
    second = d.accept_receipt(sent[1], receipt(sent[1], ("e" * 64,)))
    assert d.copy_status((first, second)) == "complete"
    assert d.copy_status((first, sent[1])) == "uncertain-held"
    with pytest.raises(d.PolicyError):
        d.copy_status((first,))
    with pytest.raises(d.PolicyError):
        foreign = replace(second, copy=replace(second.copy, copy_id="d" * 32))
        d.copy_status((first, foreign))
    repeated = d.accept_receipt(sent[1], receipt(sent[1]))
    assert d.copy_status((first, repeated)) == "uncertain-held"


@pytest.mark.parametrize("now", [float("nan"), float("inf"), True])
def test_invalid_clock_never_admits(now):
    with pytest.raises(d.PolicyError):
        reserved(now=now)


def test_bounded_raw_receipt_is_retained_without_first_id_success():
    dispatched = d.mark_dispatch(reserved())
    outcome = d.accept_receipt(dispatched, receipt(dispatched, ("x" * 20000,) * 100))
    assert outcome.attempt.phase == "uncertain"
    assert len(outcome.attempt.raw_ids) <= 64
    assert sum(len(value.encode("utf-8")) for value in outcome.attempt.raw_ids) <= 16384
    assert outcome.attempt.message_id is None


def test_terminal_receipt_is_idempotent_and_conflict_does_not_erase_evidence():
    dispatched = d.mark_dispatch(reserved())
    result = d.accept_receipt(dispatched, receipt(dispatched))
    assert d.accept_receipt(result, receipt(dispatched)) is result
    conflict = d.accept_receipt(result, receipt(dispatched, ("e" * 64,)))
    assert conflict.attempt.contradicted
    assert conflict.attempt.raw_ids == ("f" * 64, "e" * 64)
    assert d.part_status(conflict) == "uncertain-held"
    assert result.attempt.message_id == "f" * 64


def test_reminder_occurrence_names_logical_identity_not_refresh_timestamp():
    req = request()
    reminder = replace(req, reason="reminder", occurrence=req.source.logical_id)
    assert reminder.occurrence == req.source.logical_id
    with pytest.raises(d.PolicyError):
        replace(reminder, occurrence="refresh-timestamp")
    # Lifetime reminder uniqueness is a deferred durable owner budget, not this copy request key.
    assert (
        replace(reminder, source=replace(req.source, revision="refresh-2")).source.logical_id
        == reminder.occurrence
    )


def test_closed_history_cannot_forge_retry_or_move_attempt_to_another_copy():
    first = reserved()
    with pytest.raises(d.PolicyError):
        replace(first, history=(replace(first.attempt, phase="confirmed", message_id=None),))
    with pytest.raises(d.PolicyError):
        replace(
            first,
            history=(
                first.attempt,
                replace(first.attempt, identity=replace(first.attempt.identity, ordinal=2)),
            ),
        )
    with pytest.raises(d.PolicyError):
        replace(first, copy=replace(first.copy, copy_id="d" * 32))
    with pytest.raises(d.PolicyError):
        replace(first, cancelled=True)


@pytest.mark.parametrize(
    "reason,occurrence",
    [
        ("digest", "20261018"),
        ("digest", "2026-02-30"),
        ("join", "not-authenticated-inbound-id"),
        ("reissue", "ABC"),
    ],
)
def test_occurrence_has_closed_identity_shape_without_claiming_authentication(reason, occurrence):
    with pytest.raises(d.PolicyError):
        replace(request(), reason=reason, occurrence=occurrence)
