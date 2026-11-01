Skill "code-review" completed (forked execution).

Result:
I reviewed PR 154 as the single commit `HEAD~1..HEAD`. `main...HEAD` spans 1272 files because main has diverged, so I left that wider range out of scope. Nothing was verified: I didn't run any tests and found no crash-level bug. The findings are mostly coverage gaps and design or cleanup issues. They are ranked most-severe first; the two test findings are close in severity, so read them as a pair.

```json
[
  {
    "file": "tests/test_deployment_profile_check.py",
    "line": 69,
    "summary": "The 'causal' descriptor-change test never reaches the new `profile-check-descriptor-changed` branch: `os.fchmod(profile_fd, 0o600)` makes `_identity` raise `producer-descriptor-custody` before the identity comparison runs, and the regex `descriptor-custody|descriptor-changed` hides this.",
    "failure_scenario": "Delete or invert `if _identity(profile_fd) != before` at deployment_profile_check.py:36 and the test still passes, because the custody check raises first. A change that keeps mode 0400 (for example futimes or a write through an already-open writer that only changes mtime/ctime/size) is never tested, so a regression in the only new safety check in `_profile_check` would go unnoticed."
  },
  {
    "file": "tests/test_deployment_profile_check.py",
    "line": 25,
    "summary": "The 'no effect calls' guards cannot fail: they patch `producer._prepare`/`producer._derive` and `os.open`, but `_profile_check` never refers to `producer._prepare`/`_derive` and never calls `os.open`. The child test does the same on line 83.",
    "failure_scenario": "If `deployment_profile_check` later imports `_prepare` directly (`from ...deployment_card import _prepare`) or opens a path through `pathlib`/`io.open`, the test still passes. The claimed evidence that the checker does no card work or path opening is vacuous."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 38,
    "summary": "There is no owner-side strict decoder or size bound for the checker's stdout, unlike the producer, which ships `_check_output` (bounded, strict-typed, canonical-encoding check).",
    "failure_scenario": "The installation owner must parse the child's stdout with its own ad-hoc code. Duplicate keys, non-canonical encoding, oversize output or extra fields could be accepted, or a second codec gets written. That is exactly the second-codec risk the module docstring says it avoids."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 38,
    "summary": "`_Result` binds only the two digests and nothing about the profile that was checked (no hash of the profile bytes or of targets/group_hex/publication_url).",
    "failure_scenario": "Two profiles with the same `operation_original_sha256`/`packet_sha256` but different `targets`, `group_hex` or `publication_url` give byte-identical results. The owner can't tell from the result which retained profile was validated, so the result could be correlated with a different profile than the one on FD3."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 17,
    "summary": "The 'pure' checker imports private helpers `_identity`, `_profile` and `_bound_operation` from the producer module (with `reportPrivateUsage` suppressed), which pulls in `deployment_card`, `deployment_authority` and the rest of the card-rendering and publication import closure.",
    "failure_scenario": "The owner must qualify the producer's whole card/publication import graph to run a checker that claims no card or publication code. Any import-time side effect in those modules runs in the 'pure' child. The better fix is to move the descriptor/profile codec into a shared module that both import."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 43,
    "summary": "`_entry` copies the producer's fixed-entry boilerplate almost exactly (argv check, bounded stdin read, write and flush, broad except, fixed stderr, `os._exit(2)`). Ripwire's own quality.log flags this as a gating duplication.",
    "failure_scenario": "A future fix to the refusal boundary, such as also catching BaseException or changing the stdout flush behaviour, must be applied in two places. They will drift, and one child boundary will start leaking tracebacks or retrying flushes. A shared `_fixed_entry(refusal_bytes, fn)` helper would remove the copy."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 48,
    "summary": "The profile descriptor slot is a literal `3` instead of the existing `_PROFILE_FD` constant from deployment_producer_process.",
    "failure_scenario": "If the owner's fixed slot layout changes and `_PROFILE_FD` is updated, the checker keeps reading fd 3. It could read the wrong descriptor or refuse every check, and nothing ties the two contracts together."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 47,
    "summary": "Refusals reuse producer error codes (`producer-descriptor-custody`, `producer-profile-changed-or-bound`, `producer-original-binding`) alongside new `profile-check-*` codes, so one checker failure surface uses two code namespaces.",
    "failure_scenario": "Any caller or test that matches on `profile-check-` codes (as test line 69 half does) misses most real refusals. When the in-process `_profile_check` is used directly, diagnostics wrongly blame the producer."
  },
  {
    "file": "tests/test_deployment_profile_check.py",
    "line": 78,
    "summary": "`child()` uses `os.dup(profile_fd)`, which returns the lowest free fd. The existing producer helper deliberately uses `F_DUPFD_CLOEXEC` with a floor of 64 so the duplicate can't land on a fixed slot.",
    "failure_scenario": "If fd 3 (or another slot) is free in the pytest process, the duplicate can land on 3, and then `os.dup2(duplicate, 3)` is a no-op. The fixture no longer exercises a real remap, and the 'closed' case closes the only copy. The test diverges from the established helper pattern and its behaviour depends on the environment."
  },
  {
    "file": "src/heterodyne/actions/deployment_profile_check.py",
    "line": 33,
    "summary": "`_profile_check` does redundant work: it takes its own `_identity` snapshot even though `_profile` already takes and checks one internally, and `_bound_operation` builds `encode_record(packet)`, which is thrown away.",
    "failure_scenario": "Each check runs extra fstat/fcntl syscalls and a full canonical re-encode of up to `MAX_BYTES` that nothing uses. A non-encoding validation variant of `_bound_operation`, or having `_profile` return its snapshot, would avoid it."
  }
]
```

The code under review is `src/heterodyne/actions/deployment_profile_check.py` and `tests/test_deployment_profile_check.py`. The reused helpers are in `src/heterodyne/actions/deployment_producer_process.py`.
