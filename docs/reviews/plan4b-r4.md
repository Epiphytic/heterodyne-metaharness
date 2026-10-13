Reviewer: gpt-6.1-sol. Reviewed commit: 8897d156371b6a31803a2b82899838c9fe4be67a. Verdict: REVISE.

Note: joint review with ADR 0001 r16 round 2 (ADR commit 54a808d2b58b988d1dc31bd2e8add22a481faed7; the ADR record is 0001-design-review-r29.md in the ADR repo). Finding 3 is this plan's; ba51be9 fixes it. Findings 1-2 are ADR-side.

1. **[BLOCKING] Round-1 immutability finding remains open.** [ADR §7](~/repos/hermes-workstreams-v2-adr-r16/docs/adr/0001-workstreams-v2.md:682) and §17 item 13 explicitly permit same-uid host mutation after both checks. A host process can chmod a directory and insert another account’s credential copy, or replace a helper/plugin before it loads. The sandbox sees those changes immediately; taint detection up to ten minutes later cannot undo exposure or execution. This contradicts “the install can't change under it.” A read-only bind protects only the sandbox mount; fs-verity protects regular-file contents, not the directory namespace. ([Mount semantics](https://man7.org/linux/man-pages/man8/mount.8.html), [fs-verity documentation](https://www.kernel.org/doc/html/latest/filesystems/fsverity.html).)

   **Fix:** Require a protected snapshot covering files and directory entries throughout validation, binding and execution, including host-side reads. Otherwise explicitly narrow the guarantee to a trusted host user and describe this as accepted exposure risk; Appendix A.3 must not claim the original finding is closed.

2. **[BLOCKING] Approval can authenticate different bytes from the recorded baseline.** [Installation sequence](~/repos/hermes-workstreams-v2-adr-r16/docs/adr/0001-workstreams-v2.md:673) hashes staging before approval but records dev/ino/ctime afterward. Without mandatory protection, a same-uid writer can change a same-size file during that interval. The installer then records the changed file’s ctime, and subsequent metadata-only verification accepts bytes that do not match the approved manifest. Upstream verification and a digest-pinned approval do not close this gap.

   **Fix:** Protect staging before hashing and preserve that exact object through publication, or establish the recorded baseline while verifying all bytes against the approved manifest under enforced exclusion of writers.

3. **[BLOCKING] Task 12 still requires the retired binary configuration.** [Plan 4b live trusted-read test](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:5213) asserts `settings.binaries["codex"]` and directly constructs the old reader. It runs after 4c, when that configuration is rejected and the reader’s resolution contract changes.

   **Fix:** Update Task 12 to consume the selected verified install, and include its tests and direct reader constructions in 4c’s migration contract. The capability gates otherwise correctly prevent enabling the account features and trusted read before 4c.

4. **[NON-BLOCKING] The other round-1 findings are substantially addressed.** Installer-created nlink-1 copies remove surviving credential hard links; mount-ID checks reject same-device submounts; the private non-recursive bind and inside probe address hidden underlying trees and pre-bind root replacement. Strict link resolution and limit counting are now explicit. These checks remain point-in-time checks, subject to finding 1.

5. **[NON-BLOCKING] Authentication policy is coherent, subject to finding 2.** Published upstream verification, explicit trust on first install, manifest-pinned approval and prohibition of package scripts form a reasonable policy. The local-content exception means Appendix A.3 overstates that renamed credentials *cannot* enter a manifest: location and filename checks cannot establish that for arbitrary local content.

6. **[NON-BLOCKING] Clarify two implementation contracts in 4c.** Adapter-owned settings must prevail across `content/` plugin settings, hooks and MCP configuration as well as `defaults/`. Also pin install/digest when launch intent is journaled and replay that pin; selection changes should require a new generation. Version selection, rollback, removal protection and next-launch resume semantics are otherwise clear. Remaining ADR exception references are historical, and `.binary` references describe its retirement.

Verdict: REVISE