# AU-3 launch entries design review r2 (cycle 2 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Mode: cross-model. Reviewed commit: f054601. Verdict: REVISE.

1. **[NON-BLOCKING] R1 #1 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:62` now reuses AU-2’s credential-key formula and environment-based expansion. No further formula fix needed; launch-time rechecking has a separate problem below.

2. **[NON-BLOCKING] R1 #2 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:304` completes the operation’s own dispatch from its receipt, restores the native ID, and avoids another dispatch. No further fix needed.

3. **[NON-BLOCKING] R1 #3 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:365` removes fabricated outcomes on release. Keeping a dispatched, unresolved entry held pending a future approved recovery procedure is acceptable under r14 D2; §4.3 release cannot override receipt-only outcomes. No further fix needed.

4. **[NON-BLOCKING] R1 #4 — Recovery transition added, but not fully resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:265` preserves original adoption evidence and adds journaled resolution. The new transition has authority and parked-path defects described below; those need fixing.

5. **[NON-BLOCKING] R1 #5 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:250` specifies atomic handoff of an open pickup/resume and reuse of an existing escalation. No further fix needed.

6. **[NON-BLOCKING] R1 #6 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:213` explicitly holds missing or unreadable legacy records rather than treating them as first launches. No further fix needed.

7. **[NON-BLOCKING] R1 #7 — Resolved.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:317` implements reconstruction from bead entries while keeping receipts journal-only. No further fix needed.

8. **[NON-BLOCKING] R1 #8 — Acceptable interim behavior retained.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:333` safely escalates `AccountChanged` within AU-3’s staged scope. Keep the explicit AU-4 replacement requirement: this is not the completed r14 D5 behavior.

9. **[BLOCKING] New — Release replaces a required historical fact with current configuration.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:270` changes “no configured accounts at upgrade” to “no configured accounts now,” then adopts with today’s default key. Removing accounts today does not establish which login the legacy session used. Both r14 D2 and PICKUP.md explicitly require the upgrade-time condition; retaining it in `facts` does not help if resolution ignores it. **Fix:** preserve that historical prerequisite during release. A session held because accounts existed at upgrade needs an explicitly authorized account/history verification procedure; removing accounts alone must not authorize adoption.

10. **[BLOCKING] New — Rehashing AU-2’s resolved `Account` can miss credential repoints.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:72` says to recompute from the returned `Account`, but AU-2 stores canonical `login_dir` and `login_files`. Once `~/.codex → /login-A` has been resolved, repointing `~/.codex → /login-B` is invisible when rehashing those stored paths—or resolving files beneath the stored canonical directory. Implementers could therefore either detect or miss the same change. **Fix:** specify fresh resolution from the original configured/default path and supplied environment before hashing with AU-2’s `credential_key`; test both directory-symlink and file-symlink repoints without reload.

11. **[BLOCKING] New — Adoption release has no specified parked-bead verification path.** `docs/superpowers/specs/2026-10-08-au3-launch-entries-design.md:267` places verification after `_record_for_release` while retaining plan 3’s release flow. Existing `src/heterodyne/wsd/park.py:357` returns immediately for a parked bead, before that function. A parked adoption hold can therefore report a successful release without resolving adoption, then re-escalate when resumed. **Fix:** explicitly verify and resolve adoption before the parked/unparked branch, ensuring record recovery where necessary; preserve blockers and parking afterward. Add a parked-adoption release/replay case.

REVISE