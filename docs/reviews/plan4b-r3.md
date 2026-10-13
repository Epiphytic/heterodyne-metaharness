Reviewer: gpt-6.1-sol. Reviewed commit: 91c28fa1127fc9a72a5e4dfe6952617ff999ba5b. Verdict: REVISE.

Note: cycle 3 of 3, the cap. The author fixed blocking findings 1-2 and non-blocking 3 and 5 in 7712156; finding 4 is accepted as written (the exhausted Codex case skips as not demonstrated). The controller verified the new recover/_holders/_end_holders by executing them with injected faults (pidfd EMFILE, unreadable /proc, a failed kill, the deadline): each kept the home, logged DIAG_RECOVERY_KEPT, leaked no fd and raised nothing, and a second recover then killed the orphan and removed the home.

Cycle-2 findings **1, 2, 3, 5, 8 and 10 are fixed**. Finding **4 remains blocking**. Finding **6 is fixed for ordinary transient failures**, with a bounded-queue limitation below. Findings **7 and 9 improved**, with non-blocking validation gaps.

References below use `docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md` at `91c28fa`. No files were modified.

1. **[BLOCKING] [plan:4083](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4083) — Recovery treats incomplete inspection as proof of absence and can erase its recovery marker.**  
   `_holders` ignores every `pidfd_open` failure. With `EMFILE`, it returns no holders; recovery deletes the credential scratch and logs `DIAG_RECOVERED` while an orphan still runs. Separately, when a holder remains past the deadline, recovery still removes its home. The next startup discovers homes first, so it can no longer identify that orphan. I reproduced both branches by executing the proposed functions in memory with injected failures.  
   **Fix:** distinguish vanished processes from inspection failures. Retain a durable ownership marker whenever inspection or termination is unconfirmed; remove it only after verified termination. Add tests for failed pidfd acquisition and a holder surviving the deadline.

2. **[BLOCKING] [plan:4120](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4120) — Recovery can raise and leak descriptors and the instance lock.**  
   `pidfd_send_signal` suppresses only `ProcessLookupError`. A signalling `PermissionError` escapes before closing that fd or subsequent collected fds. `/proc` enumeration can also raise. Despite the “never raises” claim, [plan:4234](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4234) places recovery before `cli.run` enters its lock-release `try/finally`. Injecting a signalling failure reproduced the escaping exception with no descriptor cleanup.  
   **Fix:** close all acquired pidfds in `finally`, handle recovery errors with the fixed diagnostic while preserving ownership evidence, and call recovery inside the instance lock’s cleanup scope. Test enumeration and signalling failures.

3. **[NON-BLOCKING] [plan:4647](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4647) — The corrected WIP ordering needs corresponding oracle changes.**  
   Confirmation changes `op.data["defer"]` from 1 to 2 while retaining the SHA committed under `defer:<key>:1`. Existing `wip_of` then searches for the mark numbered 2 and fails. Also, [plan:4903](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4903) compares raw credential keys across independently rooted rigs; their path-derived keys differ.  
   **Fix:** preserve the committed record number for WIP verification. Validate each attempted credential key against its own rig, then compare normalized launch specifications across runs. The implementation ordering itself is correct.

4. **[NON-BLOCKING] [plan:5669](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:5669) — The exhausted Codex case still provides no live signal evidence.**  
   The observational fixture avoids the previous launch assertion, but skips when Codex’s self-test is refused. That is acceptable for the gated proof of concept, but cannot demonstrate capability (e).  
   **Fix:** retain the skip as “not demonstrated”; before enabling the capability, observe a successfully started session reaching its limit.

5. **[NON-BLOCKING] [plan:4704](~/repos/heterodyne-metaharness-v2-plan4b/docs/superpowers/plans/2026-10-12-plan4b-accounts-usage-failover.md:4704) — Held-report overflow can discard the failed report.**  
   During an outage, more than 256 pending limits causes the oldest reports—including the one that failed—to be dropped. The ordinary outage test correctly exercises replay, but the unconditional “loses no report” claim is too strong.  
   **Fix:** document the overflow limitation for the proof of concept; preferably deduplicate by launch before applying the bound.

Verdict: REVISE