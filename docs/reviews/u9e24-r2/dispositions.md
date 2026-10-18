# u9e24 second HIGH author dispositions

Actual SECOND FULL HIGH returned REVISE. These are author repairs, not review clearance. No new review was run.

## 1

`src/heterodyne/admind/recovery.py:505`: Because `operation` is UNIQUE and register() refuses any different descriptor for an operation already on file ('immutable-operation'), an operation can never get a fresh proposal once any proposal exists, whatever state that proposal is in. Design §6 says that 'after 3 or changed scope require a fresh proposal'.

   Failure scenario: A proposal for operation X expires unconsented, or is revoked, or hits generation-cap at 3, or gets 'boot-changed' after a reboot, or its consenting operator leaves (permit refuses 'authority'/'policy-drift', and a new settle only records 'duplicate-decision'). The controller registers a new descriptor for X with a new proposal_id and expiry: previous[0] != key, so it raises Invalid('immutable-operation'). Operation X is now permanently unrecoverable through this mechanism, and only manual database edits get it back.

FIXED: retain immutable proposal history and a separate current-operation pointer. A new descriptor requires a new delivered notice and its own current authenticated consent; late old settle is history only and old permit requests refuse superseded. Reject overlap while a same-boot permit initiation window remains usable, unknown boot/clock and terminal operations. Exact legacy uniqueness schema migrates transactionally; unexpected table schema, explicit index or trigger refuses. All proposals count toward128 lifetime capacity. No effect absence, nonconsumption or new native authority is inferred from expiry or supersession; the executor still needs exact original-state/EX validation.

## 2

`src/heterodyne/admind/recovery.py:615`: An authenticated revoke only takes effect when the proposal passes live(). If the clock-rollback or expiry-window check fails, the revoke is stored as 'history-only' and the proposal stays 'consented', even though cancelling consent is always safe.

   Failure scenario: The operator consents, then an NTP step moves the wall clock back before created_at (or far enough that expiry > now+24h). The operator replies '!maintenance revoke TOKEN'. live() raises, so live=False and revoking=False; the outcome is 'history-only' and the state stays 'consented'. When the clock catches up, permit() passes live() and issues a permit despite the acknowledged revoke. §6 says a revoke after consent revokes before permit issuance.

FIXED: authenticated, exact-thread revoke has a reserved durable slot independent of live-time validity. Expiry/rollback cannot preserve consent after revoke. Current policy/authentication and immutable replay checks remain required.

## 3

`src/heterodyne/admind/daemon.py:917`: serialised_post refuses every ask post whose `bead` is bound to a source operation, including question and merge asks. approval.operation.v1 can only post approval cards, so the refusal points to an alternative that cannot do the job.

   Failure scenario: A source enrolls bead bd-12 through approval.operation.v1. A worker then runs `ask --kind question --bead bd-12 --title ...`. It gets 'source-owned bead; use approval.operation.v1', and there is no protocol mode that can post a question. Questions and merge requests tied to any source-owned bead are blocked permanently, which they were not before this change.

FIXED: source bead binding restricts approval posts only, through the existing post_approval guard under the same serialization lock. Ordinary question/merge posts retain their existing gates and capacity limits.

## 4

`src/heterodyne/admind/recovery.py:566`: A replay with the same native message ID but a different payload is only written to the refusals table. permit() never checks for recorded conflicts, so §6's 'Recorded conflicts refuse permit issuance' is not enforced.

   Failure scenario: Consent message M is recorded as 'consented'. The transport later delivers M with altered text or reply_to. message() raises 'replay-mismatch', and refusal() inserts a refusal row and posts 'refused: replay-mismatch'. A later permit() call sees state='consented' and issues a permit even though the conflict on the deciding message is recorded.

FIXED: record an immutable conflict fence for the original proposal on authenticated replay-mismatch refusal; permit checks it before any issuance or publication repair. Fence capacity is bounded by retained proposals and independent of the128 diagnostic refusal cap. Restart/full-diagnostic-cap regression retains the fence. A new proposal has distinct consent and does not clear any old conflict.

## 5

`src/heterodyne/admind/recovery.py:716`: The permit repair path calls publish_permit (file creation, links, several fsyncs) inside `with self.store.transaction()`. That holds the Store RLock and an IMMEDIATE write transaction during disk I/O, unlike the normal path, which commits first and publishes afterwards.

   Failure scenario: On a slow or contended filesystem, a repair `permit KEY` runs in the worker thread while holding store.lock. Every event-loop Store call (outbox sender, may_post, ask socket, inbound handling) blocks synchronously on the RLock for the whole fsync sequence, stalling the daemon loop. A publication exception also aborts a transaction that wrote nothing.

