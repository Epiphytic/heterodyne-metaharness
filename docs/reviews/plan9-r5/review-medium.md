I found no critical flaws in this slice, so my verdict is **APPROVE as a PoC**. Everything below is minor; I did not count any of the 10 R4 findings again.

What holds up:
- Metadata is size-checked, parsed and compared against the expected digest and versions before the tree is touched.
- The original record is checked before and after hashing. A freshly made record cannot hide changed content, because the content hashes are compared with the pinned manifest.
- `require_authority` always raises Hold, and nothing is written.

The most real finding is #1. The 64 MiB hash budget is documented as returning `install_projection_bound`, but because `codec.Hold` subclasses `ValueError`, it comes back as `install_projection_tree_unavailable`. The tests never reach the read-time budget at all (#5), and the doc says they cover hash budgets. As the rules required, I did not run, verify or edit anything.

9 findings, most severe first:

1. `src/heterodyne/acceptance/install_projection.py:86`: The Hold('install_projection_bound') that the hash-budget `read` closure raises is swallowed by `except (sc.Refused, OSError, ValueError)` and replaced with 'install_projection_tree_unavailable', because codec.Hold subclasses ValueError.

   Failure scenario: A manifest whose declared file sizes sum to 64 MiB or less, but whose captured file grows during hashing (or a file added between the first check_fast and entries_of), pushes cumulative reads past MAX_FILE_BYTES. read() raises Hold('install_projection_bound'), which goes up through entries_of, is caught as ValueError at line 86, and is re-raised as 'install_projection_tree_unavailable'. The 'bound' code the doc promises for the cumulative hash budget can never be produced.

2. `src/heterodyne/acceptance/install_projection.py:64`: `manifest.version not in versions` has no runtime type check on `versions`, so passing a str instead of a tuple turns the declared-version check into a substring match.

   Failure scenario: An untyped caller passes versions='1.2.30' (a string, not a 1-tuple). A manifest with version '1.2.3', '2.3' or even '0' passes the 'declared version' check because `'1.2.3' in '1.2.30'` is True, and the projection reports a version that was never declared.

3. `src/heterodyne/acceptance/install_projection.py:81`: The tree stage catches only Refused/OSError/ValueError. A non-int or out-of-range root_fd makes the ctypes statx call (argtypes c_int) raise ctypes.ArgumentError, and a too-large non-negative fd makes os.* raise OverflowError, which escape as untyped exceptions instead of a HOLD.

   Failure scenario: verify(..., root_fd=None) or root_fd=2**40 runs sc.check_fast -> statx_at -> _statx(...) and raises ctypes.ArgumentError (not a TypeError, ValueError or OSError). The caller gets a raw exception, which contradicts the doc's 'Reads that cannot be completed or proven consistent return a typed HOLD'. Only root_fd=-1 is tested.

4. `src/heterodyne/acceptance/install_projection.py:87`: The projection reports success after checking only `entries`. The manifest's entry_point, target and sources are never checked against the tree, so 'the sampled private fd matches the specified manifest' holds only for the entry list.

   Failure scenario: An admitted manifest with entry_point='bin/cli' and entries that contain no 'bin/cli' (or where it is a non-executable 0444 file or a link) passes verify() and returns an InstallProjection. The owner's stage path would refuse that manifest under NO_VERSION/BAD_PART, yet the projection reports it as consistent.

5. `tests/test_acceptance_install_projection.py:155`: The 'file-bytes' budget case sets MAX_FILE_BYTES=1, so it trips the size check before the walk (line 59) and never runs the cumulative read budget in `read`. The doc's claim that tests cover 'hash budgets' is untested, and that is how finding #1 went unnoticed.

   Failure scenario: Delete the `budget` logic from `read` entirely and the whole suite still passes. A regression that removes the 64 MiB cumulative read cap during hashing (the documented defense against a capture that changes between checks) would not be caught.

6. `src/heterodyne/acceptance/install_projection.py:82`: Content is checked with `sc.entries_of` (a full walk with its own second pass, hashing every file present) instead of the store's existing `sc.rehash(root_fd, manifest, read=read)`, which store_core's own `_verify` uses to hash only the manifest's files.

   Failure scenario: The sequence check_fast, entries_of, check_fast does 6 full tree traversals, each bounded only by store-core's 200,000 entries. Any file added after the first check_fast is opened and hashed before the mismatch is found. rehash plus the two check_fast calls would do 4 traversals, never hash an unlisted file, and reuse the owner's verification primitive instead of a parallel equality check.

7. `src/heterodyne/acceptance/install_projection.py:21`: InstallProjection is a plain public frozen dataclass, so code can build a 'successful projection' without ever calling verify().

   Failure scenario: A downstream consumer that accepts an InstallProjection as evidence of consistency can be handed InstallProjection('codex', 'x', 'sha256:...', 1, 1) built directly, with no fd check behind it. Nothing ties the instance to a completed verify() (no private constructor or token). require_authority still holds, but the 'consistency projection' itself can be forged.

8. `src/heterodyne/acceptance/install_projection.py:55`: Raising Hold('install_projection_metadata_unavailable') inside the try only works because the `except` catches ValueError, a Hold superclass, and re-raises it with the same code. The flow is redundant and fragile.

   Failure scenario: If the noncanonical-record check is later given its own code (for example 'install_projection_record_noncanonical'), the except clause silently rewrites it to 'metadata_unavailable'. The simpler form is to do the canonical comparison after the try/except, outside the handler.

9. `src/heterodyne/acceptance/install_projection.py:62`: The allowed adapter names are a hard-coded literal ('codex', 'claude') in the acceptance module rather than coming from the managed adapter registry or a declared constant.

   Failure scenario: If a managed adapter is renamed or added in the harness, this projection silently Holds with 'expected_metadata_differs' for a valid admitted install, and nothing ties the two lists together. A shared constant (or taking the admitted adapter set as an input, as with versions) would avoid the drift.
