Reviewer: gpt-6.1-sol. Reviewed commit: f40feb137e5f9dd97e293f8e1ebbb55ea8f10092. Verdict: REVISE.

Note: cycle 1 of 3 of the cross-model design review (author claude-opus-5-5).

The design bounds the ordinary sweep-resume loop, but two paths can erase or bypass crash accounting.

1. **[BLOCKING] Passing the deadline is not proof of a healthy run.**  
   [Design:26](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:26), [runtime.py:196](~/repos/heterodyne-metaharness-v2-g08sd/src/heterodyne/sandbox/runtime.py:196), [runtime.py:631](~/repos/heterodyne-metaharness-v2-g08sd/src/heterodyne/sandbox/runtime.py:631).

   A generation can die immediately, remain unobserved during a wsd/backend outage, and be discovered after its deadline. `_overdue` then classifies it as `lifetime`, clearing earlier crashes. More directly, the proposed rule upgrades an already recorded `died` to `lifetime` if cleanup crosses the deadline. Repeating either sequence permits unlimited relaunches. The reaper currently records no evidence distinguishing its deletion from an earlier death.

   **Fix:** Keep the crash classification immutable and track lifetime WIP obligations separately. Reset only with trusted evidence of a runtime-initiated stop while the generation was confirmed live; treat an ambiguous overdue absence conservatively. Add tests for delayed discovery, cleanup replay crossing the deadline, and repeated outage/death cycles. Change proposed test 2, which currently requires the unsafe override.

2. **[BLOCKING] Accounting only in `open_resumes` misses deaths consumed by park/defer.**  
   [Design:165](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:165), [park.py:342](~/repos/heterodyne-metaharness-v2-g08sd/src/heterodyne/wsd/park.py:342), [park.py:461](~/repos/heterodyne-metaharness-v2-g08sd/src/heterodyne/wsd/park.py:461).

   A PARK/defer operation can open before the sweep observes a death. Its `_sessions()` call discovers and ends the dead generation, but the operation then finishes PARKED/DEFERRED without counting it. The sweep skips open operations and later preserves waiting rows. Resume/undefer launches another generation and overwrites the runtime evidence. This can repeat, including through recovery replay. Preserving an existing count is insufficient when these deaths never increment it.

   **Fix:** Account each prior launched generation before any replacement launch, through the common launch guard or an equivalent durable settlement step. Preserve intentional stops without counting them, and distinguish operator resets explicitly. Test death before park/defer execution, crashes during their replay, and subsequent resume/undefer; assert escalation before a third launch at the default limit.

3. **[NON-BLOCKING] Legacy lifetime records become false crashes.**  
   [Design:73](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:73), [Design:118](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:118).

   Existing ended records can have `stop_reason="lifetime"` and `started_at`, but lack `ended_at`. They produce `ran_seconds=None`, so `_death` counts them as crashes despite the trusted lifetime marker. `ran_seconds` conflates “never ran” with “duration unavailable.”

   **Fix:** Represent generation execution evidence separately from optional duration. Define and test upgrade behavior for legacy lifetime records, empty causes, and incomplete stopping records. Do not use `ran`, which persists across generations, as generation-specific evidence.

4. **[NON-BLOCKING] Meta-row retention is unspecified.**  
   [Design:79](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:79).

   The limit bounds rows per active crash episode, but closed or deleted beads retain their rows indefinitely. Prefix queries using `substr` also scan accumulated metadata.

   **Fix:** Specify terminal/deleted-bead cleanup or explicitly defer it to journal retention work. Cleanup must retain evidence needed by open operations and must not reset a recoverable claim. Add a cleanup-isolation test.

5. **[NON-BLOCKING] “No ADR amendment” needs a narrower justification.**  
   [Design:3](~/repos/heterodyne-metaharness-v2-g08sd/docs/superpowers/specs/2026-10-13-g08sd-crash-loop-accounting-design.md:3), [ADR:918](~/repos/hermes-workstreams-v2-r15/docs/adr/0001-workstreams-v2.md:918).

   The two-failure bound implements §§9–10 and needs no architectural amendment. However, lifetime resets and counting reboots are additional policy choices; the ADR separately describes reboot recovery.

   **Fix:** Record these as explicit accepted policy clarifications, including the corrected lifetime evidence rule, rather than claiming the ADR already establishes them.

The proposed journal transaction is sound: nested `escalation_for` transactions join the outer transaction, so count, STUCK state, and escalation intent commit together. Receipt replay also closes the runtime-record/journal gap conservatively: a missing receipt holds instead of relaunching, while a started receipt settles to RUNNING for subsequent accounting. The separate refused-launch budget remains intact. The sandbox binds support the claim that agents cannot directly write `session.json`; the blocking weaknesses concern interpretation and consumption of trusted evidence.

Read-only review; no files changed. The proposed tests need the adversarial sequences above before implementation approval.

Verdict: REVISE