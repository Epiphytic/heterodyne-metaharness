**Verdict: changes requested.** I ran the built-in `code-review` skill at HIGH on the full seven-file diff (`ee87166`…`ca6a2ac`). Two findings block merge, and I reproduced both in a scratch copy made with `git archive`, which I've since deleted. The other findings come from reading the code and weren't run. No host deployment is authorised and the host opener stays on HOLD.

**Round-1 repairs**
- **Fixed:**
  - #1: the packet URL is now bound to the Release through `packet_commit`/`packet_path`/`_packet_url`.
  - #3: the interpreter is hashed as a stream, with a 128 MiB limit, so the 32 MiB interpreter is no longer refused.
  - #4: install portability is handled by per-line markers plus an exemption tied to the resource hash.
  - #5: the core loads lazily and fails as an `AuthorityHold`.
  - #6: msgspec aliases are checked against `_ALIAS`.
  - #8: cancel refuses unless `rowcount == 1`.
  - #9: the policy is parsed from the bytes captured in the first sweep, not reread.
- **Partly fixed:**
  - #2 (import/bytecode closure): see findings 1, 2, 5 and 6.
  - #7 (typed SQLite errors): `IntegrityError` is now converted, but "database is locked" errors are not (finding 7).

**Blocking**
1. **The bytecode check fails the harness's own pin.** `src/heterodyne/actions/deployment_runtime.py:85`: `tree_digest` refuses any `__pycache__`, and importing `heterodyne` normally writes one into `src/heterodyne/actions/`.
   - Without `PYTHONDONTWRITEBYTECODE=1`, as in CI, the focused tests give 30 passed and 1 failed. `test_native_capture_fixed_reader_seam_and_two_sweeps` fails at `tests/test_deployment_step1.py:254` with `runtime-bytecode-refused`.
   - With it set, as in my environment, all 31 pass. So the 31-pass result depends on that variable being set.
   - A real harness that imported the package normally would hold on every `_capture`.
2. **Pins are dropped when two roles resolve to the same file.** `src/heterodyne/actions/deployment_authority.py:192`: `_sources` keys its map by file path, so a later role overwrites an earlier one and that pin is never compared.
   - If the harness runs on the same interpreter as admind, `interpreter_sha256` is never checked.
   - I reproduced it: a Release with fake `interpreter_sha256` and `contract_sha256` pins still captured successfully.
   - Fix: key the map by role, or refuse duplicate paths.

**Should fix**
3. `deployment.py:149`: deviations can contain newlines, so a deviation can add a fake `Target:` line or trailer to the approval card. The digest still covers the bytes, but what the human reads differs from what they approve. Fix: refuse control characters and newlines.
4. `deployment_authority.py:166`: `_bindings` catches `ValueError`, which also catches `AuthorityHold` and `InvalidAction`. Every specific hold reason becomes "malformed native release context", and `_capture` labels authentication refusals as "unavailable", so tampering looks like an outage. The `raise … from exc` at line 168 can never run.
5. `deployment_runtime.py:361` against `deployment_authority.py:194`: the interpreter path is resolved once for hashing and again for execution. Swapping a symlink between the two sweeps would run unpinned code. Fix: resolve once and pass the same path or file descriptor to both.
6. `deployment_authority.py:191`: the set of pinned files is uneven, and the doc's claim that the "complete CLI executable closure is now explicit" goes too far.
   - The `ADMIND` console wrapper is pinned but never executed.
   - The harness's own msgspec, which decides whether a Release is valid, and both standard-library trees are not pinned.
7. `deployment.py:219`: `BEGIN IMMEDIATE` and the cancel `UPDATE` raise a raw `sqlite3.OperationalError('database is locked')` when the database is busy, not `InvalidAction`.

**Minor**
8. `deployment_authority.py:49`: `_load_core` opens the resource without `O_NONBLOCK`, so if a FIFO replaces it, `open()` hangs before the regular-file check runs.
9. `deployment_runtime.py:325`: each capture does about five directory walks and hashes site-packages (up to 512 MiB) twice, which widens the gap between the sweeps and the native readback. Fix: do the checks in the same walk that hashes.
10. `deployment_authority.py:224`: the comparison `before != _sources(release)` can never be true. The `CORE_SHA256` literal is also duplicated in `scripts/check_install_agnostic.py`, with nothing keeping the two in sync.

Nothing was edited, committed or posted, and no live DB, service, queue or GitHub thread was touched.
