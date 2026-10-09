# btq-q34bv (AU-4): deferred parking: quota and account_changed deferrals, undefer (design r3)

Base: main 25c7b2a (AU-5 merged as PR #33). Sources:
- the accounts plan, §AU-4 and the dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 D5 (deferrals), §5.2 (pickup and the wake time), §10 (the failure rows that name `account_changed`), §17 open decision 1 and D10 (the wording and the one alert), in hermes-workstreams-v2 at 82b2e4b;
- the approved AU-5 §8 plan amendment (`docs/superpowers/specs/2026-10-09-au5-usage-gate-design.md`), which moved the whole `account_changed` lifecycle acceptance bullet to AU-4, with its five named tests and the `wsctl reload` trigger;
- AU-5 as merged: the interim quota shelve (O1: `Parker._quota_shelve`, `finish_quota`, `PARKED/QUOTA` rows, `Scheduler._waiters`, the sweep's keep branch), which AU-4 replaces;
- PICKUP.md's AU-0 rules for bead writes (beads-task-queue at 72a5fa6): defer adds `v2:deferred` and no blocking edge, then the comment; each new number gets one new comment and the label stays; undefer removes only `v2:deferred`; release of a deferred bead re-checks `account_changed` and never removes `v2:deferred`.

Scope: plan 3b's defer and undefer operations, the `DEFERRED` bead state, deferral records and numbering, the quota and `account_changed` transitions, the re-gate (process start, `wsctl reload`, release), pickup and wake-time changes, sweep and recovery, and the retirement of AU-5's interim shelve and AU-3's interim `account_changed` escalation. Design only: nothing is implemented here. PoC-scoped: no usage producer (AU-7), no session-reported limit channel beyond a test entry point, no rendering or delivery of the alert or of the status wording (AU-9), no review-wait selection or storage (AU-13, by the §10 amendment).

**r3 finding fix** (post-cap, not re-reviewed by Codex; verified by the controller against park.py): the uncertainty oracle now asserts what the guard already does. `_no_receipt` escalates the op through `escalate_from`, leaving the row STUCK/UNEXPECTED_STATE with the `needs-human` handoff. The generation stays unresolved, nothing is re-dispatched, and the runtime has zero dispatches for `dispatched` and `dispatched!` and one for `launched!`. Completion backed by a receipt is tested at the `receipt` point under the completed-run oracle (§8).

**r3 changes** (review r2 on 13a6058):
1. A release of a HELD or STUCK deferred bead moves the row to DEFERRED in the same transaction that journals its `regate` step. A deadline's defer intent then takes the allowed DEFERRED → PARKING, so release adds no transition of its own (§3.5). The tests cover HELD and STUCK, each with all three gate outcomes, with `check` enforced and crashes on both sides of that transaction.
2. Reload compares a semantic `restart_view` of plain immutable values, never the settings objects (`ConfiguredAccounts` has no value equality). It compares against each lane's current settings, including what earlier reloads set (§5.2). The tests cover an unchanged reload and two consecutive supported changes through the real loader.
3. Two oracles replace the single one. The completed-run oracle covers the defer, re-gate, release and completed-launch points. A new uncertainty oracle covers the guard's `dispatched`, `dispatched!` and `launched!` points (§8). No crash point is dropped.
4. O1, O2, O3 and O5 each have a slot for Liam's dated decision (§9).

**r2 changes** (review r1 on e65f816):
1. The process-start re-gate is no longer a step of recovery, which also runs on the 5-minute reconcile and after failed jobs. It runs once per process from the daemon, after the workstream's first successful recovery (§3.4, §5.1). The no-timer test drives the periodic reconcile and the recovery-retry path.
2. The defer tail's steps have their own names (`defer.*`). Every replay route (`Scheduler.replay` for PICKUP, `Parker.replay` for PARK, RESUME and RELEASE, recovery's step 4) sends an op at a `defer.*` step to the tail first, before any undefer, resume or launch handling (§3.6). Each route has a crash test.
3. The transition table gains PARKED → PARKING and the other source states the entry points need (§2.2). The due-resume entry is tested through the public pickup path, with transition checks on.
4. The PARKED/QUOTA conversion runs in recovery before the sweep, and the sweep keeps AU-5's PARKED/QUOTA branch as a migration-only guard until no such row is left (§6). It is tested from a completed AU-5 shelve's recorded state.
5. Reload is narrow. It swaps only `accounts`, `usage` and `models`. Any other difference is refused with "restart required", so `coder_role`, repositories, btq locations and daemon timers never change live (§5).
6. A release that must re-gate keeps its RELEASE op open at a journaled `regate` step until the re-gate's outcome is written. The over-mark and the op's finish share one transaction, and a deadline runs the defer tail inside the same op (§3.5).
7. DEFERRED candidates join Source 1's existing order (by bead id) with the parked ones, and each is dispatched to undefer or resume (§4).
8. The crash oracles count runtime launches and simultaneous sessions, and check generation and receipt identity, the WIP commit's contents and the journaled SHA. Release → re-gate → pickup runs through real replay, including while another coder is running (§8).
9. O2 is a plan amendment in the AU-5 §8 form (§10): AU-13 takes the `BASE..HEAD` storage, with its schema obligation and its tests named.
10. O1 and O5 are recorded as operator decisions. O5 keeps the event's per-deferral identity for AU-9's delivery.

Items that need Liam's sign-off are marked **[Liam]** in §9: **O1** (`account_changed` is never retried on a timer), **O2** (the §10 amendment: review-wait storage moves to AU-13), **O3** (reload's supported set) and **O5** (the alert is a journal event until AU-9, a temporary notification gap).

