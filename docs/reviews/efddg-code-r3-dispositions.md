# P1 third-review repair dispositions

Actual third MEDIUM result: **REVISE**, nine findings. This is an author repair, not reviewer clearance. Two previous HIGH rounds and this MEDIUM remain consumed. The operator-authorized critical-repair follow-up has not run.

The complete report is retained byte-for-byte in `efddg-code-r3-medium.md`; the JSON ledger retains each finding verbatim in original order.

1. Fixed. add_decision re-runs begin_decision against the unique durable request and requires exact resulting Decision equality. The durable phase, digest, revision and supplied event authority flags are revalidated. add_dispatch separately corroborates the same durable filed request. New insertion-negative tests cover each authority flag, digest/revision mismatch and an unfiled request.

2. Fixed. uncertain refuses preparing/held; successful reconciliation requires a consumed claim and an unsettled initial dispatch. Preparing and forged-unconsumed success cases refuse. Existing ambiguous ready-claim readbacks remain allowed, but cannot manufacture successful effect settlement.

3. Fixed. completed can close request protocol bookkeeping for an exact settled failed initial dispatch with operator recovery provenance and cleanup. The dispatch retains failed outcome, its original row and its non-replay restriction. Durable capacity/resource release and subsequent initial-dispatch refusal are tested.

4. Fixed. the durable request validator always loads any stored decision, including cancellation proposals. A committed deny requires denied settlement; that settlement and session denial history remain atomic. Cancellation followed by resubmission no longer bypasses the deny.

5. Fixed within the approved pure-port boundary. TransitionObservation carries independently supplied clock/allocation-elapsed observations. Journal validation no longer derives those observations from old.started, proposed deadlines or fixed due thresholds. Claim expiry/deadline and readback/allocation backoffs run against these explicit observations. Missing, expired or premature observations refuse. This package does not authenticate clocks; future admitted adapters must supply current trusted observations.

6. Fixed. record_denial loads and compares the exact durable Request and Decision within its transaction before deriving session denial history. A forged caller request for a different session/intent refuses.

7. Fixed. each parent lookup consumes an exact settled read-only child row, matching parent attempt, approval, revision, digest, target, resource, evidence and success outcome. The parent retains consumed child attempt IDs atomically with its counter. No child, unsettled child, mismatched outcome/evidence and reused child refuse; the next child also requires the preceding parent observation.

8. Fixed within the approved explicit-attestation contract. allocation/abandonment/dispatch recovery now requires a separately supplied typed recovery observation. Validators never turn a proposed label/digest into all-true recovery authority. False authentication/scope/native flags refuse. Nonempty observations are retained atomically with the state event; refusal rolls both back. Synthetic operator attestations are not described as machine-verifiable authentication.

9. Fixed. removed settle_failed_dispatch; existing callers and tests use the single settle_dispatch reducer with an explicit absent-effect recovery observation.

Validation uses only synthetic offline ports and private temporary SQLite fixtures. No real queue, native writer, service, executor, network or authorization integration is enabled.

Measured quality debt is retained in the private evidence: added closed-transition checks increase complexity/verbosity, and ripwire marks explicit observation type references as dead-code candidates. These findings are not waived or described as a passing quality gate.

Final author verification: 223 named P0/P1 synthetic tests pass on Python 3.12.3.
Ruff, source-only Pyright, install-agnostic and diff checks pass. Extending Pyright
to test fixtures fails with 412 errors, versus 299 in a disposable original-source
baseline; generic journal union/literal typing debt remains unwaived. Ripwire
quality-delta exits 2 with 11 gating findings. No aggregate static/quality pass is
claimed. Exact previously recorded decision replay remains idempotent after request
settlement; a new decision insertion still requires durable request validation.
