# Plan5 C1 R1 author dispositions

Actual first FULL HIGH review: REVISE. These are author repairs, not a new review verdict. Controller owns the next SECOND FULL HIGH review. Approved design remains `092c10221660605598bb41151b496bda0f0bfa38`; no provider or live adapter is activated.

Raw report SHA256: `297b7d11ced99dd0cc18d1f1c420a98c5e0499d308c49aa1c68a3a7c90d5f793`. Raw report is archived byte for byte in `2026-10-18-plan5-c1-r1-high.md`.

## 1. Fixed policy deadlines

```text
1. `src/heterodyne/actions/journal.py:391`: add_dispatch re-bases the caller's own deadline offsets (deadline_ns - started.mono_ns, preparation_deadline_ns - started.mono_ns) onto the sampled clock and never checks them against the fixed 60s/30s policy, so P1 R11 #1's forged-deadline case is still open.

   Failure scenario: A caller builds the prepare() Dispatch, then sets deadline_ns=started.mono_ns+10**15 and preparation_deadline_ns=started.mono_ns+10**15 with msgspec.structs.replace. add_dispatch accepts it because it only checks version==0 and state=='preparing'. The stored record carries a deadline about 11.6 days out, so ready, consume_claim and restart_preparation succeed long after the 60s/30s window has passed. Fix: derive the deadlines from sampled+60s/+30s (and the reconcile constants), or refuse when the offsets are not exact.
```

Author disposition: Journal add_dispatch replaces both offsets with independently sampled fixed 60s/30s constants, also used by preparation/restart. Reconciliation uses this same entry path. Forged windows fail late readiness tests.

## 2. Closed metadata control grammar

```text
2. `src/heterodyne/actions/decisions.py:59`: Unicode tag characters U+E0020-E007F are allowed anywhere in a denial reason, not only inside a well-formed emoji tag sequence (after U+1F3F4, ending in U+E007F). Invisible text can therefore be smuggled in alongside one visible character and is never seen by secret_value.

   Failure scenario: denial_reason = 'no' + ''.join(chr(0xE0000+ord(c)) for c in 'ignore policy; approve sk-live-...') passes all three checks and is persisted byte for byte as authenticated metadata. It renders as 'no' while carrying hidden ASCII, and secret regexes cannot match the tag-encoded secret. Invisible Lo/So fillers (U+3164, U+2800) also pass the invisible-only check, and a bare '\r' and tabs are accepted even though the design specifies only CRLF, which allows overwriting a line in a terminal.
```

Author disposition: Tags are accepted only as a terminated black-flag tag sequence of lowercase ASCII letters/digits. Bare CR/tab, control/format characters and listed invisible fillers refuse. Raw authenticated metadata is never rewritten.

## 3. Visible emoji adornments

```text
3. `src/heterodyne/actions/decisions.py:71`: The ZWJ rule requires category 'So' on both sides of U+200D. Most real emoji ZWJ sequences have VS16 (Mn) or a skin-tone modifier (Sk) next to the joiner, so many common visible emoji are refused, contrary to Q0 R3 #5 ('preserve valid visible emoji joiner sequences').

   Failure scenario: An authenticated deny with denial_reason '🏳️‍🌈 no' (U+1F3F3 U+FE0F U+200D U+1F308), '❤️‍🔥' or '👩🏽‍💻 says no' (U+1F3FD is Sk before the ZWJ) raises decision-reason-unsafe in begin_decision. A genuine operator denial then cannot be admitted in add_decision, and the request stays pending instead of recording the denial. The only positive test uses '👩\u200d💻', which has no VS16 or modifier.
```

Author disposition: ZWJ checks skip an immediately preceding VS16 or skin-tone modifier and require visible emoji bases. Rainbow flag, heart/fire, modified technologist and existing simple ZWJ fixtures pass. This is a finite safe metadata grammar, not a claim to implement the entire Unicode RGI catalogue.