## 0. What AU-4 changes in production, and what it doesn't

As AU-5 §0 says, `CAPABILITIES` is still empty, so every profile resolves to the implicit `default` account with `failover = "none"`, and nothing writes usage rows. The gate therefore always gives `default`, so no pickup, guard or re-gate ever defers in production. No `PARKED/QUOTA` row or `STUCK/ACCOUNT_CHANGED` row exists in production either. What does run in production:
- the process-start re-gate and the `wsctl reload` re-gate, which find no `account_changed` deferral and write nothing;
- `wsctl reload` itself, for its narrow set (§5);
- the sweep's new `v2:deferred` rules, which find no such label.

Everything else is exercised by tests, with capabilities, accounts and usage rows injected, as in AU-5.

## 1. What the sources fix (implemented exactly)

- **Defer (D5):** intent (session key, deferral number, role, profile, reason, `defer_until` or none, trust) → stop every session, confirmed (unconfirmed: the workstream holds `runtime_unavailable`, as a park does) → WIP commit → `v2:deferred` → the `wsd-defer` comment. No blocking edge. A new bead state `DEFERRED`. The bead stays claimed by wsd throughout.
- **Comment (D5):** one line, `wsd-defer session=<key> n=<number> role=<role> until=<UTC or none> reason=<reason>`, written once per deferral number.
- **Records:** `deferrals(session_key, number, bead, role, profile, reason, defer_until, trust)`, primary key (session_key, number), already created by AU-3's upgrade. The number is separate from launch generations. A session's current deferral is its highest number; lower ones are superseded and never due.
- **Undefer:** for a due quota deferral, or an `account_changed` wait that a re-gate marked over: intent → the gate → remove `v2:deferred` → launch through `Parker.launch`. The gate may instead give:
  - a deadline: the next number, the label kept, a new comment;
  - `account_changed` on a quota deferral: the transition below;
  - `account_changed` on an `account_changed` wait: nothing written.

  A bead that also carries `v2:parked` ends PARKED instead, for plan 3's parked resume.
- **Quota to `account_changed`:** one journaled transition writes the next record (reason `account_changed`, no `defer_until`), then one comment and one alert, each idempotent on the new number. It is never rechecked by a timer or the backstop. The reverse, an `account_changed` wait that re-gates to a deadline, writes one quota record.
- **Re-gate:** an `account_changed` wait is re-gated only at startup, on `wsctl reload`, and by `Parker.release` for that bead. The re-gate is journaled; the undefer runs at the next pickup.
- **Precedence:** `v2:held`, `needs-human`, a HELD row and a STUCK row each win over a deferral. A bead with both `v2:deferred` and `v2:parked` resumes only when both conditions are met.
- **Sweep and recovery:** `v2:deferred` is treated like `v2:parked`. No journal row means `journal_lost` (escalated, never relaunched). A listed session, or labels and journal that disagree, means `unexpected_state`.
- **Pickup order (§5.2):** due or over deferrals take part in Source 1 in the same order as parked beads.
- **Wake time (§5.2):** it counts the coder quota deferrals still ahead, and the current gate deadline of due coder deferrals that are still ineligible. It excludes `account_changed`, held, stuck and blocked beads.
- **Untrusted deferral (D5):** `defer_until` is the hint, or `now + unknown_backoff`, clamped to [now + min_recheck, now + untrusted_max_defer]. This is `usage.untrusted_defer_until`, which already exists.
- **Before AU-7:** only a due resume with no eligible account defers. A test entry point drives the session-reported path.
- **Reload trigger (AU-5 §8):** a `wsctl reload` control request. It re-resolves host and workstream settings with plan 1's loader. If they are valid, it swaps them in under every workstream's operation lock, then re-gates. An invalid config is refused with its path-free error, and the old settings are kept. There is no file watcher and no timer.

## 2. Data shapes

### 2.1 No schema change (O4)

AU-4 adds no table and no column, so the schema stays at version 2:
- The `deferrals` table exists. A new state `DEFERRED` is just a new value of `beads.state`, which has no CHECK.
- The `ops.kind` CHECK allows only `pickup`, `park`, `resume`, `release` and `escalate`, so AU-4 adds no op kind. Instead:
  - A standalone defer is an `OpKind.PARK` op opened directly at step `defer.recorded`.
  - An undefer is an `OpKind.RESUME` op with `"undefer": "<number>"` in its data.
  - The defer tail can run inside a PICKUP, PARK, RESUME or RELEASE op. Its steps are all named `defer.*`, and no other operation uses that prefix, so dispatch is by step name (§3.6). The data's `"defer": "<number>"` says which record the tail finishes.
- The re-gate's "over" mark is a `meta` row, `deferral_over:<session_key>:<number>` = the epoch it was marked. This follows the `clamp:` marker precedent. It is pruned when its deferral stops being current.

New journal methods (journal.py):

```python
def deferral_insert(self, row: DeferralRow) -> None             # inside the caller's transaction
def deferral_current(self, session_key: str) -> DeferralRow | None
def deferral_next(self, session_key: str) -> int                 # highest number + 1, from 1
def mark_over(self, session_key: str, number: int) -> bool       # INSERT OR IGNORE; True if new
def is_over(self, session_key: str, number: int) -> bool
def drop_over(self, session_key: str, number: int) -> None
```

