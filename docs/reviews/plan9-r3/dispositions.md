All ten R3 findings plus prior F9 remain OPEN UNWAIVED. Offline core PoC accepted; production collectors, CLI, proof adapters, source cursor semantics, operational migration and72-hour corpus remain outstanding.

1. Repeated content-identical spool arrival after retirement can be coalesced; no lossless per-arrival ingestion claim. Must resolve duplicate/cursor semantics before live collector admission.

2. Commit intent may persist before final proof refusal; correcting requires abort/new attempt. Availability debt, not replay permission.

3. Repeated abort with changed intent can leave additional retained intent before immutable conflict; no mutation of terminal abort allowed.

4. Post-release outside-segment tail requires an explicit archived qualification prefix; current evaluator does not infer that cut. Production collector must retain exact prefix.

5. Typed IO diagnostics cover original reads and directory enumeration; lock/write/fsync OSError can still escape and must be contained by future operational adapter.

6. Private undeployed fence/directory shape changed without live migration. Do not open or reinterpret historical live ledgers; operational migration not implemented.

7. Interleaved boot monotonic regression is not caught by adjacent-only check; no full boot continuity claim.

8. Extra post-fence retry state remains cleanup debt.

9. Duplicate proof/inventory replay remains F9 performance debt, no short-lock or72hourthroughput claim.

10. Retirement-history scans grow per spool call; bounded capacity is not production ingestion performance.
