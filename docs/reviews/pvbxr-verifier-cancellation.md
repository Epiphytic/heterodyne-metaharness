# Verifier cancellation failure

The stable high-FD candidate stalled after 1508 admind tests in the membership-during-slow-restart test. It was interrupted after 854.14 seconds; this is not a full-suite pass. A read-only process stack and a disposable async task diagnostic showed fulfillment-verification with cancellation pending but supervised retry sleeping after Invalid.

Cause: cancellation while draining the in-flight verifier awaited its future without handling a pass failure. That failure replaced CancelledError; the supervisor caught it as a restartable failure and ran forever despite cancellation. Preserve the cancellation after draining; log only a fixed diagnostic if the drained pass fails.

An added deterministic disposable verifier regression fails on exact predecessor f8edc10 with Invalid and passes with the fix. The previously hanging original membership test passes unchanged. The first affected run passed 203 tests; then only fixed-text shutdown logging and new-test formatting changed. Configured typing analyzed 414 files with zero errors/warnings. Ruff/format/diff pass. Four quality gates remain OPEN UNWAIVED; no reset. Final-byte affected testing passed 203 tests. The full admind run is pending. Actual cross-model source review is still required. This does not resolve known descriptor lifecycle, fetch select, old-writer/schema or native deployment holds.
