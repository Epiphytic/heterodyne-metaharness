# AU-3 launch entries design review r3 (cycle 3 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Mode: cross-model. Reviewed commit: c6c83d1. Verdict: APPROVE.

1. **[NON-BLOCKING] R2 blocker 9 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:282` preserves “no configured accounts at the upgrade.” Removing accounts later cannot authorize adoption. **Fix:** none required; account-history recovery can remain deferred.

2. **[NON-BLOCKING] R2 blocker 10 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:78` requires fresh resolution from the original configured/default path and supplied environment, detecting directory and file symlink repoints without reload. It reuses AU-2’s credential-key function. **Fix:** carry the requested `Account.configured_dir` field into AU-2. This is a compatible additive change alongside its canonical paths and key.

3. **[NON-BLOCKING] R2 blocker 11 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:285` places record recovery and adoption verification before the parked/unparked branch. Line 455 covers parked release and crash replay while preserving blockers. **Fix:** none required.

4. **[NON-BLOCKING] Interim AccountChanged escalation is acceptable for AU-3.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:352` prevents an unsafe credential switch. It explicitly requires AU-4 to replace this safety stop with r14 D5’s deferral and startup/reload/release checks. **Fix:** retain that AU-4 requirement.

5. **[NON-BLOCKING] No-receipt holds are acceptable under r14 D2.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:384` keeps dispatched entries unresolved, blocks later gating, and refuses release without receipt evidence. Deferring an approved recovery procedure preserves the authority’s required safety behavior. **Fix:** none required for this PoC.

6. **[NON-BLOCKING] The design is good enough for a proof of concept to implement.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:246` specifies an atomic upgrade without `executescript`; line 323 completes receipt-backed replay without another dispatch; line 306 preserves entries and detects conflicts. I found no remaining blocking contradiction, duplicate-launch path, guessed-account launch, silent entry loss, or half-applied upgrade in the specified flow. **Fix:** implement the stated crash and recovery acceptance cases.

APPROVE