The existing `deferrals_current(ws, role)` and `clamp_deadlines` are unchanged. `clamp_deadlines` already clamps the current `defer_until` with its `clamp:deferral:` marker.

### 2.2 States, reasons and labels

- `BeadState.DEFERRED = "deferred"`: claimed, `v2:deferred`, no session. The reason is `Reason.QUOTA` (with detail `quota_detail(until)`) or `Reason.ACCOUNT_CHANGED` (with detail "account changed").
  - Both reasons already exist. Their interim meanings, `PARKED/QUOTA` and `STUCK/ACCOUNT_CHANGED`, are retired (§6), and the comments on them in states.py are rewritten.
  - `ACCOUNT_REPOINTED` (the unclaimed ready bead) is unchanged.
- `ALLOWED` gains these transitions, each needed by a named path:

  | new transition | path |
  |---|---|
  | PARKED → PARKING | pickup's due-resume defer (§3.2 entry 2) and the PARKED/QUOTA conversion (§6) |
  | PARKING → DEFERRED | the end of every defer tail |
  | STARTING → DEFERRED, RESUMING → DEFERRED | a guard defer, or an undefer that re-defers or is abandoned (superseded or blocked), ends in the tail or directly |
  | DEFERRED → PARKING | a re-gate that gives a deadline opens its PARK op |
  | DEFERRED → RESUMING | undefer |
  | DEFERRED → HELD, DEFERRED → STUCK, DEFERRED → CLOSED | precedence, escalation, and a bead closed by anyone |
  | HELD → DEFERRED, STUCK → DEFERRED | the release of a bead that still carries `v2:deferred` |

  Already allowed and reused: STARTING → PARKING, RESUMING → PARKING, RESUMING → PARKED (undefer with both labels), HELD/STUCK → RESUMING. The tests reach DEFERRED only through the public entry points, with `check` enforced. No test adopts a row to get past it.
- `beads.DEFERRED = "v2:deferred"`.
- `ws_state`: AU-5's `all_quota` becomes `all_deferred`, true when every non-terminal row is DEFERRED, whatever the reason. Either `all_deferred` or an armed wake makes an otherwise idle or all-blocked workstream `DEFERRED`.

### 2.3 Trust

`trust` is `"trusted"` or `"untrusted"`:
- A deferral the gate produced (pickup, guard, re-gate, undefer) is `trusted`. The gate's deadlines come only from trusted windows and marks (D4).
- A session-reported limit (the test entry point now, AU-7 later) is `untrusted` unless its channel is a trusted source. Its `defer_until` goes through `untrusted_defer_until`.
- `account_changed` records are always `trusted` and have no `defer_until`.

## 3. Operations (park.py)

### 3.1 The defer tail

All the ways of deferring share one tail, `Parker.defer_tail(op)`. They differ only in which op contains it and how it got there. **Recording is the intent:** one transaction inserts the record (session_key, n), sets the containing op's step to `defer.recorded` with `{"defer": n}`, and puts the bead row in PARKING. A replay therefore always continues with the same number, and never allocates a second one.

| step | action | crash replay |
|---|---|---|
| `defer.recorded` | stop every listed session of the bead and confirm none is listed; `RuntimeUnavailable` → `_runtime_hold(op, STOP_UNCONFIRMED)`, op stays open | re-stops (idempotent) |
| `defer.stopped` | claim must be OURS (else ABANDONED, STUCK/CLAIM_LOST); `gitwip.wip_commit(worktree, f"defer:{key}:{n}", ...)`; the SHA goes into the step's data | `find_wip` finds the mark |
| `defer.committed` | `ensure_label(v2:deferred)` | idempotent |
| `defer.labelled` | `ensure_comment(mark=f"wsd-defer session={key} n={n} ", D5 line)` | idempotent by mark |
| finish | one transaction: drop any stale over-mark for the session, finish the op (DONE for a PARK or RELEASE op, ABANDONED for a pickup, resume or undefer, as the quota shelve did), set the final row, and for `account_changed`, `emit(ws, bead, "deferral_account_changed", ref=f"{key}:{n}")` | the alert and the finish commit together, so there is exactly one |

The final state is read from the bead, in precedence order:
- `needs-human` → STUCK/NEEDS_HUMAN;
- `v2:held` → HELD;
- otherwise DEFERRED with the record's reason.

A blocker added meanwhile doesn't change the state: blocked beads are simply not candidates (§4).

Crash points are `defer.recorded`, `defer.stopped!`, `defer.stopped`, `defer.committed!`, `defer.committed`, `defer.labelled!`, `defer.labelled`, `defer.commented!` and `defer.done`. They are prefixed with the containing op's kind (`pickup.`, `resume.`, `park.`, `release.`), as the guard's points already are.

### 3.2 Entry points to the tail

1. **`Parker.defer(bead, reason, until, trust, ref=None)`** is the standalone defer, and the test entry point for the session-reported path.
   - Under `entry()`, it opens a PARK op at `defer.recorded` with the record, then runs the tail.
   - For `untrusted`, `until` is passed through `untrusted_defer_until` before it is recorded.
   - AU-7 calls the same method from its producer.
2. **Pickup source 1, a due resume with no eligible account.** A PARKED resumable candidate whose `_gate_resume` gives a Deadline or AccountChanged is deferred through `Parker.defer`, with reason `quota` and that `until`, or reason `account_changed`.
   - It replaces AU-5's skip (O6). The row goes PARKED → PARKING → DEFERRED.
   - A gate that gives None (the guard must reconcile first) still goes to `resume`, as today.
   - The bead keeps `v2:parked`, so it carries both labels and ends PARKED when undeferred.
