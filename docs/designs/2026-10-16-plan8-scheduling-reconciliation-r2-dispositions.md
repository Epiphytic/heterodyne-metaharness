# Plan8 d3 — actual R2 ordered dispositions

Task btq-8d487. Reviewed d2 parent `09411b3c48bc2018540a3fa29afc3c3f3fe60239`. Actual R2 REVISE20; two of three issued rounds used, only R3 remains. Author repair d3 is UNREVIEWED/UNAPPROVED. No review-budget reset. Historical R1 findings/dispositions retained.

Raw report SHA256 `7cd005a26f96dbce4c5d94f7e38fad94d53398eb138477696c674fb731e72af4`. Extracted JSON SHA256 `891def21eda42951514c1e6fceee0d081ac883ed6dc771cd22fa0bdded03caf5`. All twenty original JSON findings below are verbatim/in order.

## 1. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 94,
  "summary": "Health episodes are keyed only by install/generation/code, so a later episode of the same code in the same generation reuses the earlier episode's alert identity. admind's outbox_key is derived only from the alert file name (alerts.py:104-107), so the repeat is never relayed.",
  "failure_scenario": "WSD goes `stale`, recovers (recovery notice sent), then goes `stale` again next week in the same generation. The second episode has the same key and file ID. The shared writer either settles it as 'matching bytes', refuses it as 'conflicting bytes', or writes a file that admind dedups as already relayed. The operator never hears about the second outage. Episode identity needs a per-code sequence or epoch, and the alert ID must be unique per episode and per recovery notice."
}
```

Disposition (§7): Persist per-code episode sequence and observer store UUID before publication; each failure/recovery phase has separate canonical identity. Repeat outages/restarts cannot reuse filename/outbox identity; corrupt identity holds publication rather than reset.

## 2. Fixed with disclosed delivery limit

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 96,
  "summary": "The 256 'outstanding alert files' cap has no settlement or cleanup path. admind relays each file once and never deletes it ('cleanup is the writer's job', alerts.py docstring). The observer cannot read admind's relay receipts ('file receipt is local reporting only'), so files never stop being outstanding.",
  "failure_scenario": "After 256 lifetime p8_ episode and recovery files (or fewer, if they share the directory with the proposed 6C files), the observer stays in capacity mode forever. Every later failure is only coalesced into a local summary plus a service-journal line, and the 'drain after space returns' step never runs because space never returns. Real outages then produce no Marmot alert."
}
```

Disposition (§7): Coordinate neutral shared local_alerts publish/settle, one five-second cross-process directory lock,255 individual slots plus reserved overflow, bounded owner metadata and exact-ID/hash cleanup. Local condition settlement can retire a file before relay and does not claim delivery. Foreign/active-unknown saturation stays visible/coalesced; never gates core. Current reader has no relay receipt API.

## 3. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 70,
  "summary": "Calendar daily slots are the integer UTC day number, yet local-time civil schedules are explicitly supported. Across a DST change, two consecutive civil days can map to the same UTC day, so the high-water dedup drops one real occurrence.",
  "failure_scenario": "Job at 19:30 America/New_York. Day X before spring-forward is 19:30 EST = 00:30Z on UTC day N+1; day X+1 is 19:30 EDT = 23:30Z, also UTC day N+1. highest_consumed_slot is already N+1, so day X+1 is suppressed as consumed. The reverse transition skips a UTC day. The slot should be the local civil date ordinal, with the chosen offset pinned alongside it."
}
```

Disposition (§5): Calendar slot is local civil date ordinal, with zone/civil instant/resolved UTC offset pinned. Timezone changes require generation; include consecutive19:30 New_York spring/fall days in acceptance.

## 4. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 58,
  "summary": "Each scheduled job start now writes an ordinary journal event. At baseline, backstop pickups write no per-tick rows (pickup only calls set_ws_state). The journal has no retention: journal.py says 'Retention is plan 8's, with the journal's', and this design adds none. That moves R1.1's unbounded-growth problem into the journal.",
  "failure_scenario": "60 s pickup plus 300 s reconcile is 1,728 rows per workstream per day; with 128 allowlisted workstreams that is about 221k rows per day, without limit. The journal grows indefinitely, every status/snapshot scan and integrity check gets slower, and each locked Journal.backup copy (§8) takes longer while blocking all lanes."
}
```

Disposition (§4): Removed per-start ordinary journal events. Provenance is bounded queued/running/latest in-memory projection only; restart loses it honestly. No added per-core-wake journal rows or claimed full historical retention.

