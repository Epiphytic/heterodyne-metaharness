**Not yet.** At HEAD `3cc4975` I don't think the private parent transport and consumed-correlation component should merge, even as a PoC. Two defects in the correlation code would let consent that isn't bound to this card pass. Both have small fixes. The transport side (immediate-parent pidfd, private seqpacket credentials, sealed nonce, retained lease, size limits) has no blocking defects, only hardening items.

**Review setup:** the built-in `/code-review` skill, args `high 156`, effort high, model Opus 5.5 (`claude-opus-5-5`). It was read-only: no comments, pushes, edits, service changes or tests run. I re-read `_correlate` myself and confirmed findings 1 and 2. Your figures (70 focused tests in 13.08s on Python 3.12.3, Ruff/Pyright passing, full quality exit 2) were not re-run.

### Blocking (confirmed in the source)
1. **The receiver's group is never checked against the producer's group** (`src/heterodyne/actions/deployment_admission_binding.py:128`). `enrollment["group"]` is never compared with `producer.group_hex`. The "expected" original is built from the producer, and the operator check only confirms that someone in the enrollment's operator list approved. So an operator from group G1 can approve a card for group G2. The synthetic fixture always gives both the same group, so tests miss it.
   *Fix:* add `enrollment.get("group") != producer.group_hex` (in the matching encoding) to the installation check, plus a test where the groups differ.
2. **The producer profile is hashed from one read and parsed from another** (`deployment_admission_binding.py:101` and `:108`). The digest is checked on `producer_raw = _read(13, …)`, but the profile that's used comes from a separate `_profile(13)` read. The bytes that pass the check aren't the bytes that get used.
   *Fix:* decode the profile from `producer_raw` and drop the second read.

### Should fix (plausible; not blocking a PoC on their own)
3. **`receipt.policy_sha256` is only format-checked** (`:173`). It's never compared with the enrollment's or the consumed record's `capability_policy_sha256`, and the test fixture uses `d…` against `a…`. Bind it, or document why the receipt's policy digest can legitimately differ.
4. **Nothing proves the parent actually holds the lock on FD5** (`deployment_admission_peer.py:115`). Only its file metadata is checked. One check is to reopen `/proc/self/fd/5` and call `F_OFD_GETLK`.
5. **Some failures escape as raw exceptions instead of `InvalidAction`** (`deployment_admission_peer.py:152`):
   - `select.select` on the pidfd raises `ValueError` once the fd number is 1024 or higher; use `poll`.
   - Stat-ing `/proc/{pid}/exe` can raise `FileNotFoundError` if the parent exits, instead of `admission-parent-ceased`.
   - The `recvmsg` timeout raises `TimeoutError`.
6. **FD4 setup leaks a descriptor** (`deployment_admission_peer.py:125`). If FD4 isn't a socket, the dup is leaked and the caller gets a raw `OSError` rather than `admission-channel-type`.
7. **`_json` skips the codec's text and limit checks** (`deployment_admission_binding.py:79`). Control and bidi characters are accepted, and the enrollment is re-serialized with `ensure_ascii=False` although nothing enforces the comment's "ASCII-only" claim.

### Test gaps
- **The seal check is never reached** (`tests/test_deployment_admission_peer.py:43`). The "unsealed" case is rejected earlier, on access mode, so deleting the seal check wouldn't fail any test.
- **The namespace probe is pinned to Python 3.12** (`tests/test_deployment_admission_peer.py:94`). It hard-codes `/usr/bin/python3.12` and the 3.12 site-packages path. Separately, the non-root refusal test has no skip when pytest runs as root.

### Cleanup
`decode_packet` is run three times per correlation, and the 64-character hex check is hand-written four times; reuse `_HASH.fullmatch` instead (`deployment_admission_binding.py:112`).

None of these findings depend on the parts you scoped out (entry point, importer, journal admission, permit publisher, host effect). With 1 and 2 fixed and covered by tests, plus a decision on 3, I'd consider this component clear to merge as a PoC.