FIXED: both initial issue and existing-generation publication occur after the Store writer transaction commits. Repair retains the exact bytes/generation and does not extend authority/deadline.

## 6

`src/heterodyne/admind/daemon.py:795`: In on_recovery, if the handler is cancelled and the worker then finishes with Invalid, OSError or sqlite3.Error, `task.result()` raises inside the CancelledError handler. The outer except clauses turn that into a normal Reply and the cancellation is lost.

   Failure scenario: During shutdown, JsonSocketServer.close() cancels a handler whose permit worker later raises Invalid('policy-drift'). task.result() raises Invalid, which is caught by `except recovery.Invalid` and returned as Reply('refused'). The cancelled task completes normally, so callers relying on the cancellation (TaskGroup or timeout uncancel accounting) see the handler finish instead of being cancelled.

FIXED: retained worker completion is awaited even after handler cancellation; consume any worker exception without replacing CancelledError with a normal refusal/unavailable reply. Both Invalid and OSError races are covered.

## 7

`src/heterodyne/admind/daemon.py:2489`: The !maintenance interception runs before the guard verdict is acted on. Any text matching the command now skips the existing 'drop' and 'ignore' auditing, and recovery_message returns silently for messages from a foreign group or account.

   Failure scenario: A message from another group, or a stranger's '!maintenance settle <24hex>', used to produce a 'drop' audit record (sender prefix, reason). Now the foreign-group case returns from recovery_message with no audit at all, and the stranger case is logged only as 'maintenance-recovery refused authority'. Guard-level forensic evidence of unauthorised senders is lost for this command shape.

FIXED: apply original guard ignore/drop handling before recovery interception; retain stranger/foreign-group bounded forensic records without recording hostile text. Foreign-account mismatch also records a bounded drop. Exact valid commands still bypass agent intake.

## 8

`src/heterodyne/admind/recovery.py:627`: message() works out `revoking` and then repeats the same live/revoke/state condition for the branch. It also filters 'policy_sha256' out of provenance before that key exists, checks `delivery[2] != 'sent'` after SQL already filtered on status='sent', and fetches notice_text in a second query when the first rows query could return it.

   Failure scenario: This is repeated logic rather than a crash. If someone changes the revoke condition in one place but not the other, the cap-bypass reservation and the state transition drift apart, for example a revoke stored in the reserved revocations table without changing state, or the reverse.

FIXED scoped repetitions: compute revoking once, hash actual provenance directly, use SQL sent filter once, fetch notice_text in the proposal query. One authoritative revoking predicate drives reserved-slot selection and transition.

## 9

`src/heterodyne/admind/daemon.py:1501`: The withdrawn→cancelled mapping is written four times: Store.close_attempt already remaps open→cancelled for untouched/refused when the source withdrew, and close_refused, settlement() and refresh() each add their own version on top.

   Failure scenario: This adds maintenance cost. Each layer carries its own wording and conditions, so changing the withdrawn semantics (for example which settled values count as no-decision) means editing four places. If one is missed, the ask status and the reply text disagree, such as status 'cancelled' with a 'try again' PREFLIGHT_FAILED reply. Keeping only the Store-level mapping and returning the new status would avoid this.

DEFERRED OPEN UNWAIVED: withdrawn/cancelled wording duplication spans the admitted sibling approval-operation settlement and Store close protocol. Its behavior and existing oracles are preserved; refactoring these independent reply/state mappings is outside this minimal correctness repair. No claim of a consolidated single mapping.

## 10

`src/heterodyne/admind/daemon.py:1030`: post_approval repeats the bead_binding refusal that serialised_post has already made under the same ask_post_lock, with no await in between. In operations.post, `uid = peer.uid if peer.uid is not None else -1` cannot run because handle() already refuses a None uid.

    Failure scenario: Each approval post runs an extra read-only transaction and query. The duplicated checks are also two places to keep in sync if the refusal wording or binding rule changes.

FIXED: remove duplicate pre-approval bead-binding transaction from serialised_post while retaining post_approval protection, and replace unreachable uid fallback with the existing handle peer invariant. No authorization check removed from the actual approval entry point.

Quality debt and production activation prerequisites remain UNWAIVED. All earlier review history and oracles are preserved.
