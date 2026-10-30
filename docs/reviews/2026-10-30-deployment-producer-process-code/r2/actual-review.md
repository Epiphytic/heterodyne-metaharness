**Verdict: CHANGES REQUESTED.** I ran the built-in `code-review` skill at high effort over `6ac40e3..919edae`, round two, cumulative, read-only. Afterwards the worktree is clean, HEAD is still `919edae`, and no `.cessation-*` folders were left behind.

The R1 runtime repairs hold:
- Nested JSON and closed stdin now produce a typed refusal.
- The child no longer leaks tracebacks.
- Candidate derivation now goes through one shared `_derive`.
- Valid-base64 tampering now reaches the correlation check.

What doesn't hold is the round's main claim, that the refusal tests prove which check refused. The reviewer deleted checks one at a time in a scratch copy, and the tests stayed green each time. That copy has been deleted.

**Blocking**
1. `src/heterodyne/actions/deployment_producer_process.py:95`: you can delete the canonical-binding check (`profile.packet_sha256`) and all 44 producer/card tests still pass. Nothing else enforces it, so a profile whose digest doesn't match is accepted. The profile-digest check (line 84), the profile-URL check, and the size limit on `original` (line 91) have the same problem.
2. `src/heterodyne/actions/deployment_producer_process.py:117`: no test depends on the object-alias check. The real-child aliased case fails later in `check_git`, and the in-process case hits `producer-descriptor-alias` instead. This is the same masking R1 finding 1 raised.
3. `src/heterodyne/actions/deployment_cessation.py:205`: the 19 cessation tests stay green with any one of these removed:
   - the folder-inode re-check (205)
   - the copy-size check (198)
   - the `live.check()` after the snapshot (291)
   - the destination-changed raise (297)
   - the source-folder custody raise (126)

   Only sidecar custody, taken as a whole, is shown to matter.
4. `src/heterodyne/actions/deployment_publication.py:91`: reproduced. `HTTPConnection(_HOST, 443)` sends `Host: raw.githubusercontent.com:443`, while the old code sent no port. A CDN that routes on the exact host can then fail publication corroboration even when the content is correct. The test stub never checks the header. Fix: set the Host header explicitly, or set `default_port=443`.
5. `src/heterodyne/actions/deployment_producer_process.py:165`: reproduced. If the reader closes stdout early, bytes stay buffered, and the flush at shutdown exits with 120 plus a `BrokenPipeError` on stderr. That breaks the exit-2, one-line stderr contract. Fix: in the failure path, either point fd 1 at `/dev/null` or call `os._exit(2)`.

**Design and correctness, should fix**
6. `src/heterodyne/actions/deployment_card.py:49`: `_derive` is documented as pure, but `auth._rendering()` reads the host `config.toml` and policy files, which nothing in the profile binds. The child and the parent can therefore render different cards. Tests stub this out.
7. `src/heterodyne/actions/deployment_card.py:80`: the `deployment-card-publication-mismatch` comparison can never fail, so it can't be tested and gives false assurance.
8. `src/heterodyne/actions/deployment_producer_process.py:142`: `_check_output` reimplements part of `codec._decode`. The copy drops the rejection of NaN/Infinity, the depth and node limits, and the strict UTF-8 decode. Use `_decode(raw, _Output, max_bytes=_OUTPUT_LIMIT)` instead.

**Cleanup**
9. `src/heterodyne/actions/deployment_producer_process.py:96`: nothing uses the return value of `_bound_operation`. The packet is still decoded 3–4 times per call, so the R1 note about repeated decoding is still open.
10. `src/heterodyne/actions/deployment_cessation.py:115`:
    - `_source_directory` duplicates `deployment_host_store._private_root`.
    - The identity tuple exists in three copies.
    - The `main is None` checks at lines 160 and 167 can never be reached.
    - `tests/test_deployment_cessation.py:22` creates `.cessation-*` folders in the current folder, so the tests depend on that checkout's parent folders passing custody.

The private child FD protocol (launcher and current-consent authority) and the source custody repair were left out of scope, as you asked. To close this round, add one test per guard that fails only when that guard is removed, and fix items 4 and 5.
