**Verdict: changes requested.** I found no critical bug in the producer process code (`deployment_producer_process.py`). What blocks is the evidence: the real-child refusal tests don't show that each guard is the thing that causes the refusal, so the qualification document claims more than the tests prove.

I made no edits and ran no services or queue work. The probes ran from `/tmp/probe-ppx`.

**What checks out:**
- **Test count:** rerunning the manifest's pytest command gives 151 passed. The new test file alone gives 21 passed.
- **Hashes:** the source, test and fixture hashes match `source-test-manifest.json`, and so do all four log hashes.
- **Positive case:** the child is a real isolated subprocess with real Git objects and FD 3/4/5. The HTTPS endpoint and the rendering profile are synthetic, and the qualification document says so.

**Actionable findings, most serious first:**

1. **The real-child refusal tests don't prove which guard refused** (`tests/test_deployment_producer_process.py:156`). Every refusal exits 2 with the same stderr, and on this host the negative child can never succeed. I turned off the 0400 mode check inside the child and the "writable" case still passed. The child got past preparation and then failed on a missing `/run/heterodyne-deploy-publication` instead. The same masking applies to the original, large, aliased, unknown-profile, closed-fd and argv cases. The results also depend on whether the host has that directory. The document's claim of "proving … custody/closed-fd refusals" is overstated.

2. **The tamper cases never reach the comparison against the original request** (`tests/test_deployment_producer_process.py:117`). The original, canonical and request cases append `"a"` to base64 fields, so they fail base64 decoding first, and `match='producer-output'` hides that. A field that is validly re-encoded but different is never tested. That means weakening the byte-field comparison would leave all 10 tamper cases passing.

3. **Deeply nested output crashes instead of refusing** (`deployment_producer_process.py:143`). `_check_output` uses a bare `json.loads` with no depth limit. With `b'['*200000` it raises `RecursionError` instead of `InvalidAction`.

4. **Unexpected exceptions bypass the fixed refusal** (`deployment_producer_process.py:176`). `_entry` only catches `InvalidAction`, `OSError`, `ValueError` and `TypeError`. If fd 0 is closed, `sys.stdin` is `None` and the child dies with an `AttributeError`. `RecursionError`, `MemoryError` and `KeyError` behave the same way. The child then exits 1 with a traceback containing paths and source lines, which breaks the closed stderr contract.

5. **Output checking rebuilds the card separately from preparation** (`deployment_producer_process.py:152`). `_check_output` builds `_PreparedCard` by hand from 12 positional arguments, mostly plain strings, instead of sharing `_prepare`'s derivation. Two swapped string arguments would not fail type checking. It also leaves out the `asks.normalise_id` check, so output for a packet that preparation would refuse still passes. The "recomputed on the actual packet and card" claim is therefore not exact.

6. **Several refusal paths have no test** (`deployment_producer_process.py:124`, also `:80`). Nothing covers `producer-descriptor-changed`, `producer-profile-changed-or-bound` or `producer-output-bound`, or any refusal after the network step (hosted mismatch, URL or path mismatch). Deleting the identity recheck at line 124 or the size/identity recheck at line 80 leaves all 21 tests green.

7. **One assertion proves nothing** (`tests/test_deployment_producer_process.py:158`). `os.fstat(archive).st_size > 0` holds for any fixture. It doesn't show the archive was left untouched or that the refusal came before publication.

**Lower priority:**
- **Timestamp granularity** (`deployment_producer_process.py:71`): change detection relies on mtime, ctime and size. On kernels with coarse timestamps, a same-size overwrite through an fd opened before the chmod could go undetected. This host (kernel 6.17) has fine-grained timestamps, so this is a portability limit, not a current exploit.
- **Repeated decoding** (`deployment_producer_process.py:120`): the same packet is decoded and validated up to 4 times per run. Having `_bound_operation` return the decoded packet would remove the duplicate logic.

**Suggested fix for findings 1, 2 and 6:** make each real-child case assert which check refused, for example through a test-only diagnostic channel or by running in-process with `match=`. Alternatively, give the negative child a working scratch directory and a rendering stub, so that removing a guard makes the child succeed or reach the network instead of failing later anyway.

None of these findings are in base 6ac40e3, and none touch the launcher, custody or consent enrollment work that is on HOLD.