3. **Guard step 2** (`_pin`, in a PICKUP or RESUME op):
   - A Deadline replaces `_quota_shelve`, and AccountChanged replaces `escalate_from(op, ACCOUNT_CHANGED)`. Each one moves the containing op to `defer.recorded`, then runs the tail.
   - Step 2 precedes the generation's entry, so no session was dispatched for this op. The stop step only confirms that.
   - The bead stays claimed. That includes a new bead whose pickup lost the race between the pickup gate and step 2.
   - Step 3 (`_pin_holds` false) stays as AU-5 left it.
4. **Undefer's gate** gives a Deadline (re-defer, the next number, `quota`) or turns quota into `account_changed` (§3.3). The tail runs inside the RESUME op.
5. **The re-gate** gives a Deadline on an `account_changed` wait (§3.4):
   - From a release, the tail runs inside the RELEASE op (§3.5).
   - Otherwise it opens a PARK op at `defer.recorded` with record n+1 (`quota`), because the bead has no open op.

### 3.3 Undefer

`Parker.undefer(bead, ref=None)` is called by pickup under `entry()` from Source 1 (§4). It opens a RESUME op with `{"undefer": n}` (the current number) and puts the row in RESUMING. Its replay runs:

1. **`intent`**, which checks in this order:
   - Current number ≠ n (superseded meanwhile): ABANDONED, and the row goes back to DEFERRED.
   - `needs-human`: ABANDONED, STUCK/NEEDS_HUMAN.
   - `v2:held`: ABANDONED, HELD.
   - Open blockers and no `v2:parked`: ABANDONED, DEFERRED, still waiting.
   - Then the gate (`decide`, on the record's profile and the session's previous key):
     - An account: the step becomes `gated`.
     - A Deadline: a new record n+1 (`quota`), then the tail.
     - AccountChanged on a `quota` record: a new record n+1 (`account_changed`), then the tail, with the alert.
     - AccountChanged on an `account_changed` record (its over-mark is stale): drop the mark and finish ABANDONED, DEFERRED/ACCOUNT_CHANGED in one transaction. Nothing else is written.
2. **`gated`**: `ensure_label(v2:deferred, present=False)`, then the step becomes `unlabelled`.
3. **`unlabelled`**:
   - With `v2:parked` on the bead: finish DONE with `parked_state(shown)` (PARKED, or still waiting on blockers). Plan 3's resume takes it from there, and launches only once its blockers close.
   - Otherwise: `self.launch(op)`. `unlabelled` is exactly the step from which `replay_resume` already enters the guard. The guard gates again at step 2 and may still defer (§3.2 entry 3).

Crash points are `undefer.intent`, `undefer.gated!`, `undefer.gated`, `undefer.unlabelled!`, `undefer.unlabelled` and then the guard's resume points. The undefer never removes `v2:parked`, and never removes `v2:deferred` before the gate gave an account.

### 3.4 Re-gate of `account_changed` waits

`Parker.regate(bead, within: Op | None = None) -> Regated` takes the bead's current deferral, which must be `account_changed`, with no over-mark yet:
- **The gate gives an account:** `mark_over(key, n)`, one write. The undefer runs at the next pickup. Within a release, the mark and the release's finish share one transaction (§3.5).
- **A Deadline:** record n+1 (`quota`, that `until`), then the tail: one quota record and one comment. Within a release, the tail runs inside that RELEASE op. Otherwise it runs in a new PARK op. A crash replays the tail.
- **AccountChanged:** nothing is written: no op, no row and no event. The gate is a pure read.

**Skips.** Without a containing op, a bead that already carries an over-mark, or that has an open op, is skipped without gating, so repeated reloads write nothing. Precedence still applies: a bead with `v2:held` or `needs-human`, or with a HELD or STUCK row, isn't re-gated. Its release re-gates it (§3.5).

`Scheduler.regate_all() -> RegateCounts` re-gates every DEFERRED/ACCOUNT_CHANGED row of the workstream under `entry()`. Exactly two callers use it:
- **Process start:** `Wsd` runs it once per workstream per process (§5.1).
- **Reload:** §5.

The third trigger is `Parker.release`, for one bead (§3.5). Recovery, the reconcile, the backstop, QUOTA_WAKE and every other pickup trigger never call it.

### 3.5 Release

- **A HELD or STUCK row whose bead carries `v2:deferred`:** the RELEASE op runs as today up to `unlabelled` (removing `v2:held` and `needs-human`). One transaction then sets its step to `regate` and moves the row to DEFERRED, with the current record's reason (HELD → DEFERRED and STUCK → DEFERRED, both in §2.2). Every later outcome starts from DEFERRED, so a deadline's defer intent takes the allowed DEFERRED → PARKING, and release needs no transition of its own. If the current deferral is `quota`, `regate` finishes DONE with the row as it is: DEFERRED/QUOTA, not a parked state or a resume.
- **A DEFERRED/ACCOUNT_CHANGED row:** `Parker.release` opens a RELEASE op directly at `regate`, with nothing to unlabel. A DEFERRED/QUOTA row is `NotReleasable`: a quota wait has nothing for the operator to release.
- **The `regate` step** runs `regate(bead, within=op)` and settles in exactly one of three ways, never by launching:
  - **An account:** one transaction writes `mark_over` and finishes the op DONE with DEFERRED/ACCOUNT_CHANGED.
  - **A Deadline:** the tail runs inside this op, so the op ends with the quota record.
  - **AccountChanged:** the op finishes DONE with DEFERRED/ACCOUNT_CHANGED, and nothing else is written.

  A crash anywhere before that finish leaves the RELEASE op open at `regate` or at a `defer.*` step, and recovery's step 4 replays RELEASE ops. So the obligation is never lost, and the gate is simply asked again.
