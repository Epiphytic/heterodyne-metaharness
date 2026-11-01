## PR #156, second full HIGH review (R2) at frozen HEAD `7e9c0f7`

**Run details:** I ran the built-in `/code-review` skill with the explicit args `high 156` on Opus 5.5 (`claude-opus-5-5`) at high effort. The PR is OPEN and its head matches local HEAD (`7e9c0f7`). The review was read-only. I posted no comments and made no edits, pushes, service changes or test runs.

**Verdict: no blocking defects found.** Both R1 blockers are fixed in the source: the enrollment group is now bound to the producer group, and the exact bytes whose digest was checked are the ones parsed. R2 found no new critical, security or correctness defect in scope (private parent transport and pure correlation). It returned 10 findings, all hardening, test-coverage or cleanup items. None of them requires the rest of the project.

### Should fix (not blocking)
1. **The binding checks have many untested mutations.** In `tests/test_deployment_admission_binding.py:70`, no rejection case changes the startup, installation index or source digests, `capability_template`, `original.ask_id`, `original.operation_canonical`, or the receipt's `ask_id`, `packet_sha256`, `request_sha256` or `operation_sha256`. It also never changes `consumption_key.ask_id` or `consumption_key.header_sha256`. Deleting any of those comparisons from `_correlate` would leave the suite green. The source checks are present today; only the coverage is missing.
2. **The peer namespace probe is too permissive.** `tests/test_deployment_admission_peer.py:46` accepts any `OSError` or any `InvalidAction` as a correct rejection. A removed guard could still "pass" through an unrelated timeout or EINVAL.
3. **The 30-second timeout raises an untyped error and affects FD4.** `deployment_admission_peer.py:140`: `settimeout(30)` makes a stall raise a raw `TimeoutError` instead of an `InvalidAction`. It also sets O_NONBLOCK on the OFD it shares with FD4. R1 already listed this as non-blocking and it is still unrepaired.
4. **Nothing checks that the parent still holds the FD5 lock.** `deployment_admission_peer.py:113` checks only metadata and the stat key. This matches the stated design, where the parent owns the same OFD lock continuously and the child never relocks it. R1's suggestion still applies and is consistent with that design: reopen `/proc/self/fd/5` and run a read-only `F_OFD_GETLK` probe, which takes no lock.
5. **Two receipt fields pass through unchecked.** `deployment_admission_binding.py:51`: `receipt.authenticated_candidate` (any JSON) and `consumption_key.header_sha256` (format-checked only) are returned inside `_Consumed`. This is fine while the scope is pure correlation. Any later journal or permit step must not treat them as evidence.
6. **Two failures escape as raw `OSError`.** In `deployment_admission_peer.py:56`, `F_GET_SEALS` on a non-memfd nlink==0 file (such as an O_TMPFILE) raises EINVAL. `os.fstat(5)` in `check()` does the same if FD5 has been closed.
7. **Duplicate operators are accepted.** `deployment_admission_binding.py:167` accepts `[X, X]` in the enrollment operator list and hashes it into the semantic policy digest.

### Cleanups
8. Each correlation decodes the packet three times and encodes it twice (`binding.py:108`). The local `canonical` value duplicates `expected.operation_canonical`.
9. The 64-character hex check is hand-written three times, although `deployment_root_helper._HASH` already exists.
10. The FD5 lease custody check repeats `deployment_root_helper._read_lock`.

I checked findings 3 and 4 against the source and the R1 findings and disposition files in `docs/reviews/2026-11-01-deployment-admission-peer/r1/`. R1 also classed both as non-blocking hardening, so this verdict is consistent with it.

I wrote no R2 report or disposition file because the review was read-only. If you want this run recorded under `docs/reviews/.../r2/`, that needs a separate edit.
