# P3 actual round 4 review dispositions

Claude Opus MEDIUM reviewed the entire signed range `b436af6f823852f68a48f85e2b53f9ac4056bc39..95a9541082c23623e1a04b5913e3f7ed0afef558` using built-in `/code-review` and the exact PoC question. The [actual report](plan5-p3-r4-poc-medium.md) approved the offline PoC and confirmed both R3 required repairs. It is retained byte-exact; no test or probe was run by the reviewer.

All ten findings remain OPEN and unwaived. No runtime source changed after this review.

1. **Sidecar replacement across instances — OPEN_OFFLINE_QUALIFICATION**. Private stable sidecar is not unlinked by this implementation. External replacement is not yet bound to persistent journal identity; do not qualify multi-process live ownership against replacement from this PoC.

2. **Decision/dispatch facts are fixture-supplied — OPEN_OFFLINE_ONLY**. Typed reducer eligibility is not authenticated durable native decision or dispatch evidence. No live adapter exists; callers cannot promote fixture facts into authority.

3. **Reducer refusal exception contract — OPEN_MAINTENANCE**. P1 Refused can escape prepare/issue; compatibility intentionally preserves explicit tests. Production integration must normalize or handle both refusal types.

4. **Detached public issue — OPEN_OFFLINE_ONLY**. Public issue remains intent simulation; supported execute/assess own the live lifecycle and all five native adapters are unavailable.

5. **Groom preparation concurrency/crash gap — OPEN_OFFLINE_ONLY**. Advice read and command/plan commits retain a crash/concurrency HOLD gap; no replay or synthetic outcome.

6. **Fork inheritance of owner descriptor — OPEN_OFFLINE_QUALIFICATION**. O_CLOEXEC covers exec, not a child that forks without exec. Such children can prolong owner-busy after parent death; no automatic reclaim.

7. **Restrictive umask sidecar creation — OPEN_OFFLINE_QUALIFICATION**. Exact0600 sidecar validation can refuse restrictive-umask creation. This is an availability HOLD, not a permission to chmod existing unknown artifacts.

8. **Repeated cancellation fixture — OPEN_MAINTENANCE**. Three explicit fixture inputs are deliberately retained with original assertions and archived original files; no shared helper migration in this patch.

9. **Nonce validator duplication — OPEN_MAINTENANCE**. P3/P2 nonce checks remain duplicated; valid configured contracts match today, drift risk retained.

10. **Catalogue and lock-path duplication — OPEN_MAINTENANCE**. Operation/phase catalogue and sidecar path reconstruction remain duplicated. Measured quality debt remains unwaived.

Fresh signed reciprocal product/Q1 acceptance is still required before task completion and merge. All five live adapters remain unavailable. This is not production qualification or deployment authority. Configured795 compatibility tests and379-file0/0 typing remain the author evidence; the whole CI suite is separate and pending. Quality11 gates remain OPEN; no acknowledgements or baseline reset.
