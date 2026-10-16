# Plan8 d2 — actual R1 ordered dispositions

Task btq-8d487. Original d1 commit `c9fa7942386556b5227d96cbe73b8ca4a9223efc`. Actual Claude builtin R1: REVISE28; 1/3 rounds issued. D2 is author repairs, UNREVIEWED/UNAPPROVED. No new model/review/queue/runtime operations. Findings below preserve every original JSON value verbatim and in order.

Raw report SHA256: `978578bab018dc2cf248f98bae068525fb3f78183848681259e542816b2cc983`. Extracted JSON SHA256: `e26d3c67f885b7d4201c9e325f67be9d63a4ada0aed9e5d32cdf8ad5edf33968`.

## 1. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 78,
  "summary": "The 100,000-key cap per active generation, combined with keeping dedup keys while the generation is replayable, stops core scheduling within weeks of normal operation.",
  "failure_scenario": "Pickup every 60 s adds 1,440 keys a day and reconcile every 5 min adds 288, so about 1,728 a day; the cap is hit around day 58, or about day 31 once forge poll (1,440 a day) is on. Admission then stops with a capacity hold, so recurring pickup and reconcile halt until an operator archives the generation and installs a new future start boundary. Slots only increase within a generation and are already guarded by the UTC high-water, so a per-(generation, job) admitted-slot high-water would give dedup with O(1) storage. That is the fix at the right depth, and it avoids a recurring host maintenance step for Liam."
}
```

Disposition (§§4–5): Removed durable dedup keys for idempotent wakes. Effect jobs use O(1) slot high-water plus finite recent detail; no lifetime key capacity stopping core after58 days.

## 2. Removed unsafe protocol

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 66,
  "summary": "The not-issued absence proof is called point-in-time and 'not a lease', yet it 'settles' an uncertain occurrence even while the original request may still be in flight.",
  "failure_scenario": "The runner's scheduled-tick times out at 5 s while still queued in the socket backlog or waiting on the admission lock. tick-status takes the lock first and returns not-issued. The runner marks the occurrence definite-unsent or coalesces it into a later slot and records it as skipped. The original request then gets the lock and commits an accepted intent for a slot the ledger records as skipped or unsent. The fix is a fence: the status lookup writes a tombstone that later admission of that tuple must honor, or WSD drops requests whose connection has closed before admission."
}
```

Disposition (§§4–5): No not-issued/status settlement for wakes. Effects remain uncertain without producer fencing/outcome proof; point-in-time absence never settles an in-flight request.

## 3. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 56,
  "summary": "It is unclear who writes the schedule ledger: WSD inserts accepted intents under its admission lock, while the separate runner oneshot also writes due, uncertain, held and complete states, all under a 'single writer lock'.",
  "failure_scenario": "If both processes write the same SQLite file, the tick-status protocol is redundant, because the runner could read the commit directly, and 'single writer' does not hold. If they are separate stores, nothing says how the runner's uncertain/skipped rows and WSD's accepted intents reconcile. The 'backlog count and oldest age' (line 70) would also be unknown to the WSD health snapshot that the babysitter reads. Implementers would pick incompatible ownership models."
}
```

Disposition (§5): Only WSD writes the effect store through one maintenance/effect lane and protected connection. CLI never opens it; health reads WSD-owned aggregates. Babysitter separately owns observation state.

## 4. Fixed by simplification

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 60,
  "summary": "The request carries only an opaque occurrence_id and no slot index, so WSD, the effect authority, cannot check that the ID is a valid slot key, that the slot is eligible, or that it is consistent with coalescing.",
  "failure_scenario": "A runner bug that derives the ID per process start, or uses the wrong anchor, sends a fresh valid 64-hex ID every pass. WSD accepts each one as a distinct occurrence, which defeats dedup and burns through the 100k capacity. Pre-boundary and negative slots (line 72) cannot be refused at the acceptance boundary. WSD should receive the integer slot and recompute the SHA-256 key itself."
}
```

Disposition (§§4–5): Wakes need no slots/digests. For effects WSD derives integer slot/key from its own installed schedule and clock; the caller cannot inject opaque occurrence identity.