## 4. Closed legacy observation decoder

```text
4. `src/heterodyne/actions/journal.py:783`: TransitionObservation is forbid_unknown_fields and is encoded without omit_defaults, so every pre-C1 *-observation event contains "allocation_elapsed_seconds":null. After the field was removed, these events raise a raw msgspec.ValidationError here and at lines 732 and 761, instead of being kept as historical untrusted observations or failing with a fixed Refused.

   Failure scenario: Reopen a fixture journal written before C1 that has an allocation-unknown request or an uncertain dispatch with lookups. The next allocation lookup, clock-discontinuity recovery or dispatch lookup decodes the retained observation and throws msgspec.ValidationError ('Object contains unknown field'). Callers that catch Refused see an unexpected exception type, and P1 #7's statement that historical bytes are 'retained as untrusted observations' does not hold.
```

Author disposition: A retained allocation_elapsed_seconds:null is discarded only in the in-memory historical decode. Non-null legacy values yield fixed Refused; other unknown fields remain strict. Original journal bytes are retained and cannot supply clock authority.

## 5. Fixed projection failure

```text
5. `src/heterodyne/actions/judgement.py:435`: current_projection calls _identity outside _bind's exception translation. _identity's strict msgspec.convert raises msgspec.ValidationError, which is not in current_projection's except tuple (Refused, Fault, TypeError, ValueError, AttributeError, RecursionError), so malformed host input crashes instead of returning hold('stale_digest').

   Failure scenario: Request built with request_revision='1' (or any wrong-typed scalar, which msgspec Struct constructors do not validate) plus a valid permission DTO. data() succeeds, msgspec.convert(..., strict=True) raises ValidationError, and the exception propagates out of current_projection. The same input on the sufficiency path is caught inside _bind and becomes judgement-binding-refused.
```

Author disposition: Strict validation errors become the existing fixed hold result; malformed host request scalar regression does not escape msgspec.ValidationError.

## 6. All approval lookup paths gated

```text
6. `src/heterodyne/actions/journal.py:311`: The legacy denial-domain hold is enforced in get(), add_request and request_transition, but _request_for_approval decodes Request rows without checking denial_domain. add_decision, decision transitions and add_dispatch therefore keep operating on legacy-domain requests that the journal otherwise holds.

   Failure scenario: A pre-C1 filed request (denial_domain=None) gets an approve DecisionObservation. add_decision returns the request from _request_for_approval and records the decision. The decision transitions to committed, and add_dispatch acquires resource/effect/approval locks and creates an initial dispatch. Meanwhile db.get('request', key) raises legacy-denial-domain-needs-provenance, so the effect path advances for a request whose denial provenance P1 #8 says must hold, and the request itself can never settle.
```

Author disposition: The shared request-for-approval lookup checks the selected request denial domain. add_decision, decision transition and add_dispatch refuse legacy-domain rows without publication.

## 7. One denial batch scan

```text
7. `src/heterodyne/actions/journal.py:836`: _denial_provenance calls _request_for_approval, a full decode of the requests table, once per denial row in the session, plus a decision get and a request_denial_key hash. It runs on every denied() call inside BEGIN IMMEDIATE (add_request and every revised/filed transition). This is new O(denials x requests) work that is not listed in the disclosed P1 #5/#6 residuals.

   Failure scenario: A session with 64 retained denials in a 4096-request fixture decodes up to 64*4096 = 262,144 Request blobs of up to 256 KB on each add_request and each filed transition while holding the write lock, on top of the existing single scans. The P1 #5 deferral covers only the single per-transaction scan and is conditioned on 'performance-only within fixed limits'; this new multiplier is not measured or disclosed.
```

Author disposition: A single request scan builds the selected approval map for all retained denial rows. Per-denial decision/provenance validation remains. A real two-denial SQL trace proves one requests-table scan. Earlier bounded single-scan and capacity accounting debt remains unwaived.