## 5. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 92,
  "summary": "A single code is chosen by precedence, but episodes open and clear per code. A higher-precedence code (unavailable, unknown, stale) masks the lower ones, which then look 'cleared' and later 'reopen'.",
  "failure_scenario": "`effect_hold` is open. WSD restarts and the snapshot is briefly `unavailable`, so the evaluated code is no longer effect_hold. That sends 'Plan8 health: recovered from effect_hold', then a fresh effect_hold failure notice once the snapshot returns, although the hold never cleared. Every daemon restart produces spurious recover/reopen pairs, and with the fixed episode key these also hit the collision above."
}
```

Disposition (§7): Independent true/false/unknown predicate values; precedence is display only. Only explicit false closes an episode; unavailable/stale/recovery-in-progress do not clear lower unknown predicates.

## 6. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 60,
  "summary": "The closed scheduled-wake response schema (null request_id for invalid input, reasons invalid_request and stopping) cannot be produced by the shared server path. At baseline, ctl.CtlServer._handle answers malformed, timed-out, closing and internal-error requests with a legacy CtlReply(result, message, data) before any handler runs.",
  "failure_scenario": "During shutdown (`self.closing`), or for any frame that fails CtlRequest decoding, the oneshot gets {result:'refused', message:'wsd is stopping…'} instead of {version:1, status:'refused', reason:'stopping'}. It treats this as an invalid response (`wake_unconfirmed`), so the closed enum is never exercised and its golden tests can't pass. The design must say how CtlServer's pre-dispatch framing changes, without breaking existing clients."
}
```

Disposition (§4): Retain legacy CtlReply frame for every path. New strict decode variants put bounded string data under scheduled only after dispatch; malformed/closing/read-timeout retain legacy generic refusal, unexpected errors legacy failure. No impossible typed pre-dispatch reason/null-ID contract.

## 7. Fixed by separating owners

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 104,
  "summary": "backup_bundle has two contradictory ingress and identity models. §5 says effect jobs use `effect-wake` with a slot WSD computes from its own registry and clock. §8 uses a separate 'maintenance request' fired by the external Beads backup cycle. Neither defines the backup slot or dedup when the Beads cadence is not exactly daily.",
  "failure_scenario": "The inventoried Beads backup runs every 6 h (or twice on one UTC day after a delay). With a UTC-day slot, later cycles are deduped and the WSD snapshots are no longer taken in the same cycle. With a per-invocation slot, 'seven daily/four weekly' retention and the high-water have no basis. If the companion runs just after midnight, the slot jumps. Implementers will pick incompatible models."
}
```

Disposition (§§3,5,8): Remove backup from effect-wake/calendar slots. One proposed native-owner paired-cycle wrapper persists increasing cycle sequence before native attempt; WSD sole maintenance lane highwater/pending cycle. Exact native cadence and actual receipt status, unknown remains partial. Seven-day/four-week artifact retention is separate from cycle dedup.

## 8. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 128,
  "summary": "Rollback removes Plan8 timers and config but never removes the companion seam installed in the existing Beads backup owner's unit or command (§8), which Plan8 does not own.",
  "failure_scenario": "After rollback, the next Beads backup cycle still runs the companion. It either execs a path from the removed release (the Beads backup unit fails or reports partial), or sends an unknown maintenance op to the predecessor WSD, which refuses it as malformed every cycle. An owner outside Plan8 is left changed, and its health is degraded by the rollback."
}
```

Disposition (§§8,10): Rollback inventories and restores exact predecessor native-owner unit/wrapper/config/enablement and removes companion before release removal, stopping/waiting affected backup activity only within actual deployment authority.