## 5. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 46,
  "summary": "'Job selection reaches the same existing lane methods as a manual tick' sends scheduled pickups to `_operator_pickup` (TriggerKind.OPERATOR) under the shared lane key ('pickup', None).",
  "failure_scenario": "In `daemon._dispatch`, manual tick pickup uses `_operator_pickup`, while the internal backstop uses `_backstop` (BACKSTOP). A scheduled backstop would be journaled as an operator pickup. It would also join a queued operator pickup's job, and by the daemon's own docstring a joined request shares the first one's trigger. That contradicts line 64's 'a lane must retain the occurrence reference while coalescing', because the existing (kind, ref) key keeps one trigger per job."
}
```

Disposition (§4): Scheduled pickup invokes _backstop using a distinct scheduled key; manual OPERATOR cannot join it. Bounded origin IDs/count/truncation are honest diagnostics, not promised durable per-occurrence attribution.

## 6. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 74,
  "summary": "The clock_ready gate can block recurring pickup and reconcile indefinitely, with no bypass, in situations where internal mode keeps running. This breaks ADR17 §10's 'deterministic flows continue'.",
  "failure_scenario": "After a reboot on a host whose time service reports no sync (offline, or sync status not exposed), clock-dependent jobs 'hold visibly until the supported time service provides it', and no manual grant exists. After a one-hour backward NTP correction, 'UTC not behind the durable admitted high-water' blocks all admission for an hour. In both cases the 60 s backstop and 5 min reconcile stop, though they are idempotent wakes that do not need correct civil time. Interval jobs could use a monotonic or sequence basis, leaving the strict gate for calendar and effect jobs."
}
```

Disposition (§§3,5): Monotonic pickup/reconcile timers and startup recovery do not require UTC readiness. Only clock-dependent effects hold on synchronization/rollback.

## 7. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 102,
  "summary": "Hitting the babysitter's alert-episode capacity 'prevents further scheduled admission', which ties the observer's output backlog to the control plane. This contradicts observation-only independence, and no mechanism exists for it.",
  "failure_scenario": "admind is latched or down for a long time and 256 unsettled episodes build up. All pickup and reconcile admission then stops, so a reporting outage turns into a work outage. The babysitter also has no WSD write path, since it may not construct a Journal or call mutating endpoints, so it cannot enforce this hold without a new undefined channel."
}
```

Disposition (§7): Observer backlog only coalesces local summaries/diagnoses capacity. It has no task-control/store write path and never halts WSD scheduled admission.

## 8. Residual explicitly disclosed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 98,
  "summary": "The babysitter shares fate with the same systemd user manager and timers, and nothing detects the babysitter itself being dead. Systemd mode also drops the existing invariant that a dying recurring timer ends `serve` loudly.",
  "failure_scenario": "Today `serve` raises when a timer task dies, so wsd never runs without its backstop. In systemd mode, if the user manager's timers are disabled or the user@ instance fails, WSD keeps running with no backstop and the babysitter never runs. Alerts are only sent on failure, so silence looks healthy and no alert is ever written. A dead-man's switch, such as admind or an external check alerting on a missing babysitter heartbeat, or a statement that this is a residual risk, is required."
}
```

Disposition (§§1.4,7): Same systemd user-manager fate remains; no missing-babysitter deadman is invented. Human deployment disclosure names this and lost internal timer-fatality guarantee; external watchdog is separate future design.

## 9. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 50,
  "summary": "Rollback and mode switch leave several things undefined: committed-but-unexecuted ledger intents and uncertain runner rows, the runner oneshot still active when its timer is disabled, and release/config compatibility.",
  "failure_scenario": "(a) A systemd-to-internal rollback leaves accepted intents 'authoritative', but internal mode never opens scheduled ingress, so nothing recovers or retires them, and uncertain rows stay held. (b) Disabling a timer does not stop a runner pass already in progress, which can still send scheduled ticks during the predecessor restore. (c) `settings.py` rejects unknown `[wsd]` keys via `_only(wsd, WSD_KEYS)`. Rolling back the release while keeping a config that has `timer_owner` makes the old wsd exit with config error 78, which `RestartPreventExitStatus=78` leaves down."
}
```

Disposition (§10): Disable and stop/wait active oneshots, close ingress, drain WSD before restore. Restore predecessor exact compatible config removing unknown timer_owner field. Retain old uncertain effects; no generation-based unsent settlement.

## 10. Fixed; requires human decision

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 130,
  "summary": "Starting core Plan8 work before Plans 5, 6 and 7 departs from the roadmap's Plan 8 'Gate to start: Plan 7' and ADR17 §14's build order, but the design never names it as a sequencing departure that needs explicit human approval.",
  "failure_scenario": "§10 and §12 say only 'No Plan5/6/7 implementation is required' and that design approval comes before implementation. A human approving the design is never told the roadmap gate is being bypassed. Btq or the controller could then wire implementation tasks against Plan 8 while the roadmap still says Plan 7, with no recorded authority for the change."
}
```

