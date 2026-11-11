# P3 first HIGH author dispositions

The actual first full HIGH review was REVISE. These repairs have not received a second review. Reciprocal signed-pair acceptance and all live adapters remain unavailable. Quality debt is unwaived.

## 1

`src/heterodyne/actions/filing.py:335`: prepare() checks only one pending command, using fetchone() with no ORDER BY. Once any non-withdraw command is uncertain, the scan returns that oldest row, so every later withdraw counts as 'recoverable', even while an earlier withdraw is still prepared, issuing or uncertain.

   Failure scenario: A park command is left uncertain. prepare_cleanup creates W1 and execute(W1) loses its acknowledgement, so W1 becomes uncertain. prepare_cleanup again: the pending query returns the park row first (rowid order, no index on k), so recoverable=True and W2 is inserted. issue(W2) passes because the request is unchanged until a withdraw is observed, so ports.invoke issues a second withdrawal while W1's effect is still unknown. That breaks 'issuing/unknown never replay'. The check should refuse if any pending withdraw exists (EXISTS ... operation='withdraw'), not inspect a single arbitrary row.

FIXED: prepare reads every pending command; withdrawal is recoverable only when every pending command is a non-withdraw uncertain command. A second unresolved withdrawal cannot be hidden by an earlier command.

## 2

`src/heterodyne/actions/filing.py:809`: FilingJournal.assess prepares the provider command before j.assess. j.assess can return without ever calling provider.judge (lint not Passed, invalid egress, permission request, or an invalid boot label), or DurableProvider.judge can refuse before issue (stale Current, CAS failure). Either way the command stays 'prepared' forever: there is no retry API, the pending check blocks every later prepare, and withdraw only treats 'uncertain' as recoverable.

   Failure scenario: An ask is pinned whose SufficiencyRequest lint is not Passed. That is the normal case that should project to NeedsGrooming. j.bind succeeds (it does not check lint), prepare('provider') commits, j.assess returns Receipt(...,'unavailable') with no attempt, and assess raises Held('provider-outcome-unknown'). From then on: prepare(...) gives Held('command-pending-held'), prepare_cleanup gives Held('command-pending-held') because pending[2]=='prepared', and execute(cmd) would send the provider command to ports.invoke. The request and its 161-slot / 2-per-bead reservation leak permanently. The lint/egress/permission checks should run before prepare, or there should be a prepared-to-refused-unissued abandonment path.

FIXED: a genuine P2 no-call Receipt transitions its prepared provider intent to refused-unissued, retains the Receipt before projection, and is projected through the original P2 binding. Generic provider issuance is refused. Explicit local prepared abandonment handles other pre-effect holds.

## 3

`src/heterodyne/actions/filing.py:346`: Withdraw recovery accepts only a pending state of 'uncertain'. A command left 'issuing' by process death is never normalized: open() does not convert it, and reconcile() does not call mark_unknown when ports.read raises. Design §3.2 says such a command is uncertain on restart, but here it blocks abandonment for good.

   Failure scenario: The process dies inside ports.invoke for an allocate, so mark_unknown never runs and the state stays 'issuing'. After restart, reconcile is called at 1, 5 and 30 seconds and all three ports.read calls raise OSError, so observe is never called and the state is still 'issuing'. allocation_lookups==3, but prepare_cleanup(Cleanup('reconciled-abandoned', abandonment=...)) gets pending=('issuing') with recoverable=False, giving Held('command-pending-held'). A further reconcile gives Held('lookup-deadline-held'). Neither path can reach the authenticated AbandonmentRecovery, so the reservation is held forever.

FIXED: exact-schema reopen durably normalizes issuing commands to uncertain. Reconcile marks unknown on read/observation exceptions. Three failed spaced allocation reads retain the reducer allowance and allow the existing authenticated abandonment path; no absence or retry is inferred.

## 4

`src/heterodyne/actions/filing.py:348`: There is no way to abandon a command that was prepared but never issued. Any prepared command that can never pass issue() wedges the request against withdrawal, even though nothing was issued.

   Failure scenario: Example 1: test_arbitrary_groom_payload_cannot_launch_session prepares a 'groom' with payload b'{}'. issue raises Held('groom-owner-required'), the row stays 'prepared', and every later prepare_cleanup raises Held('command-pending-held'). Example 2: a park command whose Current observation never becomes authority_current or repository_current again. That loss of authority is exactly when withdrawal is needed, yet withdrawal is refused permanently. The fix is a single 'prepared -> refused-unissued (abandoned)' transition, rather than special-casing 'uncertain' in the withdraw path.

FIXED: abandon_prepared performs a current-request/prepared CAS with a terminal audit event and no port call. It never clears an issuing/uncertain command. A prepared allocation uses the source-owned no-call cancellation reducer.

## 5

`src/heterodyne/actions/filing.py:426`: For allocate, issue() commits r.issue_allocation (phase allocation-unknown, create_issued=True) before ports.invoke. If the adapter then raises BeforeIssue, the command becomes 'refused-unissued' but the request keeps the issued state. The request can no longer re-allocate, reconcile or be cancelled. Design §3.4 requires 'a proved unissued cancellation or adoption' for this case, and the path has no test.

   Failure scenario: ports.invoke raises BeforeIssue on allocate. The command is now 'refused-unissued' and the request is in allocation-unknown with allocation_lookups=0. prepare('allocate') fails the phase check. reconcile() raises Held('reconcile-state-refused'), so the three lookups can never happen. settle_request('cancelled') refuses from allocation-unknown, and 'reconciled-abandoned' needs lookups==3 or a clock discontinuity. The definitely-unissued request holds its capacity until there is a clock discontinuity.

