# Plan8 d4 actual R3 dispositions

All12 original findings verbatim/in order. Final R3 REVISE; cap spent. Author responses are not reviewer clearance.

## R3.1

The new backup path copies in steps (pages=128, progress callback) over a separate read-only connection, while the daemon keeps writing through its own connection. SQLite restarts a stepped backup from page 0 whenever another connection changes the source between steps, so the copy may never finish.

Failure scenario: Busy period with sessions running and hook/launch journal writes every few seconds. Python's backup() also sleeps 0.25 s between steps by default, so a multi-GB journal needs about 2000 steps (several minutes). Each commit by the daemon resets the copy to the start. The 30-minute deadline then fires every cycle: backup_bundle is always partial and no verified journal artifact is ever produced. Fix: hold one explicit read transaction on the source connection for the whole copy (a stable WAL snapshot, with no restart and writers not blocked), or use a single step or VACUUM INTO with the deadline enforced another way. State this in the design.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:110`. Exact finding SHA256 `ffdec5c6c4c5fe363bd879635764a2821d635d76d9f28944ea40f62db1d2446f`.

Author response (§8 stable snapshot): Explicit BEGIN+SELECT read snapshot for all backup steps, separate WAL readers and bounded WAL/free-space/cancel deadline; disposable43-commit spike retained.

## R3.2

Cleanup can only remove files whose locally ended condition is settled, but nothing ever settles a recovery-phase file. Every recovery notice therefore holds one of the 255 shared slots forever, which brings back R2#2 for recovery files.

Failure scenario: A failure file settles when its condition clears, but the recovery file then written has no ended condition and is never settled. After about 255 stale/recover flaps over the deployment's life (fewer when shared with 6C), the directory is full of permanent Plan8 recovery files. Every later Plan8 and 6C failure is only folded into shared_overflow.json, which admind relays once. New outages stop reaching Marmot. Define when a recovery notice settles (for example, once it is written or once the next episode opens) and include it in the shared fixture.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:98`. Exact finding SHA256 `9c475f7bbb14502447cd2b92953dd36936906be5bcc2bd8ef1eac37ffc7a84b8`.

Author response (§7 settlement): Recovery/reset notices settle after exact durable local publication; failure settles on known false. Crash resumes same intent. >255 cycles fixture.

## R3.3

Scheduled wakes queue the job and return without waiting, and the design names no consumer for the job's result. The existing _guarded rule ('any other failure to record it propagates as itself: a timer that meets it dies, and so does wsd') is silently lost in systemd mode.

Failure scenario: A scheduled pickup fails, and journal.emit('tick_failed') then raises something other than JournalBusy (a corrupt or poisoned journal, disk full). In internal mode the _every task dies and serve() exits, so the unit restarts. In systemd mode the error stays on a future nobody awaits. WSD keeps answering 'queued' to every wake while each job fails the same way. wake_overdue stays false because admission continues. The design needs a done-callback that sends such errors into the same fatal serve path as a partial submit.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:62`. Exact finding SHA256 `307a469e20b80fe415892001ec9f49b37c88ccf54a4441f8ce47962793eb8ee6`.

Author response (§4 fatal futures): Every new scheduled future has one result consumer driving a serve fatal latch on unexpected exceptions, existing bounded drain/restart retained.

## R3.4

The effect high-water mark is kept per (installation_id, generation, job), and the effect identity includes generation. Any approved reinstall starts a new generation with an empty high-water mark, so a calendar slot already used in the old generation can be issued again.

Failure scenario: The digest runs at 08:00 under generation G1 and succeeds. At 10:00 an approved reinstall (cadence change, release upgrade or timezone change) mints G2. The G1 job is settled, so §10 lets G2 admit slots. 'Coalesce missed unissued slots to latest eligible' picks today's civil date, which has a new effect identity hash, so a second digest goes out the same day. The interval rule rejects slots before generation start; calendar has no equivalent. Carry the high-water mark over per job across generations, or reject calendar slots due before the generation was activated.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:74`. Exact finding SHA256 `5705ba82e74b04c30e68a862cedc4f3ff8bfbd7e5eba8815e5208573dca73e20`.

