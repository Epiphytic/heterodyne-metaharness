# P3 round 3 author dispositions

Actual review: Claude Opus, medium reasoning, signed candidate `11e21592b10b9b2f63a3f7d3003bab94008ace86`.
Question: "Are there any critical flaws with this, or is this good enough to implement as a PoC?"
The actual report found no critical offline PoC flaw, while requiring findings 1 and 2
before Q1 pair acceptance. The byte-exact report is [archived here](plan5-p3-r3-poc-medium.md).
Author repairs below await the separately authorized follow-up; this is not an APPROVE verdict.

1. **FIXED** — Actual P1 settle_request eligibility runs transactionally in prepare and again in _issue before any native port call. It does not fabricate a terminal readback; _reduce still validates actual terminal proof. Three rejected cleanup phases plus issue-time reducer refusal have behavioral RED evidence.

2. **FIXED** — Owner flock now uses a separate persistent private regular single-link .owner.lock inode, rejecting symlinks, hardlinks, public mode and replacement. Never open/close a raw database descriptor for flock. A real disposable SQLite BEGIN IMMEDIATE / competing-thread / child-process regression demonstrates lock loss before the fix.

3. **PARTIAL_OPEN_OFFLINE_ONLY** — Public mark_unknown takes ownership and refuses a supported live port/provider callback; private internal _mark_unknown handles owned failures. Public issue retains its original offline intent-simulation API and tests. It is not a supported live dispatch API; execute/assess own the complete supported live lifecycle. Its detached simulation surface remains OPEN, with all live adapters unavailable.

4. **OPEN_OFFLINE_ONLY** — Preserve the existing positive oracle: uncertain allocated/blocker-pending/parked/landed cancellation can use P1 existing booleans without a CancellationRecovery. P3 does not authenticate those fixture observations. Ask-pinned and later cleanup uses the actual typed authenticated P1 recovery requirement. Do not infer production cancellation authority from this fixture-only behavior.

5. **OPEN_OFFLINE_ONLY** — prepare_groom still derives advice outside one owning transaction and persists command/authorization in two commits. A concurrent change or crash can leave a self-induced HOLD/uncertain effect; there is no automatic replay or fabricated success. Atomic grooming preparation is deferred, not silently waived.

6. **OPEN_OFFLINE_ONLY** — One nonblocking journal-wide lease serializes supported effects and provider await. Unrelated work is busy while an owner is hung/slow. Callers must yield/retry under external scheduling, not busy-poll. Per-request concurrency is deferred; no expanded orchestration protocol is introduced.

7. **FIXED** — SQLite open uses Path.resolve().as_uri() plus mode=rw. Reserved ?, # and % path regressions reopen the exact validated journal.

8. **OPEN_MAINTENANCE** — Strict Cleanup decoding intentionally revalidates untrusted persisted payload at prepare, issue and reduce boundaries rather than caching authority across restart. The allowed-operation set and phase catalogue remain duplicated; future edits must update both. No quality finding is acknowledged or reset.

The original thirteen regressions failed behaviorally on the signed candidate. Three
existing positive fixture inputs now supply the typed authenticated cancellation P1
already requires; all original assertions are AST-identical. Original files, hashes,
diffs and RED/GREEN receipts are retained in external task evidence. No reducer,
unknown-outcome rule, live-adapter restriction, approval or capacity oracle was weakened.

All five live adapters remain unavailable. Fresh signed Q1 pair acceptance is OPEN
before completion. Quality debt remains unwaived. No deployment is authorized here.