FIXED: immediate typed BeforeIssue on allocation atomically stores source-owned cancel_unissued_allocation and refused-unissued command. create_issued remains true as historical fact; terminal cancellation cannot allocate again. No retrospective read can establish this proof.

## 6

`src/heterodyne/actions/filing.py:562`: Each reconcile of a non-allocate command appends a 'result' lifecycle event, including when the read outcome is 'uncertain' or 'unavailable', and there is no per-command bound. The terminal cleanup reserve is checked only in prepare(), so repeated read-only reconciliation uses up the slots kept for withdrawal.

   Failure scenario: A park command is uncertain and the port keeps returning outcome='unavailable'. Each reconcile adds one non-detail event, so after about 110 calls 'used' exceeds 124. prepare_cleanup then raises Held('terminal-lifecycle-reserved'), and once 128 is reached append_event raises request-event-capacity-held. The request can never be withdrawn. Reconcile reads should not consume lifecycle slots (or should be capped below the reserve), and the reserve should be enforced inside append_event.

FIXED: uncertain/unavailable readbacks replace a bounded diagnostic rather than appending lifecycle events. append_event enforces ordinary and terminal budgets independently. A boundary test fills 124 ordinary slots then completes actual P1 terminal cleanup in the reserved slots.

## 7

`src/heterodyne/actions/filing.py:782`: prepare_cleanup and the public prepare(...,'withdraw',payload) store the Cleanup payload without decoding or checking its shape. The decode and the cancellation/abandonment shape check happen only in _reduce, after ports.invoke has performed the withdrawal. Likewise, prepare('provider') followed by execute() sends a provider command through the mutation port.

   Failure scenario: Example 1: prepare_cleanup(k, DIGEST, Cleanup('denied', cancellation=CancellationRecovery(...))). issue succeeds and ports.invoke withdraws. _reduce then raises Held('cleanup-recovery-shape-refused'), mark_unknown leaves an uncertain withdraw, and because pending[1]=='withdraw' the request is not recoverable (wedged). Example 2: prepare(k,'withdraw',DIGEST,b'garbage') issues the effect, then msgspec.DecodeError escapes untranslated rather than as a Held. Validate the payload in prepare/issue, and refuse 'provider' operations in issue() outside DurableProvider.

FIXED: strict Cleanup decoding and cancellation/abandonment-phase shape checks run before preparation and issuance. Public issue/execute refuse provider; only DurableProvider may enter its private durable issue route. Native cleanup proof is still independently required after the effect.

## 8

`src/heterodyne/actions/filing.py:487`: The 1/5/30 s lookup spacing is anchored on commands.clock, which is the prepare-time sample. issue() samples 'now' but stores it only in the event, so the first lookup's 1 s spacing is measured from prepare rather than from when the create was actually issued.

   Failure scenario: prepare runs at mono=0. ports.current is slow and issue/invoke run at mono=10s, then the acknowledgement is lost. reconcile at mono=10.001s passes the check, since 10s exceeds 1s from the prepare anchor, so the first lookup is consumed milliseconds after the create was issued. The design's spaced lookup budget is used up early. Persist the issue sample as the anchor in the same transaction as 'issuing'. Existing tests miss this because prepare and issue use the same clock value.

FIXED: actual issue Clock is persisted atomically with issuing; lookup spacing starts there and advances only before each actual read. Delayed prepare-to-issue regression proves immediate lookup remains held.

## 9

`src/heterodyne/actions/filing.py:259`: append_event runs two count(*) scans per event over events.k, which has no index. reserve() runs a correlated count over all events for every active request. The approved design table (P1 R11 #6) explicitly requires capacity accounting 'without repeated per-event scans'.

   Failure scenario: With 16384 retained events and 64 active requests, every event append scans the whole events table twice. Each reserve does about 64 correlated full scans (around 1M row visits) while holding BEGIN IMMEDIATE, which blocks every other writer past the 1 s busy timeout. Keep per-request counters in the requests row, updated transactionally, or at least index events(k,kind) and commands(k,state).

FIXED: per-request event/lifecycle counters are updated in the same transaction as each append. Pending and event indexes support bounded lookups; reserve sums retained counters rather than correlated event scans. This changes the disposable exact schema; predecessor journals HOLD rather than silently migrate.

## 10

`src/heterodyne/actions/filing.py:574`: The allocate branch of _reduce works around the P1 reducer rather than reusing it. It rewinds allocation_lookups, calls observe_allocation with a constant elapsed_seconds that makes P1's due check pointless, then restores the count; for zero or multiple shells it rebuilds P1's shell union with r.changed. There is also dead code and duplicated logic elsewhere in the module.

    Failure scenario: Because elapsed_seconds is always the exact constant, P1's 'allocation-lookup-not-due' guard can never fire. The create-acknowledgement path tells P1 that 1 s elapsed when about 0 s did. P1's duplicate-ID validation is skipped in the r.changed branch. Separately: create() has an unreachable 'if ports is None' (line 197) after _capability, the pending SQL is copy-pasted in prepare and _idle, and the 32-hex nonce check is written three times (filing.prepare, judgement.assess, _sufficiency_projection). Any P1 change to these semantics would silently diverge. A P1 'lookup consumed / readback adopted' split API would be the proper fix.

FIXED material reducer divergence: P1 now owns separate consume_allocation_lookup and adopt_allocation_readback transitions. Legacy observe_allocation preserves one-version/count/due semantics. P3 persists consumption before reading and adopts through the same validated update builder, without count rewind, invented elapsed time or manual shell union. Lower-risk duplicated pending query/nonce validation remains OPEN UNWAIVED; no broad refactor was needed.