## 9. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 88,
  "summary": "The snapshot that the event loop captures every 30 s must include each effect's pending/uncertain/held counts and oldest age from the WSD-owned SQLite effect store. That means either blocking SQLite I/O on the loop, or contention on the store's RLock, which the backup_to copy (§8) and the effect lane hold.",
  "failure_scenario": "During the daily schedule-store backup, or a 5 s producer call made under the store lock, the loop's capture blocks. The sequence stops advancing for more than 90 s, so the babysitter opens a false `stale` episode and alert, the very false-liveness failure §7 was meant to remove. The counts should be an in-memory mirror updated by the effect lane."
}
```

Disposition (§§5,7): Effect lane publishes immutable cached aggregates after committed transitions and recovery, with revision/time/readiness. Heartbeat loop reads cache only, no SQLite/connection/store-lock waits and no producer call while store lock held.

## 10. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 60,
  "summary": "An unexpected partial-submission error 'closes scheduled admission' with no reopen path, so one internal error permanently stops systemd-mode core wakes until the daemon restarts.",
  "failure_scenario": "A thread-start RuntimeError or a bug during one wake's multi-lane submit closes scheduled admission. Every later timer wake is refused or unconfirmed for days. Recurring pickup and reconcile stop while WSD looks alive, and only `wake_overdue` notices. The design should say whether admission reopens at the next turn, or whether this is a fatal error that triggers a restart (matching the internal-mode invariant that a dying timer ends serve)."
}
```

Disposition (§4): Unexpected partial enqueue closes admission and signals serving task fatal/drain/raise path. Existing service restart contract and native recovery apply; no permanent alive-but-closed scheduled mode or fake partial success.

## 11. Fixed without altering original API

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 104,
  "summary": "Calling Journal.backup on the shared daemon connection holds the Journal RLock for the whole single-step SQLite copy (backup_to calls db.backup(copy) with pages=-1). Every lane and journal user stalls for a time proportional to journal size, which has no retention. A separate read-only WAL connection, or a stepped backup, avoids the global stall.",
  "failure_scenario": "With a multi-GB journal (see the per-wake journal-row finding), the copy takes tens of seconds. All 128 workstream lanes block on their next journal write, scheduled jobs pile up, and any latency-bound journal path (for example, hook decisions with ADR §10's 5 s fail-closed shim) times out. In WAL mode a backup from a separate connection does not block writers."
}
```

Disposition (§8): Keep existing Journal.backup locking unchanged. New maintenance path opens separate read-only non-immutable WAL connections, reuses copy algorithm with proposed bounded progress callback, avoids daemon Journal lock; source schema/path verified without mutating Journal construction.

## 12. Explicit human choice

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 46,
  "summary": "This is an undisclosed change in cadence meaning. Internal `_every` sleeps the cadence after each job finishes. Systemd OnUnitActiveSec fires relative to the oneshot's activation, which does not wait for the lane job, so slow workstreams run pickups back to back.",
  "failure_scenario": "A workstream whose pickup takes 90 s with backstop_seconds=60 always has one running and one queued scheduled pickup, so bd/Dolt runs continuously instead of with 60 s idle gaps. Moving to systemd mode multiplies load across workstreams, even though the design says cadences are 'preserved'."
}
```

Disposition (§§1.6,3): Preserve numerical cadences, disclose changed semantics: external activation interval vs internal completion-relative sleep, bounded queued backlog/possible back-to-back load. Actual changed-design/deployment authority must accept owner switch; no claim semantics unchanged.

## 13. Fixed; explicit ADR amendment

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 66,
  "summary": "The new authoritative schedule/effect store is not added to ADR17 §3.3's state-ownership and recovery table, and not listed among the §1 departures. Its 'recovery if lost' semantics is only implied by a hold.",
  "failure_scenario": "The approver sees retention and high-water changes in §1.5, but not that a new SQLite authority with its own loss behaviour now sits outside ADR §3.3. Approving the design may not cover this ownership change, and runbooks built from §3.3 omit the store from recovery order."
}
```

Disposition (§§1.5,5,8): Add new WSD-owned authoritative effect store and loss/recovery behavior to proposed ADR17§3.3 amendment. Missing/corrupt store holds effects, cannot reconstruct from clock; producer reconciliation or genuine Marmot recovery establishes safe nonreplay boundary.

## 14. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 58,
  "summary": "Origin association for joined wakes must be updated atomically with Lane.submit's join under Lane.lock. Otherwise a request_id can be added after the worker has popped the job and written the start event, and is silently lost without `origins_truncated` being set.",
  "failure_scenario": "Wake B joins queued job J. Between submit() returning J and the event loop appending B's request_id, the worker thread starts J and journals origins {A}. B's reply says joined, but attribution shows A only and is not marked truncated. That breaks 'a truncated set is never presented as complete'."
}
```

Disposition (§4): Submission join and provenance update occur under the same Lane.lock; dequeue freezes association under that lock. No append-after-submit race. Maintain existing shutdown→lane lock ordering.