- **What stays the same:** `v2:deferred` is never removed by a release. RELEASABLE becomes {HELD, STUCK, DEFERRED}, with the quota exclusion above.

Crash points: `release.unlabelled`, `release.regate` (after the step and the DEFERRED row are committed), `release.regate.over!`, and the `release.defer.*` points.

### 3.6 Replay dispatch

Every route checks the step before anything else. The rule is the same for all four containing kinds: **a `defer.*` step goes to `Parker.defer_tail(op)`, and the legacy `quota` step goes to the conversion (§6), before any undefer, resume, release or launch handling.**

| route | today | AU-4 |
|---|---|---|
| `Scheduler.replay`, PICKUP | `replay_pickup` → `_start` → `Parker.launch` | `defer.*` → tail; `quota` → conversion; otherwise as today |
| `Parker.replay`, PARK | `replay_park` | `defer.*` → tail; otherwise `replay_park` |
| `Parker.replay`, RESUME | `replay_resume` | `defer.*` → tail; `quota` → conversion; `"undefer"` in data → `replay_undefer`; otherwise `replay_resume` |
| `Parker.replay`, RELEASE | `replay_release` | `defer.*` → tail; `regate` → §3.5; otherwise `replay_release` |
| recovery step 4 | replays PARK, RELEASE and ESCALATE ops; leaves PICKUP past its claim and RESUME to pickup | also replays any PICKUP or RESUME op at a `defer.*` or `quota` step (they never launch); the rest is unchanged |

An undefer that re-defers has both `undefer` and `defer` in its data. Its step is `defer.*` by then, so the tail wins and the undefer never resumes.

## 4. Pickup and the wake time (scheduler.py)

**Source 1** becomes one list, sorted by bead id as `_resumable` is today. It holds:
- the existing PARKED or WAITING_INPUT resumable beads;
- DEFERRED beads whose coder deferral is over.

Each DEFERRED bead must meet all of these:
- **Role:** a current coder deferral (only `deferrals_current(ws, coder_role)`, so reviewer deferrals are never read here).
- **Labels and row:** `v2:deferred`, no `v2:held`, no `needs-human`, no open op, and a row that isn't HELD or STUCK.
- **Blockers:** none open, or `v2:parked` as well.
- **Over:** `quota` with `defer_until <= now`, or `account_changed` with an over-mark.