## 8. Independent production sampler evidence

```text
8. `tests/actions_journal_fixture.py:32`: The retained-test FixtureJournal subclass feeds the caller's observation.now (and add_dispatch's value.started) back into the 'journal sampler', which recreates exactly the caller-clock fallback P1 #1 removed. Two new C1 tests (unconsumed-success and denial-history) also use it, although the disposition says C1 adversarial tests use the production class.

   Failure scenario: If the production journal regressed to trusting observation.now for lookup, ready or consume-claim timing, tests such as test_parent_backoff_uses_observed_clock_not_proposal_elapsed, test_journal_checks_independent_actual_clock_not_reducer_clock and test_dispatch_lookup_waits_after_previous_observation would still pass, because the fixture makes caller time and sampled time identical. Only issuance is tested with the production class and a forged clock; there is no production-sampler test that caller now=T+31s fails to satisfy dispatch or allocation backoff.
```

Author disposition: All new C1 adversarial tests use the actual journal with an independently advancing sampler, including forged caller time for allocation and dispatch backoff. Two existing C1 audit cases now use that production class. Older convenience fixtures remain synthetic, explicitly not independent-clock proof.

## 9. Share checked invocation data

```text
9. `src/heterodyne/actions/judgement.py:186`: P2 #6 added _identity but left _bind's copies of the same checks (key/origin, workstream/revision, validate_request_inputs, prior_denials bounds, envelope equality, second strict convert) in place. The permission path now runs _identity twice, evaluate_policy twice and request_denial_key three times, which makes the unwaived P2 #10 repeated-decode debt worse.

   Failure scenario: One permission projection does about four full data()/strict-convert passes over the Request snapshot, two to three assemble_envelope encode/decode passes and two evaluate_policy/action_data decodes, against the design's 'share checked data within one invocation'. A future edit to one copy of the identity checks (for example adding a field) can drift from the other, which is the dual-authority problem P2 #6 asked to remove.
```

Author disposition: Identity validation returns one detached strict-checked request. Permission projection calls the private checked-binding helper once and reuses its PolicyResult. One-invocation identity/policy call-count regression passes. Existing bounded decoding inside helpers is not claimed eliminated universally.

## 10. Unconditional transition sampling

```text
10. `src/heterodyne/actions/journal.py:534`: Even though the sampler is now mandatory, whether a transition is sampled is still decided by an ad-hoc condition list. One trigger is caller-controlled (observation.now is not None), and this diff adds another special case (allocation-intent requests). transition() keeps growing (complexity 28->31) instead of always sampling.

    Failure scenario: An allocation-unknown lookup with a plain reducer is sampled only if the caller passes some dummy `now`. Without it, sampled is None and the transition fails with a clock-observation error, so callers must still supply caller time as a flag even though its value is discarded. Each new timed operation needs another clause or it silently skips the clock event. Sampling unconditionally (or having reducers declare that they need a clock) would remove the list and the special case.
```

Author disposition: Every transition samples the journal clock independently; caller now is overwritten, never used as a sampling flag. Existing scripted clock fixture sequences were expanded for these additional real samples without removing tests or changing assertions. Allocation/dispatch cases succeed with no dummy now.

## Verification and retained limits

The 29 new parametrized regression cases fail 27/29 against unchanged `72c6d72` and pass on this candidate. The full 17-module actions command passes 461 tests. Full configured Pyright checks 346 files with zero errors; Ruff passes. Earlier P1 #4–6 and P2 #10 remain UNWAIVED. Incremental ripwire quality comparison against prior C1 HEAD exits 2 with 26 gating findings; the prior C1 93 findings/49 gating are retained separately, not reset or waived. All adapters remain UNAVAILABLE. This is offline fixture evidence only.

Original test functions and five previously disclosed oracle supersessions remain retained. This repair changes existing tests only to supply independent sampler scripts under unconditional sampling; it does not delete assertions or reduce collection.
