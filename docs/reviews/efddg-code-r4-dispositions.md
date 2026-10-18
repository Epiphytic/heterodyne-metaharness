# P1 actual MEDIUM review round 4 dispositions

The actual fourth invocation returned **REVISE**. This author repair does not reset that ordinal or claim a follow-up approval. The JSON retains all ten original findings in order.

## 1

Fixed. Durable pending/issued/native/committed decisions and retained authenticated native-conflict events block grooming. Cancellation cannot discard an unresolved decision. Committing a deny records its exact durable canonical intent/session identity atomically with the decision event, before request terminal cleanup; an audit-capacity failure rolls both back. Same-intent re-admission is refused immediately, and denied settlement remains available.

## 2

Fixed. DispatchRecovery can explicitly attest a sampled clock discontinuity, exact current scope, definitive outcome and cleanup. The reducer verifies that the independent valid clock actually differs in boot/continuity; only then can exact authenticated recovery settle without the otherwise-required three scheduled reads. Recovery never grants another initial claim or resets consumed/nonces. Reboot and UTC/monotonic-gap regressions cover release of retained locks and refusal of stale, unauthenticated and continuous-clock bypasses.

## 3

Fixed within the approved synthetic-port boundary. Finishing a read-only child requires separately supplied ReconciliationReadback bound to its exact approval/revision/digest/target/attempt/parent/version. Read-only invocation completion, evidence, effect outcome, worker cleanup and effect cleanup are explicit observations, never recovered from the proposed child. Parent lookup consumes those retained child facts. These are honest synthetic attestations, not machine proof that any real invocation ran; all live adapters remain unavailable.

## 4

Fixed. DecisionReadback supplies current authority/check/native-read/native-event/heart facts separately. The journal supplies the current durable Request, and issuance/read/finalization checks its exact approval, revision, digest and admissible phase. A revoked-authority invalidation requires an explicit current check and false authority; a stale-digest invalidation requires an actually changed durable digest. Write-issued invalidation still requires authenticated conflicting native readback. No decision validator manufactures true authority/read flags.

## 5

Fixed. Issuance requires an independent valid Clock retained in the same journal transaction. Allocation lookups load that exact retained issuance observation and derive elapsed time from the newly sampled same-boot continuous clock. Caller integer and caller-supplied allocation_started do not establish elapsed time. The original 1/5/30 schedule and no-second-create rule remain unchanged. Missing historical clock observations fail closed rather than inventing a start.

## 6

Fixed. A filed request with no active decision/dispatch may cancel only with an exact separately supplied CancellationRecovery attesting authenticated current scope, native withdrawal readback and cleanup. The observation binds key, origin, version and all shells and is retained atomically. Pending/issued/committed decisions, a retained native-conflict event, or any retained dispatch refuse this path. Invalidated prewrite and unanswered filed requests have explicit tested terminal exits without assuming silence is withdrawal.

## 7

Fixed without adding a new request phase. The existing completed phase means terminal lifecycle; Request.completion_effect now explicitly records succeeded or absent from the corroborated settled initial dispatch. Legacy records default to unknown (None), never implicit success. Tests distinguish an absent effect from business success while preserving terminal capacity release.

## 8

Fixed. Unknown/absent reconciliation cannot downgrade an already absent failed parent or overwrite its original evidence/cleanup. A newly corroborated absent read is represented as failed rather than unknown. Child readback remains retained as a distinct journal event, and absence never grants replay. Parent proposals are rebuilt from the retained child outcome, including the separately attested effect cleanup.

## 9

Fixed at both pure and journal boundaries. Read-only children require uncertain/failed initial parents; held parents cannot create orphan read invocations. Existing serial-child fixtures now begin from an actual ready-to-uncertain parent, preserving the original nonce/counter checks.

## 10

Deferred noncritical performance debt. _request_for_approval retains the exact bounded scan of at most 4096 fixture Request rows under the transaction. No indexed-schema migration was added solely for this performance concern during critical integrity repair. This is not an unbounded production operation: no production journal adapter is enabled. Future admitted persistence work should profile and add a durable unique approval index with migration/rollback tests; no contention or performance pass is claimed.

All runtime observations remain explicit synthetic attestations. No live action adapter, native writer, transport, deployment or production journal is enabled. Finding 10 is deferred performance debt; quality findings remain unwaived.