Author response (§5 activation boundary): Reject calendar resolved due instant before approved generation activation, including same-day earlier slots; unknown prior effects still hold.

## R3.5

The §1 list of proposed amendments (items 1–6) leaves out two human decisions that the manifest's human_decisions_required lists. One is the native-owner paired backup seam with its non-atomic three-store capture. The other is that local alert settlement/coalescing can remove a diagnostic before relay, with same-manager/overflow freshness limits.

Failure scenario: The approver approves the six numbered §1 items as the changed-design scope. Implementation then wraps the foreign Beads backup owner with a counter and lock, and deletes undelivered alert files, without any listed approval. This is the same class of gap as R2#13. inputs.json lists 9 decisions while the design lists 6, so the approval scope is ambiguous.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:11`. Exact finding SHA256 `5ad24b88855f3fb5e317ff572ee04870495281a6b3cea3183d0c67ab6db37782`.

Author response (§1 complete decisions): Ten explicit human choices include paired backup non-atomic seam, local coalescing/remote limits and observer reset. Manifest mirrors full list.

## R3.6

The 30-minute backup copy runs on 'its maintenance lane', which §5 also names as the single 'maintenance/effect lane' and the only writer that publishes the cached effect aggregate. A long copy blocks effect evaluation and cache updates.

Failure scenario: A large journal copy holds the lane worker for up to 30 minutes. Effect-wake jobs queue behind it, the 30 s pass budget and the round-robin guarantees mean nothing, and the cached aggregate goes stale. The snapshot then reports effect health as unknown or stale, which contradicts 'cannot starve other jobs'. Either use separate lanes for copy and effect, or have the backup lane only record cycle state through the writer lane.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:110`. Exact finding SHA256 `fbdb787d9983c0e72505d2cbf659ece26b4aee214db2997bbac61dc1c0c3abd9`.

Author response (§§5/8 separate workers): Backup copy has its own bounded worker, never effect writer lane; bounded progress/result messages update cache without blocking effect fairness.

## R3.7

If observer state is lost or corrupt, all publication stops until a Marmot recovery installs a fresh epoch. But IDs are already namespaced by a fresh store UUID, so a new store cannot collide with old IDs. The hold buys no safety and stops all remote health alerting.

Failure scenario: The observer state file is corrupted, say after a crash on a filesystem without journaling. Only a service-journal diagnostic appears, which is terminal-only. The next WSD outage produces no Marmot alert until a human notices and runs recovery, and nothing remote prompts them to. A fresh UUID epoch could publish immediately with a fixed 'observer state reset' notice.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:92`. Exact finding SHA256 `94f491e436d7830ebb1e3bf97b07d61eb2839e2fd9d106e505fa8b7b48aeb1ed`.

Author response (§7 diagnostic reset): Observation loss automatically creates durable fresh random epoch and fixed reset retirement notice under proposed human design choice. Does not reset effects/authority. Reset identity is exactly `[1,installation_id,observer_epoch,"observer_state_reset",1,"retirement"]`, hashed as UTF8 compact JSON, ensure_ascii=false, no newline/full SHA256; ordinary tuples unchanged. Persist epoch/reset intent before publication and resume the same tuple/hash after crashes.

## R3.8

Episodes are keyed per install/generation/code, but the design never says what happens to an open episode when the generation changes. The old generation never sees an explicit false, so it never closes or sends a recovery notice.

