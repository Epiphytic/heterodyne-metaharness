# Plan6C pure slice — actual first HIGH findings and author repairs

The actual FIRST FULL HIGH Claude Opus `/code-review` inspected signed source
`5b84f877e1a3cd775aab8f258c72015cba3ace2b` against original base
`2c0c5b5b775ee652d2ce32c192091aa2314e5b32`. Its unchanged exact report is
[plan6c-r1-high-actual.md](plan6c-r1-high-actual.md); original capture hashes and
actual settings are in [provenance](plan6c-r1-high-provenance.json).
The reviewer listed ten findings and said **“This is not an APPROVE token.”**
These are author dispositions, not a reviewer pass or a replacement of history.
No second review, production adapter, deployment or full Plan6C completion is claimed.

1. **Canonical route/reason/occurrence binding — repaired in pure source.** CanonicalRoute.validate requires a deciding source and its exact canonical ask ID; ask is initial/update only; askr/askd are join/reissue with exact inbound occurrence. check_native_collision refuses another payload under retained legacy native keys, including initial-to-update revision collisions.

   Oracles: `test_noncard_reason_cannot_gain_ask_route; test_repeat_and_details_native_request_must_equal_frozen_occurrence; test_same_legacy_native_key_cannot_silently_dedup_changed_revision_payload; test_nondeciding_source_cannot_take_canonical_control_card_route`. Remaining boundary: Future owner must atomically enforce native-key index and request dedup and authenticate source refresh lineage. Recovery askr epoch/request mapping is unsupported/HOLD in this partial slice.

2. **Invalid receipt shape — repaired in pure source.** Missing/wrong DTO and malformed ID field return closed invalid-receipt uncertainty. Only an immutable tuple containing exactly one matching lowercase message ID can confirm; no string splitting or implicit list conversion.

   Oracles: `test_malformed_receipt_shape_is_closed_uncertainty; test_missing_receipt_dto_is_uncertain_not_decoder_exception`. Remaining boundary: Actual production wire decoder remains deferred; it must explicitly validate/convert its closed JSON array before DTO construction.

3. **Immutable bounded uncertainty diagnosis — repaired in pure source.** Ordered distinct uncertainty kinds and append-only bounded raw prefix survive later empty/malformed/foreign receipts and final positive settlement. All correlation headers must match before IDs enter diagnosis. Maximum 4 kinds, 64 IDs, 16KiB raw; exact accepted receipt retained separately.

   Oracles: `test_uncertainty_and_correlated_raw_evidence_survive_later_empty_and_foreign_receipts; test_retained_uncertainty_stays_bounded_under_many_different_bad_results; test_foreign_account_group_key_and_text_do_not_pollute_retained_diagnosis`. Remaining boundary: The prefix intentionally stops accepting diagnosis at the cap; it is not an unbounded archive or durable owner log.

4. **Attempt target/source/trust binding — repaired in pure source.** Required AttemptBinding freezes reply target, source token, registration epoch and full copy digest. Gate binding, receipt and every retry-history record must agree. Part>0 cannot reserve without concrete root; in-place payload/source/root changes refuse.

   Oracles: `test_reply_source_and_trust_binding_are_exact_and_immutable_on_retry; test_immutable_attempt_payload_and_source_binding_cannot_be_replaced_in_place`. Remaining boundary: Synthetic structural source-token default is not authentication. Future owner supplies actual token/currentness/confirmed-root evidence and durability; no live caller or permissive missing gate/receipt binding is wired.

5. **Distinct aggregate lifecycle status — repaired in pure source.** All-cancelled reports cancelled; any exhausted part remains exhausted-held; superseded unresolved dispatch or duplicate receipts reports superseded-uncertain-held. Late confirmation remains superseded.

   Oracles: `test_all_cancelled_and_all_exhausted_have_distinct_aggregate_status; test_supersession_never_hides_unresolved_dispatch`. Remaining boundary: These are pure inspect summaries, not a production health renderer or permission to clear uncertainty.

6. **Closed held/refused vocabulary — repaired in pure source.** PolicyError.tag/code exposes the approved held/refused sets independent of diagnostic text. Source-changed, trust-denied, parent-incomplete, clock-unready, source-closed and exhausted have explicit codes. Not-yet-due is contention while preserving existing diagnostic and 0/5/30 timing.

   Oracles: `test_held_errors_expose_closed_codes_without_message_parsing; original timing/gate/clock oracles`. Remaining boundary: No new source/trust availability adapter is supplied; full native typed outcomes remain the deferred owner boundary.

7. **Dispatch-marked positive prewrite settlement — repaired in pure source.** A dispatch-marked attempt may become proven_unissued only through exact identity/binding prewrite-refusal with explicit write_not_invoked=True. Bare proof, reserved-no-dispatch after mark, mislinks and any already uncertain attempt refuse. Exact positive fact is retained.

   Oracles: `test_dispatch_mark_can_settle_only_explicit_correlated_prewrite_fact; test_positive_settlements_retain_full_correlated_fact_and_receipt; unchanged original bare-proof rejection`. Remaining boundary: No transport error allowlist, absence inference or authenticated fact producer is invented. Actual owner must prove its open-before-write boundary. Healthy-absent and proof settlement of recovered uncertainty remain unavailable in this partial slice.

8. **Frozen hash caching — repaired in pure source.** FrozenCopy computes ordered-parts, full copy, text and native-key hashes once; transitions consume cached hashes. Test patches digest to fail on any later whole-copy rehash.

   Oracles: `test_frozen_payload_hashes_are_cached_across_all_part_transitions`. Remaining boundary: Admission/replacement computes a fresh frozen cache; no timing or production performance claim.

9. **Runtime Literal single source — repaired in pure source.** Reason/Profile/Phase/ProofKind/Uncertainty/SourceKind runtime validation uses get_args; receipt message-ID validation reuses _hex; proof phase consistency no longer repeats allowed-kind sets.

   Oracles: `Existing and new closed-shape/proof/receipt oracles; full strict configured typing`. Remaining boundary: ProofKind intentionally omits healthy-absent until actual durable proof contract exists; no source authority is inferred from the Literal.

10. **Logical reminder lifetime identity — repaired in pure source.** Immutable payload request key still binds revision as d4 specifies; separate reminder_key and pure ReminderBudget/consume_reminder survive revision/canonical ask-ID refresh and refuse another consumed logical reminder.

   Oracles: `test_reminder_lifetime_budget_survives_refresh_but_payload_identity_does_not`. Remaining boundary: Future owner must authenticate logical lineage and atomically persist budget consumption with copy admission before reservation, retaining unknown consumption. No durable budget/timer/Store integration installed.

The original shared fixtures were narrowly strengthened with root authorization:
actual matching canonical route occurrences, concrete synthetic continuation root, and
exact gate/receipt bindings. Every original assertion AST is unchanged. Predecessor
bytes, hashes and exact fixture diff are retained privately; no assertion was deleted
or weakened. All other previously tracked tests are byte-identical. New negative and
positive regressions run against the exact signed predecessor before the candidate.

Configured full typing, scoped Ruff/format, exact RED/GREEN and measured quality/change
check results are recorded in the hash-bound repair freeze. Prior mutation history and
all unwaived debt remain valid historical evidence. No static quality/test-gate output
is treated as live proof, authority, or a waiver of downstream integration gates.
