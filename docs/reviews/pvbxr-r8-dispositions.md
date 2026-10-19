# Actual eighth MEDIUM review: offline PoC only

The actual Claude Opus /code-review used the exact critical-flaws question at MEDIUM effort. It found no critical flaws in the offline source-only PoC and confirmed the two R7 critical gaps closed. It did not issue an APPROVE token or clear production. First two FULL HIGH reviews and all subsequent actual ordinals remain preserved. No new repair is bundled with this review record.

1. Legacy receipt followed by metadata can retain protected active capacity with no supported recovery. OPEN owner/recovery obligation.
2. Answered V2 cards cannot use V1 fulfillment and require explicit cancellation until a separately designed V2 lifecycle. OPEN owner integration blocker.
3. Metadata NULL round-trip may restore an old stamp without generation change. OPEN; no mixed-writer or live clearance.
4. Metadata added after propose leaves the old intent pending until expiry. OPEN lifecycle cleanup.
5. Pre-repair processes accept unchanged schema and can still fulfill V2 rows. OPEN deployment blocker: retire incompatible writers and qualify rollout/rollback before activation.
6. The composition fulfilled/dirty names now cover refused V2 lifecycle, not actual V2 receipt transitions. OPEN coverage limitation; separate direct-SQL receipt-plus-metadata regressions retained.
7. Extra legacy guard SELECT and repeated checks. OPEN noncritical performance debt.
8. Unqualified predicate columns depend on current schema names. OPEN noncritical maintenance debt.
9. Ask list combines separate snapshots. OPEN from R7.
10. Reminders use raw receipt exclusion rather than active projection. OPEN from R7.

R7 findings4–7, R6 findings1–9, earlier unresolved findings, and cumulative quality debt remain OPEN and unwaived. All production queue adapter, independently enrolled reader, native permission/receipt, V2 lifecycle and deployment requirements remain HOLD.

Verification remains 19 predecessor regression failures,19 repaired passes,474 focused passes, configured typing412 files zero errors/warnings, scoped lint/format/diff passing. The interrupted predecessor-started all-admind run produced26 timeouts and2036 passes before SIGINT; it is not full-suite evidence for this source. A fresh stable-source full run is in progress; no full pass or merge is claimed. Standalone earlier2477-pass result applies only to its recorded source.
