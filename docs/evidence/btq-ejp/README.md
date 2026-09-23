# btq-ejp validation evidence

The design was written before implementation. Ask 14f487f9 resolved signer
ownership: the configurable manager signs delegated review receipts.

Commands use an isolated test environment with pytest, pytest-subtests, PyYAML
and requirements-blockers.txt (coincurve), with PYTHONPATH=. to avoid the installed
Hermes tests package shadowing this repository. No live Marmot messages are sent
by these tests.

- `focused-tests.txt`: review, blocker, gate and signature regression tests.
- `review-gate-tests.txt`: artifact lifecycle and provider-isolation tests; the later
  focused/full runs additionally cover native-execution category rejection.
- `full-suite.txt`: final complete harness pytest regression run.
- `prior-full-suite.txt`: 495 passing tests before the final native-gate guard.
- `live/`: first real Fuel iX Opus review, with complete input and output. Verdict
  changes. Its dispatch/identity findings led to fixes and regression tests.
- `quality.txt`: ripwire quality-delta output against a698b1d. The command reports
  nonzero debt, not a clean quality gate. It attributes complexity/parameter
  changes to untouched modules sharing short symbol names (babysitter_queue,
  pr_watch, babysitter_resolution), flags dynamically invoked test methods as
  dead, and includes supervisor churn plus fixture/test duplication. Those
  measurements are retained without suppressions; they are not test failures.

Runtime Hermes home, native logs and lock/PID markers are excluded from versioned
evidence. Retained inputs contain source and acceptance criteria; credentials
are resolved from FUELIX_API_KEY and never included in review input or output.
Production enablement, manager receipt signing, live group delivery and deployment
remain manager rollout work. Signature and outbox behavior are exercised with
real BIP340 test signatures and local transports, not claimed as live delivery.

The real-model invocation used the installed Hermes Python with this checkout
first on PYTHONPATH, populated only FUELIX_API_KEY from the existing Hermes
secret profile, changed directory to the retained review directory and called
`harness.review_runner.main(directory)`. The model configuration is retained;
credential loading and runtime directories are deliberately not evidence.

Final validation: 497 passed, 2 skipped, 61 subtests passed. Focused review/gate/receipt
regression: 54 passed, 9 subtests passed. The two skips belong to optional existing
repo tests; signature tests ran with coincurve installed.

Follow-up live attempt: `followup/evidence.json` includes corrected source and
passing test logs. `followup/error.json` records JSONDecodeError from the real
model response. The process exited; no automatic approval or model replay was
performed. This is retained as a failed independent appraisal, not a pass. The
first live result's code findings (resolved model identity, stale pending jobs,
and admission holds) are addressed and covered by tests. Manager review remains
required for patch integration.
