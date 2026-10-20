# Supplemental MEDIUM 5: single-GC CI test correction

The built-in review found no critical flaw; the wrapper approves a source-level PoC. All four new findings remain OPEN, as do prior review debts. The original two HIGH and MEDIUM 3/4 reports and full findings remain unchanged in the parent directory; exact copies are additionally retained in the encoded evidence. This supplements that lineage, without resetting it.

Only the GC closure inside the hostread leaked-FD test changes: collect on the first simulated sleep, then continue advancing the fake clock. All assertions, existing calls and other top-level AST remain exact. Production source and the sleeper's 60-second cap are unchanged. Root retains publication/commit authority.

Qualification: the full hostread module passed 92 tests in 40.57 seconds using the publication checkout's CPython 3.14.7 and local source. Ruff passes; scoped Pyright has zero errors/warnings. Candidate and original both fail the existing broad format check; no formatting was performed. No local predecessor RED was reproduced. The repeated-GC explanation is inferred, with no measurement proving the failed CI test reached 60 seconds. The reviewer also identifies missing/released lifeline alternatives and remaining cap/finalizer dependence.

Root reports failed PUSH CI run 39414406292 (8932 passed, one failure, 103 skipped, 12 subtests) and successful PR run 39414438285 on identical f092b8e6c43e0515301bc79a0a7ad60afc3c294d. This packet retains the failed log losslessly and pins it through the original proof's dependency manifest; independent CI receipts are not supplied here and must be attached by the controller. A successful run on f092 does not test this later candidate or prove a flake eliminated.

This test-only change needs a new exact commit target and mechanical deployment binding addendum. No activation, deployment, CI retry, task closure or approval is granted here. Existing target authority does not silently carry over.

Readable review bodies normalize the operator's home prefix to `<operator-home>` only. Exact originals are losslessly recoverable under `raw/`; raw snapshots ending in `.py` are encoded data, not runnable public code. Decode base64, gunzip, and verify each of the three hashes/sizes in raw-manifest.json. The inert decoder text below is supplied for inspection; no decoding was executed by the publication process. Transport streams/start metadata are omitted with hashes/reasons, rather than duplicated into the public record.