Disposition (§§1.1,11): Early core slice is explicit ADR/roadmap sequencing amendment. Without authenticated changed-design approval, Plan7 start gate remains; no release/consumer prerequisite waiver.

## 11. Fixed; requires human decision

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 112,
  "summary": "Deferring user schedules removes ADR17 §9's v1 `schedules.toml` jobs (fixed commands and filed beads), which §12 counts as v1 'cron'. The design frames this as outside 'this approval scope' and says it 'does not create another approval gate', instead of declaring an ADR departure.",
  "failure_scenario": "The roadmap rule says 'any finding that requires changing the ADR stops the plan' and needs an ADR amendment with a new approval bead. The design neither requests an amendment nor asks the operator to explicitly accept a scoped v1 deferral. Plan 9 acceptance could then judge v1 complete without the ADR-required user jobs, or the gap could surface late as an unapproved ADR change."
}
```

Disposition (§1.2): User jobs deferral is an explicit v1 scope departure requiring real changed-design authority and revised release acceptance or continued original requirement. Not silently complete.

## 12. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 29,
  "summary": "Several new or changed behaviors are not listed as ADR17 departures for the human approval: the five-minute babysitter (absent from §9, where only the old babysitter is culled in §14), the typed scheduled ingress replacing §9's 'timers call `wsd tick <job>`', and the new 30-day compaction and capacity holds.",
  "failure_scenario": "§9 line 126 requires publishing 'the reviewed design and deviations', but no deviations list exists. The Marmot approver cannot see which parts change the ADR and which implement it, so approving the design may not count as genuine approval of those deviations."
}
```

Disposition (§1): Numbered human departure inventory lists early sequencing, user jobs, typed ingress, observer failure domain and high-water/retention/capacity semantics; publication/deployment must disclose it.

## 13. Source-backed rebuttal plus clarification

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 118,
  "summary": "'Reuse Journal.backup' and 'serializes the backup on the owning WSD journal connection' do not match the code: `Journal.backup` does not take `Journal.lock`, and its `_depth`/`in_transaction` check races with lane threads on the shared `check_same_thread=False` connection.",
  "failure_scenario": "A maintenance request runs `backup()` on one thread while a workstream lane thread opens BEGIN IMMEDIATE on the same connection just after the check. `db.backup` then reads through a connection with an open write transaction and may copy uncommitted pages, or it deadlocks, depending on SQLite's behaviour. The design must require taking `Journal.lock` for the whole copy, say which lane runs it, and accept that this stalls lane writes."
}
```

Disposition (§§2,8): Exact076 journal.py:616 has @_locked; decorator at417–424 takes self.lock (line422) across method body; transaction at628+ shares lock. Claim of missing Journal.lock is false. Maintenance lane calls existing locked backup outside transaction; documentation now explicitly states writes can block for copy.

## 14. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 34,
  "summary": "The journal backup gets its own default time (03:15 UTC) instead of being 'backed up with the beads backups' (ADR17 §3.3; roadmap Plan 8 says 'timed with the beads backups'), and this is not declared as a deviation.",
  "failure_scenario": "The journal and Beads snapshots are taken at unrelated times. A restore pairs a journal from 03:15 with a Beads backup from another hour, which widens the recovery window that §3.3's coupled backup was meant to keep small. The design accepts 'not a cross-store atomic checkpoint' but never names the departure from ADR timing."
}
```

Disposition (§8): No unrelated03:15 default. Couple WSD journal/schedule snapshots to actual inventoried Beads backup cycle via supported companion. No second Beads invocation; independent capture times/partial native receipt are explicit, not cross-store atomicity.

## 15. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 52,
  "summary": "The schedule ledger 'backs up its own store', but the fixed job enum has only `journal_backup`, so the ledger backup has no scheduled owner, cadence, retention or receipt.",
  "failure_scenario": "Nothing ever invokes the ledger backup, so §9's 'Do not restore an old schedule ledger while leaving newer effects replayable' cannot be met. Losing the ledger loses all dedup keys and accepted intents, with no backup to restore."
}
```

Disposition (§§3,8): backup_bundle owns both local-store backups in same Beads backup cycle, with retention/hash/partial receipt and crash settlement. No unowned schedule-ledger backup.

## 16. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 30,
  "summary": "The design gives forge poll to a 'Future approved Plan5 intake adapter', but ADR17 §12 and §14 put forge bridge and GitHub/Radicle approvals in phase 2, and roadmap Plan 5 has no forge intake.",
  "failure_scenario": "The activation gate points at a Plan 5 contract that will never include forge intake. Forge poll then either stays disabled forever under the wrong gate, or an implementer adds it to Plan 5 without the phase 2 design and approval round the ADR requires."
}
```

