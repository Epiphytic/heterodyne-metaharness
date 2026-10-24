# Ordered review dispositions

The linked raw archive preserves the actual R1 and R2 reports and separate wrappers verbatim. Wrapper categories are not represented as severity labels supplied by the actual reviewer.

R1's nine ordered actual findings and separate wrapper categories remain in the raw archive. The R2 actual report explicitly confirmed the nine R1 repairs; the archive, rather than a reordered author summary, is the authoritative original order.

R2's nine ordered findings:

1. Distinct maintenance/recovery-control lock owners: fixed; actual-method lock tests after finished startup recovery fail against the predecessor and pass on the candidate.
2. Mandatory reply wire schema: fixed, retaining convenience encoder behavior.
3. Exact unavailable tuple: fixed, including empty/partial/duplicate refusal cases.
4. Separate custody authorization-drop accounting: fixed for the normal intake route. R3 identifies an inherited fallback discrepancy below.
5. Component-specific allowed phases: fixed at emission and decode boundaries.
6. Retained fallback custody authorization count: fixed for its tested valid-command path; remaining malformed/guard fallback detail is R3 finding1.
7. Dead availability field: removed; bind-refusal tests retain real observable main-socket/foreign-inode assertions.
8. String-based task wiring: OPEN_UNWAIVED; not a PoC blocker.
9. Real send-path deadline qualification: PARTIAL_OPEN_UNWAIVED; timing tolerance relaxed, actual send remains unqualified.

R3's seven ordered minor findings are all OPEN_UNWAIVED: fallback custody malformed/guard counter discrepancy; hand-written component phase table/type coverage; defaulted honesty markers on decode; coarse CLI unavailability diagnostics; duplicate custody command parsing; string-based task registration; and missing actual send-under-timeout test. The complete actual objects are in r3-open-findings.json. No further source changes were made to chase these findings.
