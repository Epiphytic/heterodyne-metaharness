# btq-q34bv (AU-4): deferred parking: quota and account_changed deferrals, undefer (design r1)

Base: main 25c7b2a (AU-5 merged as PR #33). Sources:
- the accounts plan, §AU-4 and the dependency graph (`docs/superpowers/plans/2026-10-05-heterodyne-accounts-and-usage-changes.md` at 66ad195);
- ADR 0001 r14 D5 (deferrals), §5.2 (pickup and the wake time), §10 (the failure rows that name `account_changed`), §17 open decision 1 and D10 (the wording and the one alert), in hermes-workstreams-v2 at 82b2e4b;
- the approved AU-5 §8 plan amendment (`docs/superpowers/specs/2026-10-09-au5-usage-gate-design.md`), which moved the whole `account_changed` lifecycle acceptance bullet to AU-4, with its five named tests and the `wsctl reload` trigger;
- AU-5 as merged: the interim quota shelve (O1: `Parker._quota_shelve`, `finish_quota`, `PARKED/QUOTA` rows, `Scheduler._waiters`, the sweep's keep branch), which AU-4 replaces;
- PICKUP.md's AU-0 rules for bead writes (beads-task-queue at 72a5fa6): defer adds `v2:deferred` and no blocking edge, then the comment; each new number gets one new comment and the label stays; undefer removes only `v2:deferred`; release of a deferred bead re-checks `account_changed` and never removes `v2:deferred`.

Scope: plan 3b's defer and undefer operations, the `DEFERRED` bead state, deferral records and numbering, the quota and `account_changed` transitions, the re-gate (startup, `wsctl reload`, release), pickup and wake-time changes, sweep and recovery, and the retirement of AU-5's interim shelve and AU-3's interim `account_changed` escalation. Design only: nothing is implemented here. PoC-scoped: no usage producer (AU-7), no session-reported limit channel beyond a test entry point, no rendering of the alert or of the status wording (AU-9), no review-wait selection (AU-13).

Items that need Liam's sign-off are marked **[Liam]** in §9: O1 (`account_changed` is never retried on a timer), O2 (the review-wait range moves to AU-13), O3 (what a reload may change) and O5 (the alert is a journal event until AU-9).

## 0. What AU-4 changes in production, and what it doesn't

As AU-5 §0 says, `CAPABILITIES` is still empty, so every profile resolves to the implicit `default` account with `failover = "none"`, and nothing writes usage rows. The gate therefore always gives `default`, so no pickup, guard or re-gate ever defers in production. No `PARKED/QUOTA` row or `STUCK/ACCOUNT_CHANGED` row exists in production either. What does run in production:
- the startup re-gate and the `wsctl reload` re-gate, which find no `account_changed` deferral and write nothing;
- `wsctl reload` itself, which re-resolves and swaps settings (useful on its own: a model or limit change no longer needs a restart);
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
- **Wake time (§5.2):** it counts the coder quota deferrals still ahead, and the current gate deadline of due coder deferrals that are still ineligible. It excludes `account_changed`, held, stuck and blocked beads.
- **Untrusted deferral (D5):** `defer_until` is the hint, or `now + unknown_backoff`, clamped to [now + min_recheck, now + untrusted_max_defer]. This is `usage.untrusted_defer_until`, which already exists.
- **Before AU-7:** only a due resume with no eligible account defers. A test entry point drives the session-reported path.
- **Reload trigger (AU-5 §8):** a `wsctl reload` control request. It re-resolves host and workstream settings with plan 1's loader. If they are valid, it swaps them in under every workstream's operation lock, then re-gates. An invalid config is refused with its path-free error, and the old settings are kept. There is no file watcher and no timer.

## 2. Data shapes

### 2.1 No schema change (O4)

AU-4 adds no table and no column, so the schema stays at version 2:
- The `deferrals` table exists. A new state `DEFERRED` is just a new value of `beads.state`, which has no CHECK.
- The `ops.kind` CHECK allows only `pickup`, `park`, `resume`, `release` and `escalate`. So:
  - A defer is an `OpKind.PARK` operation whose data carries `"defer": "<number>"`.
  - An undefer is an `OpKind.RESUME` operation whose data carries `"undefer": "<number>"`.
  - `Parker.replay` dispatches on that key. See O4 for the alternative, a v3 table rebuild.
- The re-gate's "over" mark is a `meta` row, `deferral_over:<session_key>:<number>` = the epoch it was marked. This follows the `clamp:` marker precedent. It is pruned when its deferral stops being current.

New journal methods (journal.py):

```python
def deferral_insert(self, row: DeferralRow) -> None             # inside the caller's transaction
def deferral_current(self, session_key: str) -> DeferralRow | None
def deferral_next(self, session_key: str) -> int                 # highest number + 1, from 1
def mark_over(self, session_key: str, number: int) -> bool       # INSERT OR IGNORE; True if new
def is_over(self, session_key: str, number: int) -> bool
```

The existing `deferrals_current(ws, role)` and `clamp_deadlines` are unchanged. `clamp_deadlines` already clamps the current `defer_until` with its `clamp:deferral:` marker.

### 2.2 States, reasons and labels

- `BeadState.DEFERRED = "deferred"`: claimed, `v2:deferred`, no session. The reason is `Reason.QUOTA` (with detail `quota_detail(until)`) or `Reason.ACCOUNT_CHANGED` (with detail "account changed").
  - Both reasons already exist. Their interim meanings, `PARKED/QUOTA` and `STUCK/ACCOUNT_CHANGED`, are retired (§6), and the comments on them in states.py are rewritten.
  - `ACCOUNT_REPOINTED` (the unclaimed ready bead) is unchanged.
- Transitions (`ALLOWED`):
  - RESUMING, STARTING and PARKING → DEFERRED (a defer, or a guard defer);
  - DEFERRED → PARKING (a transition or re-defer reuses PARKING while its op is open);
  - DEFERRED → RESUMING (undefer);
  - DEFERRED → HELD and DEFERRED → STUCK (precedence);
  - DEFERRED → PARKED (both labels);
  - HELD and STUCK → DEFERRED (a release of a bead that still carries `v2:deferred`).
- `beads.DEFERRED = "v2:deferred"`.
- `ws_state`: AU-5's `all_quota` becomes `all_deferred`, true when every non-terminal row is DEFERRED, whatever the reason. Either `all_deferred` or an armed wake makes an otherwise idle or all-blocked workstream `DEFERRED`.

### 2.3 Trust

`trust` is `"trusted"` or `"untrusted"`:
- A deferral the gate produced (pickup, guard, re-gate, undefer) is `trusted`. The gate's deadlines come only from trusted windows and marks (D4).
- A session-reported limit (the test entry point now, AU-7 later) is `untrusted` unless its channel is a trusted source. Its `defer_until` goes through `untrusted_defer_until`.
- `account_changed` records are always `trusted` and have no `defer_until`.

## 3. Operations (park.py)

### 3.1 The defer tail

All five ways of deferring share one tail, `Parker._defer_tail(op)`. They differ only in how the op and its record come to exist. **Recording is the intent:** the record (session_key, n) is inserted in the same transaction that sets the op's step to `deferring` with `{"defer": n}` and puts the bead row in PARKING. A replay therefore always continues with the same number, and never allocates a second one.

| step | action | crash replay |
|---|---|---|
| `deferring` | stop every listed session of the bead and confirm none is listed; `RuntimeUnavailable` → `_runtime_hold(op, STOP_UNCONFIRMED)`, op stays open | re-stops (idempotent) |
| `stopped` | claim must be OURS (else ABANDONED, STUCK/CLAIM_LOST); `gitwip.wip_commit(worktree, f"defer:{key}:{n}", ...)` | `find_wip` finds the mark |
| `committed` | `ensure_label(v2:deferred)` | idempotent |
| `labelled` | `ensure_comment(mark=f"wsd-defer session={key} n={n} ", D5 line)` | idempotent by mark |
| finish | one transaction: finish the op (DONE for a defer op, ABANDONED for a guard or undefer op, as the quota shelve did) with the final state; for `account_changed`, `emit(ws, bead, "deferral_account_changed", ref=f"{key}:{n}")` | the alert and the finish commit together, so there is exactly one |

The final state is read from the bead, in precedence order:
- `needs-human` → STUCK/NEEDS_HUMAN;
- `v2:held` → HELD;
- otherwise DEFERRED with the record's reason.

A blocker added meanwhile doesn't change the state: blocked beads are simply not candidates (§4).

Crash points are `defer.deferring`, `defer.stopped!`, `defer.stopped`, `defer.committed!`, `defer.committed`, `defer.labelled!`, `defer.labelled`, `defer.commented!` and `defer.done`. They are prefixed with the op kind where the tail runs inside a resume or pickup op, as the guard's points already are.

### 3.2 Entry points to the tail

1. **`Parker.defer(bead, reason, until, trust, ref=None)`** is the standalone defer, and the test entry point for the session-reported path. Under `entry()` it opens a PARK op with the defer data and the record, then runs the tail. For `untrusted`, `until` is passed through `untrusted_defer_until` before it is recorded. AU-7 calls the same method from its producer.
2. **Pickup source 1, a due resume with no eligible account.** A PARKED resumable candidate whose `_gate_resume` gives a Deadline or AccountChanged is deferred through `Parker.defer`, with reason `quota` and that `until`, or reason `account_changed`.
   - It replaces AU-5's skip. A gate that gives None (the guard must reconcile first) still goes to `resume`, as today.
   - The bead keeps `v2:parked`, so it carries both labels and ends PARKED when undeferred.
3. **Guard step 2** (`_pin`, in a pickup or resume op):
   - A Deadline replaces `_quota_shelve`, and AccountChanged replaces `escalate_from(op, ACCOUNT_CHANGED)`. Each one inserts the record and sets the op's step to `deferring` in one transaction, then runs the tail.
   - Step 2 precedes the generation's entry, so there is nothing to stop. The stop step only confirms that.
   - The bead stays claimed. That includes a new bead whose pickup lost the race between the pickup gate and step 2.
   - Step 3 (`_pin_holds` false) stays as AU-5 left it.
4. **Undefer's gate** gives a Deadline (re-defer, the next number, `quota`) or turns quota into `account_changed` (§3.3).
5. **The re-gate** gives a Deadline on an `account_changed` wait (§3.4). It opens a PARK op with record n+1 (`quota`), because the bead has no open op.

### 3.3 Undefer

`Parker.undefer(bead, ref=None)` is called by pickup under `entry()` after its BUSY check (§4). It opens a RESUME op with `{"undefer": n}` (the current number) and puts the row in RESUMING. Its replay runs:

1. **`intent`**, which checks in this order:
   - Current number ≠ n (superseded meanwhile): ABANDONED, and the row goes back to DEFERRED.
   - `needs-human`: ABANDONED, STUCK/NEEDS_HUMAN.
   - `v2:held`: ABANDONED, HELD.
   - Open blockers and no `v2:parked`: ABANDONED, DEFERRED, still waiting.
   - Then the gate (`decide`, on the record's profile and the session's previous key):
     - An account: the step becomes `gated`.
     - A Deadline: a new record n+1 (`quota`), then the tail.
     - AccountChanged on a `quota` record: a new record n+1 (`account_changed`), then the tail, with the alert.
     - AccountChanged on an `account_changed` record (its over-mark is stale): drop the mark and finish ABANDONED, DEFERRED/ACCOUNT_CHANGED. Nothing else is written.
2. **`gated`**: `ensure_label(v2:deferred, present=False)`, then the step becomes `unlabelled`.
3. **`unlabelled`**:
   - With `v2:parked` on the bead: finish DONE with `parked_state(shown)` (PARKED, or still waiting on blockers). Plan 3's resume takes it from there, and launches only once its blockers close.
   - Otherwise: `self.launch(op)`. `unlabelled` is exactly the step from which `replay_resume` already enters the guard. The guard gates again at step 2 and may still defer (§3.2 case 3).

Crash points are `undefer.intent`, `undefer.gated!`, `undefer.gated`, `undefer.unlabelled!`, `undefer.unlabelled` and then the guard's resume points. The undefer never removes `v2:parked`, and never removes `v2:deferred` before the gate gave an account.

### 3.4 Re-gate of `account_changed` waits

`Parker.regate(bead) -> Regated` takes the bead's current deferral, which must be `account_changed`, with no open op and no over-mark yet:
- **The gate gives an account:** `mark_over(key, n)`, one write. The undefer runs at the next pickup.
- **A Deadline:** open a PARK op with record n+1 (`quota`, that `until`), then the tail: one quota record and one comment. A crash replays the tail.
- **AccountChanged:** nothing is written: no op, no row and no event. The gate is a pure read.

A bead that already carries an over-mark, or has an open op, is skipped without gating, so repeated reloads write nothing. Precedence still applies here, so a held or stuck bead isn't re-gated: `v2:held`, `needs-human` and HELD or STUCK rows are skipped (their release re-gates them, §3.5).

Callers:
- **Startup:** recovery's last step re-gates every DEFERRED/ACCOUNT_CHANGED row of the workstream, once per process start.
- **Reload:** §5.
- **Release:** §3.5.

The scheduler and backstop never call it.

### 3.5 Release

The release paths:
- **`Parker.release` on a DEFERRED/ACCOUNT_CHANGED row** opens no op. Under `entry()` it runs `regate(bead)` and returns the row's state. It never removes `v2:deferred`.
- **`Parker.release` on a DEFERRED/QUOTA row** is `NotReleasable`. A quota wait has nothing for the operator to release.
- **`replay_release` on a bead that still carries `v2:deferred`** (a HELD or STUCK deferred bead): after removing `v2:held` and `needs-human`, it finishes DONE with DEFERRED and the current record's reason, instead of a parked state or a resume. If that reason is `account_changed`, it then runs `regate`.

RELEASABLE becomes {HELD, STUCK, DEFERRED}, with the quota exclusion above.

## 4. Pickup and the wake time (scheduler.py)

- **Source 1** gains DEFERRED candidates, after the BUSY check and before the PARKED resumes. Each one must have:
  - a current coder deferral (only `deferrals_current(ws, coder_role)`, so reviewer deferrals are never read here);
  - `v2:deferred`, no `v2:held`, no `needs-human`, no open op, and a row that isn't HELD or STUCK;
  - either no open blockers, or `v2:parked` as well;
  - and it must be over: `quota` with `defer_until <= now`, or `account_changed` with an over-mark.

  Pickup doesn't pre-gate these. `Parker.undefer` gates them inside its op (§3.3), so a due but ineligible quota deferral gets its next number in the same pickup. A result of STARTED or LIVE returns RESUMED, and WAIT or UNCERTAIN returns `_stalled()`, exactly as a resume does.
- **`_waiters`** (PARKED/QUOTA) is removed after the conversion (§6). **`_deferrals`** keeps its rule (coder, `quota`, `defer_until` > now, not KEPT, not held or needs-human, not blocked) and also skips beads with an open op.
  - A due, ineligible quota deferral is never left due: its undefer writes the next record with a later `until`. So "the current gate deadline of due ineligible deferrals" is that record's `defer_until`.
  - The one exception is a due deferral the pickup didn't reach because the role was busy. The running session's end re-triggers pickup, so it needs no wake.
- **`account_changed`** rows contribute nothing to the wake time. No pickup trigger (`QUOTA_WAKE`, backstop or tick) ever gates one. The gate-call spy in §8 test 2 checks this.
- **The r1 occupancy recheck** before returning DEFERRED stays.

## 5. `wsctl reload` (ctl.py, daemon.py, wsctl)

- `CtlRequest.op` gains `"reload"`, which takes no `ws`. The `wsctl reload` subcommand sends it, and the reply's `data` holds the per-workstream re-gate counts: `over`, `requota` and `unchanged`.
- `Wsd._dispatch("reload")` resolves the full settings with plan 1's loader (`settings.resolve`). On `ConfigError` it replies `refused` with the error's path-free message, and nothing is swapped.
- If the resolved set of workstream names differs from the running set, the reload is also refused: "workstreams were added or removed; restart wsd". See O3.
- Otherwise, one `reload` job is queued per lane. Each runs in that lane's thread under `parker.entry()`, its operation lock. It swaps `Scheduler.ws` and `Parker.ws` (whole frozen `WorkstreamSettings`; host `usage` comes with them), then re-gates every DEFERRED/ACCOUNT_CHANGED row of that workstream.
- The reply waits for every lane's job. Lanes swap in turn, not atomically across workstreams, but each workstream sees one consistent settings object.
- AU-4 does not touch any systemd unit. Wiring `ExecReload=wsctl reload` belongs to the installer plan.

## 6. Retiring the interim paths (O1 of AU-5, and AU-3's escalation)

- **Removed:** `Parker._quota_shelve`, `finish_quota`'s ending as PARKED/QUOTA, `Scheduler._waiters`, `all_quota`, the sweep's PARKED/QUOTA keep branch, and `escalate_from(op, ACCOUNT_CHANGED)` in `_pin`.
- **An open op at step `quota`** (a pre-AU-4 crash mid-shelve) is replayed by converting it into the guard defer: insert record n (`quota`, `until` from `op.data["until"]`) and set the step to `deferring` in one transaction, then run the tail. `QUOTA_STEPS` stays, for replay only.
- **A `PARKED/QUOTA` row with no open op** is converted once, in recovery after the sweep and before the re-gate, by gating it fresh:
  - An account: the row becomes `parked_state(shown)`, and the ordinary resume takes it.
  - A Deadline or AccountChanged: `Parker.defer` with that reason. The bead keeps `v2:parked`, so it has both labels.

  The old `until` is not reused: a fresh gate is at least as accurate. Rows that `needs-human`, a hold or a blocker would replace are left to the sweep, as today.
- **A `STUCK/ACCOUNT_CHANGED` row** (AU-3's escalation) stays STUCK until the operator releases it. The release resumes it through the guard, which now defers it as `account_changed`. No conversion is needed.

None of these rows can exist in production (§0). The conversion is there so that test journals and developer journals upgrade cleanly.

## 7. Sweep and recovery (sweep.py, recovery.py)

`_Sweep._ours` gains a `v2:deferred` branch beside the `v2:parked` one. The existing NEEDS_HUMAN → STUCK and no-row → JOURNAL_LOST checks come first and apply unchanged. JOURNAL_LOST is never relaunched.

| bead / row | result |
|---|---|
| `v2:deferred`, a session listed | UNEXPECTED_STATE "deferred, but a session is listed" |
| `v2:deferred`, row DEFERRED, no op | kept, reason and detail (the KEEP branch AU-5 used for PARKED/QUOTA) |
| `v2:deferred` and `v2:held`, row HELD | kept (a held deferred bead) |
| `v2:deferred`, row PARKING or RESUMING with an open op | left to the op's replay |
| `v2:deferred`, any other row | UNEXPECTED_STATE "deferred, but the journal says <state>" |
| row DEFERRED, no `v2:deferred`, no open op | UNEXPECTED_STATE "the journal says deferred, but the bead isn't labelled" |
| row DEFERRED, the record missing for the bead's session | UNEXPECTED_STATE "deferred without a deferral record" |

Recovery replays open PARK ops (defer and transition included) and RESUME ops at their own steps, as today. It then runs the §6 conversion, then the startup re-gate (§3.4).

## 8. Tests (TDD, offline, injected `deps.clock`, the existing crash-point harness)

**Defer and undefer:**
- A hypothesis crash at every defer point gives the same record, label, comment, row and alert count (0 or 1). The defer runs from each of the five entry points.
- A hypothesis crash at every undefer point gives the same end state, for each of these:
  - an account, then launch;
  - an account with both labels, ending PARKED;
  - a deadline, re-deferring with n+1;
  - quota → `account_changed`;
  - superseded;
  - held;
  - needs-human;
  - blocked.
- A quota deferral never undefers before `defer_until` (clock at `until - 1`, then at `until`). A due quota deferral that gates to a deadline gets n+1, keeps the label, and adds one new comment.
- Neither reason undefers while held, stuck or blocked. With both labels, a crash at each point ends PARKED, and the bead launches only after its blockers close.
- The bead stays claimed throughout. A fake btq asserts that no unclaim happens in any test of this file.
- A lost journal (a `v2:deferred` bead with no row) escalates JOURNAL_LOST, and no launch follows on later pickups.
- The `untrusted` entry point clamps `until` to [now + min_recheck, now + untrusted_max_defer].
- Sweep: each row of the §7 table.
- Release:
  - of a DEFERRED/QUOTA row: refused;
  - of a HELD deferred bead: it ends DEFERRED and keeps the label.

**The AU-5 §8 amendment tests**, named as approved:
1. **`test_quota_to_account_changed_writes_one_comment`:** a due quota deferral, and a resume, that gate to `account_changed` each write exactly one `wsd-defer … reason=account_changed until=none` comment, one superseding record and one alert. A crash at each step replays to one of each.
2. **`test_account_changed_has_no_timer`:** after the clock passes `max_window_hours`, backstop and QUOTA_WAKE pickups never re-gate it (the gate-call count for its session stays 0). `wake_at` never comes from it, and the outcome is never DEFERRED because of it.
3. **`test_startup_regates_once`:** a restart re-gates exactly once. While the result is still `account_changed`, nothing is written: the ops, deferrals, meta and events counts are unchanged. A crash during the startup re-gate replays.
4. **`test_defer_and_regate_replay`:** a crash at each step of defer and of re-gate gives the same record and a single comment.
5. **`test_reload_regates_each_account_changed_once`:**
   - restored: it is marked over, and the next pickup undefers it with all checks;
   - a deadline: it becomes quota with n+1 and one comment;
   - still `account_changed`: nothing is written;
   - repeated reloads with an unchanged config write nothing.

**The reload request itself:**
- An invalid config is refused with a path-free message, and the old settings stay in use.
- A changed workstream set is refused.
- The swap happens under the lane's lock: a pickup queued behind it sees only the new settings.

**Retirement:**
- An open `quota`-step op replays into a deferral.
- Each PARKED/QUOTA conversion outcome.
- `escalate_from(ACCOUNT_CHANGED)` is gone: a guard AccountChanged ends DEFERRED, not STUCK.

**Existing AU-5 tests** that assert PARKED/QUOTA or STUCK/ACCOUNT_CHANGED are rewritten to the DEFERRED outcomes, keeping the same oracles otherwise. That includes the r1 strict "no coder session is listed" check.

## 9. Open decisions

- **O1 [Liam]: `account_changed` is never retried on a timer.** This is ADR §17 open decision 1, which the AU-5 §8 amendment assumed.
  - With it, a wait clears only on a restart, a `wsctl reload` or a release.
  - The alternative is a slow periodic re-gate, for example every `max_window_hours`. That would put back the timer that test 2 forbids.
  - Recommendation: never on a timer, as designed.
- **O2 [Liam]: the review-wait range (`BASE..HEAD`) is stored by AU-13, not AU-4.**
  - The plan says AU-4 stores it, but the `deferrals` table has no column for it, and nothing in AU-4 can write or test a review wait.
  - Recommendation: a plan amendment moving its storage to AU-13 (an additive table keyed (session_key, number)). AU-4 keeps `role` generic, so reviewer records already fit.
  - The alternative is a schema v3 now, with an unused table.
- **O3 [Liam]: what a reload may change.** Proposed: anything that `settings.resolve` accepts, except adding or removing a workstream, which is refused with "restart wsd". Swapping lanes live is out of PoC scope.
- **O4 (team-lead and reviewer): no new op kinds.** Defer and undefer reuse the PARK and RESUME kinds, with a data key.
  - The alternative is a schema v3 that rebuilds `ops` to widen its CHECK. That is a table rebuild in SQLite, plus upgrade tests.
  - Recommendation: reuse, and revisit if plan 6 needs to tell them apart in queries.
  - In the same vein, the over-mark is a `meta` row rather than a column.
- **O5 [Liam]: the alert is a journal event** (`deferral_account_changed`, ref `<key>:<n>`) until AU-9's outbox renders and sends it. Until AU-9, no notification reaches the operator; only `wsctl status` shows the DEFERRED/ACCOUNT_CHANGED row.
- **O6 (reviewer): a due resume defers rather than being skipped.** A PARKED candidate with no headroom now gets a record, a label and a comment (both labels), where AU-5 skipped it. This follows the plan's "only a due resume with no eligible account defers", at the cost of one WIP commit and one comment per deferral.
- **O7 (reviewer): the PARKED/QUOTA conversion re-gates instead of reusing the stored `until`** (§6). It matters only for non-production journals.