Disposition (§§3,6,9): Forge intake is explicitly phase2 forge-bridge contract under ADR12/14, not a future Plan5 adapter.

## 17. Fixed compatibility disclosure

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 31,
  "summary": "Pinned Plan6C draft: the 15-minute reminder sweep is the only invocation cadence offered, but the pinned 6C draft says 'Plan8 supplies clock and invocation cadence' for its 10-minute escalation and 60 s secondary alert deadline. Its 5 s reconcile budget also equals Plan8's total 5 s request deadline.",
  "failure_scenario": "A card with no full copy at anchor+600 s is only checked at the next 15-minute sweep, so delivery escalation lands up to about 25 minutes late. The 60 s secondary local alert waits a further sweep. 6C calls that use their full 5 s budget overrun the 5 s connect/write/read deadline and become uncertain. The compatibility gate should state the cadence and deadline mismatch rather than imply alignment."
}
```

Disposition (§9): Retain historical d1 mismatch; pin current unapproved6C d2 whose own600/660/4h nearest-deadline callbacks and30s recovery sweep remove Plan8 polling dependency. Require actual bounded enqueue/clock/shared-writer fixtures; no assumed approval or5s reducer+RPC fit.

## 18. Fixed by simplification

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 72,
  "summary": "Interval slots use floor((UTC_now-A)/P), but no timer unit settings are given. Systemd's default AccuracySec=1min on a 60 s period lets activations fall twice into one slot or skip slots.",
  "failure_scenario": "With the default timer accuracy, the pickup timer fires at t=59 s and t=61 s, which are slots 0 and 1, or at 61 s and 119 s, both in slot 1. The second firing dedups to the same receipt, so the actual backstop gap can reach about 2 minutes. The design should require AccuracySec=1s with OnCalendar or OnUnitActiveSec aligned to A, or key slots by activation."
}
```

Disposition (§3): Core wakes use OnBoot/OnUnitActiveSec monotonic timers AccuracySec1s, no UTC slot dedup. Late activations coalesce one wake; no adjacent-slot suppression.

## 19. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 92,
  "summary": "The health snapshot is written 'after startup' and is stale after 90 s, so long startup recovery and ordinary Restart=always restarts produce false failure episodes.",
  "failure_scenario": "Startup recovery runs bd subprocesses for every workstream before the socket or loop is up. If it takes over 90 s, no snapshot exists, so the babysitter reports stale/unavailable on its first observation and opens a failure episode on each restart. The heartbeat should start before recovery and report recovery_status=in_progress."
}
```

Disposition (§7): Serving-loop sampled heartbeat starts before startup recovery with explicit in_progress and600s recovery-stalled condition; write work delegated/coalesced.

## 20. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 94,
  "summary": "Health and alert semantics are not exact. There is no closed set of health states (healthy, stale, unknown, unavailable, overdue, maintenance, capacity, clock-hold), no mapping from state to episode or alert, and no fixed alert text templates.",
  "failure_scenario": "One implementation alerts on an overdue job while the daemon is healthy, and another only reports it. Maintenance expiry and clock holds may or may not open episodes. The 'one failure/recovery notice per episode' rule cannot be tested deterministically, and the babysitter's output cannot be golden-tested."
}
```

Disposition (§7): Finite precedence/codes, per-code episode clear/recovery behavior and two fixed text templates make health golden-testable; no inferred task failure.

## 21. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 62,
  "summary": "Only the accepted and tick-status responses have exact schemas. Refusals of scheduled-tick (owner mismatch, stale generation, not_ready, unsupported job, conflicting payload) have no closed response shape or reason enum.",
  "failure_scenario": "The runner cannot tell a definite pre-admission refusal, which is safe to mark definite-unsent and coalesce, from a malformed frame, which must be held. It either holds every refusal, which piles up uncertain rows during each 30 s clock-readiness window after a restart, or it guesses from freeform text."
}
```

Disposition (§4): Closed queued/refused schema/reason enum and targets/joined bounds. Partial submit/transport error is unconfirmed wake, not definite-unsent or durable acceptance.

## 22. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 70,
  "summary": "Uncertain rows are never coalesced away, each pass allows one status lookup per occurrence, and the 30 s pass deadline with 5 s requests caps a pass at about 6 calls. No ordering is given between draining uncertain rows and admitting new due ones.",
  "failure_scenario": "WSD's event loop stalls while still accepting connections for 30 minutes, leaving about 30 uncertain pickup rows. After recovery, each pass spends its budget on status lookups for old rows, so new due occurrences are starved for several passes. Health shows a backlog while pickup is effectively paused."
}
```

