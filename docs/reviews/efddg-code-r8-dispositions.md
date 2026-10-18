# P1 actual R8 MEDIUM author dispositions

Actual review REVISE; ten static, unverified candidates preserved verbatim in the JSON ledger and raw report. These are author repairs, not review clearance. No new review invocation or budget reset. All five live adapters remain unavailable.

## 1

FIXED: retain exact version/effect/evidence/cleanup/settlement/applied dispositions of every actual child. An authenticated exact-scope outcome and cleanup can settle a contradictory unapplied success without fabricating extra reads, erasing evidence, consuming an unused claim, or replaying the initial effect. Tests exercise both failed consumed and uncertain unconsumed parents and reject missing/mismatched acknowledgements.

## 2

CORRECTED CLAIM: the mutable clock provider is a trusted synthetic injection boundary. Journal evidence does not protect against replacement of that provider by same-process code. README now states this limit explicitly; no hostile-caller security guarantee or production clock adapter is claimed.

## 3

FIXED: one in-transaction sample is reused by canonical observation, provenance and discontinuity recovery. Advancing sampler regression asserts exactly one call for a reboot settlement.

## 4

FIXED: journal settlement requires exact canonical dispositions for every unapplied child, including outcome and evidence, even at exhaustion. Missing or altered acknowledgement refuses; final unknown-result acknowledgement and terminal resource cleanup are tested.

## 5

FIXED WITH BOUNDARY: three strings alone no longer establish pure exhaustion. Typed settled/cleaned dispositions must match the IDs. Journal independently binds those facts to actual retained child records. Pure reducers remain synthetic observation consumers, not authenticators or database readers.

## 6

FIXED: journal overwrites caller time with its sampled canonical observation and ObservedReducer passes that observation to proposal construction. A real advancing sampler requires no predicted timestamp. Legacy callback proposals remain checked against canonical state rather than silently rewriting their effects.

## 7

FIXED: preparation-failed and retire-unconsumed require independent LauncherReadback with exact approval/attempt/startup/version and positive launcher-exited/no-children observations. Missing, wrong or negative observations refuse. The readback is explicitly synthetic, not invented process inspection.

## 8

FIXED: journal admits each child only after its 1/5/30-second interval from the retained parent/previous child start. Failed preparation still consumes a slot. Full-capacity tests reject premature first, second and third insertion without journal writes.

## 9

FIXED: _recovery_clock reuses the transaction sample; it never invokes the clock provider itself.

## 10

REVALIDATED: full-capacity regressions sample every transition, separately clean three failed preparations, acknowledge an unapplied final read, settle parent and request, and verify resource release within at most 44 of 96 owner-reserved events. Neither event nor attempt limits are enlarged.
