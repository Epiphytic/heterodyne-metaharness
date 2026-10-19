# Plan9 first HIGH author dispositions

All ten original findings are retained verbatim in review-high.md and dispositions.json. The first review is REVISE; these repairs await the second full HIGH review. Performance debt remains OPEN UNWAIVED. No operational collector, real observation or release is asserted.

1. Fixed: sealed commit invokes the existing evaluator against the exact fenced prefix, assessment coverage/additions/unresolved state and requires a qualified result with identical derived duration. No terminal manifest is written on replay failure.

2. Fixed: compare full projected Component records across drill and release identity, covering both kind and content digest; new adversarial regressions.

3. Fixed: begin validates the segment ID and rejects a retained terminal manifest before creating the active fence, preserving an unfenced ledger on retry.

4. Fixed: archive requires explicit authenticated event_bytes, unique matching Stop, exact start/end/checkpoint bindings and interval replay with all seven coverage rows; no event input HOLD. Original archive fixtures preserved and narrowly extended to supply the previously missing event evidence.

5. Fixed: abort loads and hashes the exact persisted intent. An omitted argument adopts those bytes; a different supplied intent refuses before writing an abort receipt. A newly supplied intent is first bound to this fence and retained.

6. Fixed: publication and release acquire publisher then spool lock, hold the exact validated snapshot through publication/unlink, and persist exact spool IDs in typed Manifest/Release records. Admitted source-completeness proof remains mandatory; filenames alone do not certify source reconciliation.

7. Fixed: native_utc accepts explicit RFC3339 with zero to six fractional digits only, avoiding datetime precision truncation and lenient calendar forms. Raw originals stay unchanged.

8. OPEN UNWAIVED: full chain validation and repeated inventory decoding remain bounded but quadratic/expensive; no performance or production collector claim. Do not cache unverified source state or claim a short lock based on synthetic tests.

9. Fixed: failed preparation originals are retained in separate private bounded quarantine directories outside the admitted envelope/object namespaces and their quotas; each quarantine has a 1024-entry HOLD limit. Legacy diagnostic files remain retained and excluded. Successful rename fsyncs both directories.

10. Fixed: heartbeat, abort and release use the active-fence reader that converts missing fence into typed no_active_fence HOLD.

Archive fixture supersession retains the full original bytes/hash and exact diff in autonomous-resume/drl4m-r1-repair. The graph now supplies the actual synthetic event/checkpoint chain and matching Stop, preserving the original success and negative assertions. All original ledger tests are unchanged. Corrected isolated original-source evaluator RED has nine actual failing assertions; earlier fixture/import failures are retained separately.