Disposition (§§4–5): No accumulating uncertain wake rows/status drains. One request per natural wake; effects have round-robin bounded calls reserving fresh due capacity and per-job holds.

## 23. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 60,
  "summary": "The 16 KiB request limit conflicts with the existing control socket, where `ctl.MAX_REQUEST = 4096` and the server stream limit is MAX_REQUEST+2. The design does not say whether scheduled ingress uses a new socket or the existing frame limits.",
  "failure_scenario": "A scheduled-tick frame between 4 and 16 KiB, which the design allows, is refused by the existing `CtlServer._handle`. Or the shared socket limit is raised for every op. Either way the stated bound is not the one actually enforced."
}
```

Disposition (§§2,4–5): Retain shared ctl4096-byte total request bound; wake variant max1024bytes and response1024. No undocumented16KiB socket expansion.

## 24. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 44,
  "summary": "In systemd mode the cadences come from the fixed registry (60 s, 5 min), but the existing `[wsd] backstop_seconds` and `reconcile_seconds` keys stay in config and in the restart view, and the design does not say whether they are rejected, ignored or reported.",
  "failure_scenario": "An installation with backstop_seconds=20 switches to systemd mode. Its configured backstop silently becomes 60 s while `config check` still shows 20, so the operator believes in a cadence that is no longer in effect."
}
```

Disposition (§3): Existing backstop/reconcile fields remain effective and reported in both modes; externalmode integer/range compatibility refusal preflight. Prior20s renders20s.

## 25. Accepted simplification

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 64,
  "summary": "Simplification: pickup and reconcile wakes are idempotent and coalesced on the lane, and WSD startup already recovers and picks up every workstream. The full crash-state machine, uncertain-hold and absence-proof machinery is unnecessary for those two jobs.",
  "failure_scenario": "The receipt/status/uncertain protocol adds a ledger table, an admission lock, recovery of accepted intents and status lookups whose only job is to stop a duplicate wake that is harmless. At-least-once wakes, with startup recovery covering pre-crash intents, would do the same job; the exact-receipt ledger could be kept for non-idempotent adapters (backup, digest, reminder). This would also remove findings 2, 21 and part of 1."
}
```

Disposition (§4): Removed wake ledger/intents/absence proof entirely. Startup and repeat idempotent wakes preserve native action guards; effect dedup remains separate.

## 26. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 56,
  "summary": "Reuse: the new SQLite schedule ledger and its 'own supported online' backup re-specify infrastructure that `wsd/journal.py` already has: `backup_to(db, dest)`, the `transaction()`/JournalBusy pattern, integrity check and versioned upgrade. `fsutil.private_dir` also exists.",
  "failure_scenario": "A second hand-rolled SQLite store duplicates the busy handling, private permissions, upgrade-refusal and online backup code that the journal already has. Bugs fixed in one copy would not reach the other."
}
```

Disposition (§§2,5,8): Explicit reuse journal backup_to/lock/busy/transaction/private_dir patterns, distinct strict schema and soleWSD effect owner; no handrolled duplicate backup engine or mutating Journal on wrong schema.

## 27. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 92,
  "summary": "Efficiency: rewriting the snapshot atomically (up to 256 KiB, private and fsynced) on every job transition, on the serving event loop, blocks the loop. It also scales with the number of workstreams times job transitions.",
  "failure_scenario": "With up to 128 allowlisted workstreams, each pickup or reconcile fan-out causes hundreds of synchronous write+fsync+rename calls on the asyncio loop, delaying socket handling and quota wakes. The fix is to mark the snapshot dirty on transition and write it at most once per heartbeat tick."
}
```

Disposition (§7): Transitions markdirty; event loop captures at most one immutable summary per30s, one offloop writer/fsync outstanding. Timestamp belongs loop sample, so writer cannot falsely advertise health.

## 28. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 76,
  "summary": "Identity terms are inconsistent: 'schedule generation' (line 46), 'configuration generation' (lines 56 and 76), interval keys built on 'job' (line 72) and calendar keys built on 'schedule ID' (line 76) are never defined as the same or different fields.",
  "failure_scenario": "One implementer keys calendar occurrences by job enum and another by a separate schedule ID. Dedup keys and status tuples then differ between runner and WSD, so an accepted digest occurrence can show as not-issued under the other key."
}
```

Disposition (§§3,5): Single generation term; registry job is schedule ID. One canonical version/install/generation/job/integer-slot array, authority-derived; wake request IDs only correlate observations.
