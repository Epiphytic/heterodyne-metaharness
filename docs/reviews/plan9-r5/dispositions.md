# Plan9 fifth actual review

Actual Claude Opus built-in `/code-review`, MEDIUM, approved source 6210ca7 as an offline PoC. This is cumulative round five; the first two HIGH and later MEDIUM reviews remain retained. The exact critical-flaws question was used. All nine new findings and all earlier findings remain OPEN and unwaived. No code changes were made after this review.

1. **OPEN_UNWAIVED — hash_budget_reason**: Cumulative read-budget Hold is caught through ValueError and reported as tree_unavailable. The cap still stops reading, but its advertised reason is inaccurate. Read-time overrun regression remains missing.

2. **OPEN_UNWAIVED — versions_runtime_shape**: Typed versions tuple has no runtime shape guard; a string caller can get substring membership. Only correctly configured typed callers are in this offline PoC scope; no authority is admitted.

3. **OPEN_UNWAIVED — root_fd_runtime_shape**: Invalid non-int or overflowing fd can escape as raw ctypes/OverflowError rather than typed HOLD. The documented error-containment claim has this limitation.

4. **OPEN_UNWAIVED — manifest_semantic_coverage**: Entry equality does not establish executable entry_point, target or sources semantics. Projection describes only sampled entries, not usable installation validation or acquisition/loaded provenance.

5. **OPEN_UNWAIVED — read_time_budget_oracle**: The existing file-byte negative exercises declared-size preflight only. No claim that cumulative read-budget enforcement has a regression oracle; the documentation hash-budget coverage wording is too broad.

6. **OPEN_UNWAIVED — walk_cost**: entries_of walks and hashes more than rehash would; work is bounded by owner limits, without a latency or manifest-only-read claim.

7. **OPEN_UNWAIVED — public_projection_constructor**: A public dataclass is not unforgeable evidence. Consumers must not treat it as authorization; require_authority always HOLDs.

8. **OPEN_UNWAIVED — metadata_reason_catch**: Canonical-record Hold is redundantly caught and rewritten through ValueError. Reason distinctions are not preserved for future changes.

9. **OPEN_UNWAIVED — adapter_literal_drift**: The finite codex/claude literal is not shared with the adapter registry. Future adapter evolution needs an explicit synchronization change.

Verification before review: 340 configured acceptance/store compatibility tests passed; full configured typing checked 387 files with zero errors and warnings. All 243 preexisting test files are byte-identical to the preceding reviewed source. Quality reported 15 observations and four gates, all open and unwaived. The 24 new tests are offline fixture checks. They do not establish authenticated acquisition, loaded provenance, full manifest operational semantics, operational collectors, runbooks, source-complete corpus, drills or a qualifying 72-hour clock. The evaluator remains unavailable for operational evidence and require_authority always HOLDs. This partial implementation does not complete the Plan9 Bead.