Failure scenario: The owner switch (§10 steps 2–6) drains WSD, so stale or wake_overdue becomes true and opens G1 failure episodes, which publish alerts. WSD comes back under G2 and later predicates evaluate under G2. The G1 episodes stay open forever with no 'recovered' notice, and their files never settle, which also uses up shared capacity.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:92`. Exact finding SHA256 `2ed501443865d61024532e08d1905b01188c711670a922902865f4c8814ab864`.

Author response (§7 continuity): Episodes keyed installation/epoch/code retain opening generation; new config generation evaluates old episode actual predicate, unknown retains/false closes.

## R3.9

The 256 KiB snapshot cap is too small for the stated origin provenance: up to 64 request IDs per summary, queued/running/latest per kind, 2 kinds, 128 workstreams. The design does not say what happens when the cap is exceeded.

Failure scenario: 128 workstreams, each with a full 64-ID origin set for queued, running and latest-completed on both pickup and reconcile: about 128×2×3×64×39 bytes ≈ 1.9 MB. The snapshot either fails to write (heartbeat goes stale and a false episode opens) or is truncated in a way the design does not define. Limit snapshot summaries to count/first/last/truncated.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:88`. Exact finding SHA256 `a38cfeae27ada2143ce05b5c2c2a5ef1600925c7cc8e1baf68eb53c3b382869e`.

Author response (§7 bounded summaries): Snapshot includes count/first/last/truncated only, never64-ID arrays; max capacity test and compact explicit snapshot_error fallback.

## R3.10

wake_overdue starts counting at 'completed recovery'. At baseline the listener opens only after _startup's recoveries and all startup pickups. If recovery_status=complete is set before the startup pickups finish, wakes cannot connect and wake_overdue fires falsely.

Failure scenario: Recovery finishes quickly but startup pickups across many workstreams take longer than 130 s (2×60+10). Every pickup wake gets connection refused, so no admission is recorded, and the observer opens a wake_overdue episode and publishes a false alert. Define complete as listener-open, or keep wake_overdue unknown until the listener starts.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:90`. Exact finding SHA256 `f8d017ec11adaf18408fa7fa7c7e3f48ad56c393b8ea66cb62b4ab8536f0b570`.

Author response (§7 listener clock): wake_overdue unknown until listener opens after all startup pickups, deadline anchored actual listener-ready monotonic.

## R3.11

Plan8's shared settled-metadata schema (owner metadata with current/previous generation plus aggregate older counts) does not match the pinned 6C d3 §7.1 contract, which records 'namespace/episode/hash/settled flag only'. Yet both describe one shared helper.

Failure scenario: Two implementers build local_alerts.py metadata from different sibling designs. One writer's metadata dotfile fails the other's strict parse, so cleanup refuses or ignores foreign entries, and capacity accounting across owners diverges. Activation stays held under the 'byte-pinned fixture' rule, but the conflict should be resolved in the design text now.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:98`. Exact finding SHA256 `1852990852cd8b16b5cee3a505ed7596ce279a7de5dc940d90223f0d41d7d70d`.

Author response (§7 local-alert-v1): One explicit bounded metadata/API schema coordinated with 6C d4, exact mappings and shared fixture required. Old sibling mismatch disclosed, no false compatible claim.

## R3.12

The new wake request's `generation` field is a UUID string, but the legacy CtlRequest already has `generation: int | None` for resolve and sandbox-settle. Extending CtlRequest instead of adding a separate variant would fail on type, or loosen the legacy check.

Failure scenario: An implementer adds the scheduled fields to the existing msgspec CtlRequest. Decoding a UUID into generation:int returns 'malformed request' for every wake. Or the implementer widens the type to int|str, and resolve then accepts string generations. Require a separate decode struct, or rename the field.

Source: `docs/designs/2026-10-16-plan8-scheduling-reconciliation.md:54`. Exact finding SHA256 `06baaa0ab247858b14cbec5ea2da489a5f23e507c27e3f1942b83e08373d308d`.

Author response (§4 typed variants): Separate strict ScheduledWakeRequest/EffectWakeRequest/PairedBackupRequest; legacy integer generation decoding unchanged.