The loop dispatches each bead by its row: DEFERRED goes to `Parker.undefer`, and the rest to the gate-then-resume path (with entry point 2's defer on a Deadline or AccountChanged). It acts on the results as today:
- STARTED or LIVE returns RESUMED.
- WAIT or UNCERTAIN returns `_stalled()`.
- Anything else goes on to the next candidate.

Pickup doesn't pre-gate DEFERRED candidates: `Parker.undefer` gates them inside its op (§3.3). A due but ineligible quota deferral therefore gets its next number in the same pickup.

The loop runs only after the existing BUSY check. So an over-marked bead is never undeferred while another coder is running: the running session's end re-triggers pickup.

**Wake time:**
- `_waiters` (PARKED/QUOTA) is removed once nothing needs it (§6).
- `_deferrals` keeps its rule (coder, `quota`, `defer_until` > now, not KEPT, not held or needs-human, not blocked). It also skips beads with an open op.
- A due, ineligible quota deferral is never left due: its undefer writes the next record with a later `until`. So "the current gate deadline of due ineligible deferrals" is that record's `defer_until`. The one exception is a due deferral the pickup didn't reach because the role was busy, and that needs no wake.
- `account_changed` rows contribute nothing to the wake time, and no pickup trigger ever gates one.

The r1 occupancy recheck before returning DEFERRED stays.

## 5. The daemon: process-start re-gate and `wsctl reload`

### 5.1 Process-start re-gate (daemon.py)

`Wsd` gains `regated: set[str]`, which lives in memory only and so is empty at every process start.

After a workstream's recovery succeeds, `Wsd` runs `regate_all` under the same lock, if the workstream isn't already in `regated`. It then adds the name. This happens inside `_locked`, in the same `work` closure that today follows `_recover`, so it covers `startup`, `pickup_one` and `reconcile_one`.

So the re-gate runs at most once per process per workstream. The first successful recovery of the process triggers it, never a later reconcile or recovery retry.

If the re-gate raises, `_locked` already clears `recovered`, and the name isn't added to `regated`. The next trigger then recovers and re-gates again. That retries a startup re-gate that never completed; it doesn't make the re-gate periodic. Each attempt's writes are idempotent (§3.4).

### 5.2 `wsctl reload` (ctl.py, daemon.py, wsctl)

**The request.** `CtlRequest.op` gains `"reload"`, which takes no `ws`. The `wsctl reload` subcommand sends it, and the reply's `data` holds the per-workstream re-gate counts: `over`, `requota` and `unchanged`.

**Validation.** `Wsd._dispatch("reload")` resolves the full settings with plan 1's loader (`settings.resolve`):
- On `ConfigError` it replies `refused` with the error's path-free message, and nothing is swapped.
- **The supported set (O3) is exactly three fields of each `WorkstreamSettings`:**
  - `accounts`: the accounts, their bindings to profiles, and failover;
  - `usage`: the host's `[usage]` gate settings;
  - `models`: each profile's model.
- Everything else must be unchanged. The comparison is semantic, between two immutable values, never `==` on the settings objects: `ConfiguredAccounts` is rebuilt by every `resolve()` and has no value equality, and it is not compared at all.
  - `restart_view(s: WsdSettings) -> RestartView` is a pure function. It builds a frozen value of plain fields only: `state_dir`, `backstop_seconds`, `reconcile_seconds`, `inbox_attempts_before_human`, `btq_checkout`, `btq_locations` as sorted pairs, and the workstreams as a tuple sorted by name.
  - Each workstream entry holds `name`, `repos` as sorted pairs, `coder_role`, `coder_profile`, `profiles` as a sorted tuple, and `limits`, a frozen dataclass with value equality.
  - It leaves out the three supported fields and the host `accounts`, which only the journal upgrade reads; a new host value is taken into `Wsd.s` and has no other effect.
  - The running side is built from `Wsd.s`, with each workstream taken from its lane's **current** `Scheduler.ws`. After every applied reload, `Wsd.s` is replaced by the new settings, so a later reload compares against what earlier reloads set.
- If the two views differ, the reload is refused with "restart required: <field names>", naming the fields that differ. Field names carry no paths.
- So a role change never strands deferrals, and nothing the queue factory or `Wsd.s` captured ever changes live.

**The swap.** One `reload` job is queued per lane. Each runs in that lane's thread under `parker.entry()`, its operation lock. It swaps `Scheduler.ws` and `Parker.ws` for the new `WorkstreamSettings` (which differs only in the three fields), then runs `regate_all`.
- The reply waits for every lane's job.
- Lanes swap in turn, not atomically across workstreams, but each workstream sees one consistent settings object.
- A pickup queued behind the job sees only the new settings.

**Out of scope.** AU-4 does not touch any systemd unit. Wiring `ExecReload=wsctl reload` belongs to the installer plan.

## 6. Retiring the interim paths (O1 of AU-5, and AU-3's escalation)

**Removed:**
- `Parker._quota_shelve`, and `finish_quota`'s ending as PARKED/QUOTA;
- `all_quota`;
- `escalate_from(op, ACCOUNT_CHANGED)` in `_pin`;
- `Scheduler._waiters` once the conversion has run (it has nothing left to count).

**An open op at step `quota`** (a pre-AU-4 crash mid-shelve, on a PICKUP or RESUME op) is converted on replay into the guard defer. One transaction inserts record n (`quota`, `until` from `op.data["until"]`) and sets the step to `defer.recorded`, then the tail runs.

**A `PARKED/QUOTA` row with no open op** is converted in recovery as step 4b: after the journal replays of step 4, and **before** step 5's sweep. It is gated fresh (O7):
- An account: the row becomes `parked_state(shown)`, and the ordinary resume takes it.
- A Deadline or AccountChanged: the tail runs in a PARK op at `defer.recorded`, with that reason. The row goes PARKED → PARKING → DEFERRED, and the bead keeps `v2:parked`, so it has both labels.

The old `until` is not reused, because a fresh gate is at least as accurate. A row that `needs-human`, a hold or a blocker would replace is left alone, so the sweep handles it as today.

**The sweep keeps AU-5's PARKED/QUOTA keep branch as a migration-only guard**, so neither a sweep in recovery nor a pickup's sweep can destroy an unconverted row (for example after a conversion that raised). The branch is commented as such. It is removed in a later cleanup, when no supported journal can still hold such a row. The cost is one dead branch in production.

**A `STUCK/ACCOUNT_CHANGED` row** (AU-3's escalation) stays STUCK until the operator releases it. The release resumes it through the guard, which now defers it as `account_changed`. No conversion is needed.

None of these rows can exist in production (§0). The conversion is there so that test journals and developer journals upgrade cleanly.

## 7. Sweep and recovery (sweep.py, recovery.py)

`_Sweep._ours` gains a `v2:deferred` branch beside the `v2:parked` one. The existing NEEDS_HUMAN → STUCK and no-row → JOURNAL_LOST checks come first and apply unchanged. JOURNAL_LOST is never relaunched.

| bead / row | result |
|---|---|
| `v2:deferred`, a session listed | UNEXPECTED_STATE "deferred, but a session is listed" |
| `v2:deferred`, row DEFERRED, no op | kept, reason and detail |
| `v2:deferred` and `v2:held`, row HELD | kept (a held deferred bead) |
| `v2:deferred`, row STUCK | kept until release (as for parked beads) |
| `v2:deferred`, an open op (any row) | left to the op's replay |
| `v2:deferred`, any other row | UNEXPECTED_STATE "deferred, but the journal says <state>" |
| row DEFERRED, no `v2:deferred`, no open op | UNEXPECTED_STATE "the journal says deferred, but the bead isn't labelled" |
| row DEFERRED, no record for the bead's session | UNEXPECTED_STATE "deferred without a deferral record" |

Recovery's order becomes:
1. Step 4: the journal replays, now including `defer.*` and `quota` steps (§3.6).
2. Step 4b: the PARKED/QUOTA conversion (§6).
3. Step 5: the sweep and the open resumes, as today.

Recovery never re-gates (§5.1).

## 8. Tests (TDD, offline, injected `deps.clock`, the existing crash-point harness)

There are two oracles. Every crash point is checked by exactly one of them, chosen by the point; none is skipped.

**The completed-run oracle** covers every defer, re-gate and release point, and every undefer and guard point outside the three uncertain ones below. The replayed end state must equal an uncrashed run of the same scenario:
- the deferral records and the current number;
- the labels;
- the `wsd-defer` comments, one per number;
- the row;
- the `deferral_account_changed` events, one per `account_changed` number.

It also checks against the fake runtime and git:
- **Launches:** the number of runtime dispatches, and the most coder sessions listed at once (never above 1).
- **Generations:** each launched generation has one launch entry and one receipt, and none is re-dispatched with a new generation for the same op.
- **WIP:** the WIP commit carries the mark for (key, n), its tree holds the files that were dirty before the defer (checked by content), and its SHA equals the one journaled in `defer.stopped`'s data.

**The uncertainty oracle** covers the guard's `dispatched`, `dispatched!` and `launched!` points (`UNRECEIPTED`), wherever an undefer, or a resume after one, reaches them. Those crashes come after the dispatch mark and before any receipt, so per ADR §11 the run can't match the completed one. On replay, step 0 finds the op's generation dispatched with no receipt. `_no_receipt` then escalates it through `escalate_from`, and the oracle asserts that existing behaviour:
- the op ends, with the row STUCK/UNEXPECTED_STATE, and the ESCALATE op with its `needs-human` handoff follows;
- the generation stays unresolved: dispatched, with no receipt and no outcome;
- nothing is re-dispatched, and no new generation appears;
- runtime dispatches: zero for `dispatched` and `dispatched!`, which come before the runtime call, and exactly one for `launched!`;
- the deferral records are unchanged from the crash.

Completion backed by a receipt is tested separately, at the guard's existing crash point after the receipt is journaled (`receipt`), under the completed-run oracle.

**Defer and undefer:**
- A hypothesis crash at every defer point, for each of the five entry points. Entry 2 runs through public `Scheduler.pickup`, from a PARKED row with `check` enforced (no `adopt`).
- A hypothesis crash at every undefer point, for each of these:
  - an account, then launch;
  - an account with both labels, ending PARKED;
  - a deadline, re-deferring with n+1;
  - quota → `account_changed`;
  - superseded;
  - held;
  - needs-human;
  - blocked.
- Replay through each route of §3.6:
  - a PICKUP op at each `defer.*` step (a guard defer of a new bead) replays to the recorded number, with zero launches;
  - the same for RESUME, PARK and RELEASE ops;
  - recovery step 4 finishes PICKUP and RESUME `defer.*` ops before any pickup runs;
  - an undefer op at a `defer.*` step never launches.
- A quota deferral never undefers before `defer_until` (clock at `until - 1`, then at `until`). A due quota deferral that gates to a deadline gets n+1, keeps the label, and adds one new comment.
- Neither reason undefers while held, stuck or blocked. With both labels, a crash at each point ends PARKED, and the bead launches only after its blockers close.
- **Order:** an eligible PARKED bead and an over DEFERRED bead compete, in both id orders. The lower id starts each time; the other doesn't run while the role is taken, and starts once it frees.
- **Claim:** the bead stays claimed throughout. A fake btq asserts that no unclaim happens in any test of this file.
- **Lost journal:** a `v2:deferred` bead with no row escalates JOURNAL_LOST, and no launch follows on later pickups.
- **Untrusted:** the `untrusted` entry point clamps `until` to [now + min_recheck, now + untrusted_max_defer].
- **Sweep:** each row of the §7 table.

**Release, through real replay routes:**
- A DEFERRED/ACCOUNT_CHANGED release, with each of the three gate outcomes:
  - an account: over-marked, and the next pickup undefers and launches once;
  - a deadline: the quota record n+1 and one comment;
  - still `account_changed`: nothing is written beyond the finished RELEASE op.
- A HELD deferred bead and a STUCK deferred bead, each over a quota record and over an `account_changed` record, each released with each of the three gate outcomes (an account, a deadline, still `account_changed`):
  - `check` is enforced throughout, with no `adopt`;
  - a crash at every release point, including either side of the `regate` transaction that moves the row to DEFERRED, the over-mark transaction and the `release.defer.*` points;
  - each replays to the completed-run end state, and `v2:deferred` stays.
- Release → re-gate (account) → pickup while another coder session is running: BUSY, no undefer and no launch. When that session ends, the next pickup undefers and launches once.
- A DEFERRED/QUOTA release is refused.

**The AU-5 §8 amendment tests**, named as approved:
1. **`test_quota_to_account_changed_writes_one_comment`:** a due quota deferral, and a resume, that gate to `account_changed` each write exactly one `wsd-defer … reason=account_changed until=none` comment, one superseding record and one alert. A crash at each step replays to one of each.
2. **`test_account_changed_has_no_timer`:** the test runs through `Wsd`, not only `Scheduler.pickup`. After the process-start re-gate, the clock passes `max_window_hours` and the following run:
   - repeated `reconcile_one` calls;
   - a recovery retry after a pickup job that raised;
   - backstop, QUOTA_WAKE and tick pickups.

   None of them re-gates the bead (its gate-call count stays at its post-start value). `wake_at` never comes from it, and the outcome is never DEFERRED because of it.
3. **`test_startup_regates_once`:** a new `Wsd` over the same journal re-gates exactly once per workstream, including when its first recovery fails and a later one succeeds. While the result is still `account_changed`, nothing is written: the ops, deferrals, meta and events counts are unchanged. A crash during the process-start re-gate replays, and the next process start re-gates again.
4. **`test_defer_and_regate_replay`:** a crash at each step of defer and of re-gate gives the same record and a single comment.
5. **`test_reload_regates_each_account_changed_once`:**
   - restored: it is marked over, and the next pickup undefers it with all checks;
   - a deadline: it becomes quota with n+1 and one comment;
   - still `account_changed`: nothing is written;
   - repeated reloads with an unchanged config write nothing.

**The reload request itself:**
- An invalid config is refused with a path-free message, and the old settings stay in use.
- Each field outside the supported set is refused with "restart required": `coder_role`, a repository, btq locations, the timers, and an added or removed workstream.
- Each supported field (`accounts`, `usage`, `models`) is applied, and a pickup queued behind the job sees it.
- Through the real loader, with config files in a temporary directory:
  - an unchanged reload is accepted, swaps nothing that matters and writes nothing;
  - two consecutive supported changes are accepted (first `usage`, then `accounts` or `models`), and the second is compared against the settings the first applied;
  - a restart-only change after an applied reload is still refused.
- `restart_view` is equal for two independent `resolve()` calls over the same files.

**Retirement:**
- A PICKUP op and a RESUME op at step `quota` each replay into a deferral.
- **The AU-5 shelve fixture:** a completed AU-5 shelve's end state, recorded once from main 25c7b2a and committed as test data. It holds the row, the abandoned op with its `until`, the labels and the comments. Recovery over it reaches each conversion outcome, and a sweep before or after conversion never changes the row.
- A guard AccountChanged ends DEFERRED, not STUCK.

**Existing AU-5 tests** that assert PARKED/QUOTA or STUCK/ACCOUNT_CHANGED are rewritten to the DEFERRED outcomes, keeping the same oracles otherwise. That includes the r1 strict "no coder session is listed" check.

## 9. Open decisions

- **O1 [Liam]: `account_changed` is never retried on a timer.** This is ADR §17 open decision 1, which the AU-5 §8 amendment assumed.
  - With it, a wait clears only on a process start, a `wsctl reload` or a release.
  - The alternative is a slow periodic re-gate, for example every `max_window_hours`. That would put back the timer that test 2 forbids.
  - Recommendation: never on a timer, as designed.
  - **Liam's decision:** _pending (date: —)_
- **O2 [Liam]: the §10 plan amendment.** The `BASE..HEAD` storage for review waits moves from AU-4 to AU-13, with AU-13's obligation and tests named there. On approval, team-lead carries the amendment into the plan.
  - **Liam's decision:** _pending (date: —)_
- **O3 [Liam]: reload's supported set** is exactly `accounts`, `usage` and `models` (§5.2). Any other difference is refused with "restart required". That includes the workstream set, `coder_role`, repositories, btq locations and the daemon timers.
  - **Liam's decision:** _pending (date: —)_
- **O4 (accepted by review r1): no new op kinds.** Defer and undefer reuse the existing kinds, and the tail is found by its `defer.*` step names (§3.6). The over-mark is a `meta` row.
- **O5 [Liam]: the alert is a journal event until AU-9.** Liam accepts a temporary notification gap: until AU-9 delivers it, no message reaches the operator, and only `wsctl status` shows the DEFERRED/ACCOUNT_CHANGED row.
  - The event keeps its per-deferral identity: kind `deferral_account_changed`, ref `<session_key>:<n>`, one row per `account_changed` number, written with the op's finish.
  - AU-9 delivers each (kind, ref) once, including events written before AU-9 existed.
  - **Liam's decision, including acceptance of the notification gap:** _pending (date: —)_
- **O6 (accepted by review r1): a due resume defers rather than being skipped**, through PARKED → PARKING.
- **O7 (accepted by review r1): the PARKED/QUOTA conversion re-gates**, before the sweep (§6).

## 10. Plan amendment (O2, needs Liam's sign-off)

The accounts plan's §AU-4 says "the review-wait form (reviewed `BASE..HEAD`) is stored here and used by AU-13". This amendment moves the storage to AU-13, so the whole review-wait form lives in the AU that can write and test it. Nothing in AU-4 creates a reviewer deferral.

**§AU-4, scope:** replace that sentence with: "Deferral records are role-generic, so a reviewer session's records fit the same table, numbering, comment and tail. AU-13 stores the reviewed `BASE..HEAD` range."

**§AU-13, scope, adds this obligation:**
- An additive schema bump creates `deferral_reviews(session_key, number, base, head)`, primary key (session_key, number), foreign to `deferrals`.
- It is written in the same transaction as the reviewer deferral record it belongs to (AU-4's `defer.recorded` step).
- Each number keeps its own range, so a superseding record gets a new row and the old one is never edited.
- Review selection reads the current number's range when the wait ends and the bead re-enters review selection (PICKUP.md: "a review wait re-enters review selection").

**§AU-13, acceptance, adds these tests:**
- `test_review_wait_records_its_range`: a reviewer deferral writes one `deferral_reviews` row with the reviewed `BASE..HEAD`, in the record's transaction. A crash at each defer point leaves either both rows or neither.
- `test_superseded_review_wait_keeps_its_range`: n+1 gets its own range, and n's is unchanged.
- `test_review_wait_reenters_selection_with_its_range`: when the wait is over, review selection sees the stored range, not the branch's current HEAD.
- `test_coder_pickup_ignores_review_waits`: coder pickup and the coder wake time never read a reviewer deferral. This also holds in AU-4, as the `deferrals_current(ws, coder_role)` filter.