## 15. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 106,
  "summary": "The reused backup_to renames the temporary file into place without fsyncing the artifact or its directory. The design publishes the bundle manifest and relies on artifacts after a crash, but never requires them to be durable before the manifest.",
  "failure_scenario": "After a power loss following manifest publication, the manifest is on disk but the renamed snapshot is empty or torn. Crash settlement by 'exact verified artifact manifest' then rejects it or, worse, counts a retained 'success' bundle that cannot be restored."
}
```

Disposition (§8): Snapshot files+directories fsynced and reopened/integrity/hash checked before durable manifest publication. New shared durable wrapper required; current backup_to not falsely claimed durable. Crash settlement revalidates all exact artifacts.

## 16. Fixed with startup honesty

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 54,
  "summary": "The `not_ready` readiness check assumes the socket accepts connections during startup recovery. At baseline, serve() starts CtlServer only after _startup's recoveries and pickups, so wakes during long recovery fail to connect instead of receiving `not_ready`.",
  "failure_scenario": "On every restart with a 10-minute recovery, each pickup and reconcile activation logs `wake_unconfirmed` and its oneshot may exit as failed. `not_ready` is never emitted, and the closed refusal tests don't match real behaviour."
}
```

Disposition (§§2,4,7): Keep listener start after actual startup recovery; connection failures during recovery are unconfirmed wakes. In-progress snapshot begins before recovery. No impossible not_ready wire reply or implicit listener-start reorder.

## 17. Fixed

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 52,
  "summary": "'Keep 4096 bytes including the newline' contradicts the existing shared limit, which excludes the newline (`len(line.rstrip(b\"\\n\")) > MAX_REQUEST`, stream limit MAX_REQUEST+2), even though the same sentence says not to change shared socket limits.",
  "failure_scenario": "An implementer either tightens the shared check for every op, breaking a valid 4096-byte manual request, or leaves it, so the stated bound isn't the one enforced. The malformed-4KiB acceptance test then has two correct answers."
}
```

Disposition (§§2,4): Preserve legacy4096 payload bytes excluding newline and MAX_REQUEST+2 reader; new variants<=1024 payload, legacy reply framing retained. Explicit future acceptance includes4096+newline boundary.

## 18. Simplified to remove unsafe suppression

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 88,
  "summary": "The maintenance receipt that suppresses liveness alerts has no defined writer, location, authority or replacement rule. 'Cannot renew itself' does not stop new receipts being issued back to back.",
  "failure_scenario": "Any same-UID process or a stuck maintenance script writes a fresh 30-minute receipt every 25 minutes. The babysitter suppresses unavailable and stale indefinitely while WSD is down, and no alert fires."
}
```

Disposition (§7): No maintenance receipt suppresses predicates/publication. A proposed installer-only diagnostic label binds actual deployment authority, fixed UTC<=30min window/install/generation, consumed authority/window state and no shifted reuse; expiry/clock unknown cannot extend. Same-UID residual explicit; no ongoing approvals introduced.

## 19. Reuse accepted

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation.md",
  "line": 96,
  "summary": "Reuse: the atomic durable private write that the shared alert writer and the 30-second snapshot writer need (O_EXCL/O_NOFOLLOW 0600 temp file, fsync, rename, directory fsync) already exists as agents/base.py write_at and write_private. §2's reuse list doesn't name them.",
  "failure_scenario": "A third hand-rolled atomic-write implementation appears in admind/alerts.py and the snapshot writer, and durability fixes in write_at don't reach them."
}
```

Disposition (§§2,7,8): Reuse agents/base.py write_at/write_private atomic durable primitives, move/re-export via neutral common facade if necessary; shared alert writer adds directory-lock/final-byte semantics instead of cloning atomic writes.

## 20. Citation corrected explicitly

```json
{
  "file": "docs/designs/2026-10-16-plan8-scheduling-reconciliation-r1-dispositions.md",
  "line": 174,
  "summary": "The R1.13 rebuttal cites 'transaction at628+', but at 076 `def transaction` is at journal.py:626 and its `with self.lock` is at line 633. Line 628 is inside the docstring.",
  "failure_scenario": "A reviewer checking the rebuttal against the exact baseline finds the cited line doesn't show the lock, which weakens an otherwise correct, source-backed rebuttal."
}
```

Disposition (§2 and this disposition): Correct source reference: Journal.transaction definition at626 and with self.lock at633; backup @_locked at616, decorator lock at422. Historical R1 disposition bytes retained; its628+ shorthand is superseded by these exact lines, not used as lock evidence.
