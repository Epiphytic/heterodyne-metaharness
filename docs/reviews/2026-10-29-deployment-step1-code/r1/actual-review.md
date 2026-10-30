**Verdict: changes requested.** I ran the built-in `code-review` skill at HIGH effort (forked, Opus 5.5) on `ee87166..9dc38e3`. It found 10 problems.

Two were confirmed by running things. The test claim in the qualification doc doesn't hold up, and CI will fail on this commit. The other eight come from reading the code and weren't checked further.

The fork ran pytest and the install-agnostic checker inside the worktree, which may have left `__pycache__`/`.pytest_cache`. Nothing else was edited, committed, posted to GitHub, or touched on the live DB, queue or services.

## Ranked findings

**1. The approval packet URL isn't tied to the release** (`src/heterodyne/actions/deployment_authority.py:121`)
`_bindings` takes the packet URL from the native card body and only checks it against a regex. `approval_body(release, urls[0], group)` then rebuilds the expected text from that same URL, so the comparison can't fail. Any `heterodyne-metaharness` `docs/*.json` at any commit passes. The ADR requires the pushed packet link to match exactly.
*Fix:* carry the expected packet repo, commit and path (or digest) on `Release`. Build the expected body from the release, not from the card.

**2. The admind and source pins don't cover the code that actually runs** (`deployment_authority.py:144`)
`admind_sha256` hashes the 392-byte console-script wrapper, not `heterodyne/admind/cli.py` or what it imports. If someone changes the admind package, they can forge an answered `ask get --json` and both sweeps still match. The source pins hash `.py` files, but a replaced `__pycache__/*.pyc` would run undetected.
*Fix:* pin the resolved admind module files too, or run with `-I -B` / `PYTHONDONTWRITEBYTECODE` and refuse if stale `.pyc` files are present. Alternatively, pin a sealed package digest.

**3. Confirmed: `_capture` always HOLDs on uv-managed interpreters** (`deployment_authority.py:150`)
In the worktree `.venv` (uv Python 3.14), the interpreter is 32 MB, over the 16 MiB read limit. `test_native_capture_fixed_reader_seam_and_two_sweeps` fails with `HOLD: unsafe or oversized source file`, so the run is 19 passed and 1 failed. The "20 PASS" claim only holds on `/usr/bin/python3.12`, which is 8 MB.
*Fix:* hash the interpreter as a stream with its own larger limit, or pin it differently. Then correct the qualification doc to name the interpreter it was run on.

**4. Confirmed: CI will fail the install-agnostic check** (`deployment_authority.py:21,22,23,26`; `resources/hermes_native_authority.py.txt:169`)
`ci.yml:52` runs `scripts/check_install_agnostic.py` over every tracked file, and it exits 1 on these hardcoded `/home/openclaw` paths. Skipping the local pre-commit hook doesn't skip this CI step.
*Fix:* add the checker's per-line `install-agnostic: allow=home-path` marker with a reason on each of these lines.

**5. Importing the module fails on normal installs** (`deployment_authority.py:74`)
`_load_core` runs at import time and raises when the resource has `nlink != 1`, belongs to another user, or sits under a group-writable directory. That covers uv's hardlink installs, a root-owned site-packages, and a home directory with umask 002. A dormant module shouldn't break on import.
*Fix:* load the core lazily on first authority use and turn these failures into an `AuthorityHold` there.

**6. The release alias never goes through the `deploy{target}` alias rule** (`src/heterodyne/actions/deployment.py:105`)
`Deploy(release.target.alias).target` does nothing, because msgspec doesn't validate on construction. An alias like `Hermes.Prod:1` passes `validate()` even though `decode_intent` would reject it.
*Fix:* call `codec._action`/`decode_intent` on the alias, or check it against `codec._ALIAS` directly.

**7. Refusals come back as different exception types** (`deployment.py:217`)
Reusing an approval or nonce, or starting a second job on a busy target, surfaces as a raw `sqlite3.IntegrityError`, and the test asserts that. `_bindings` can raise `TypeError` (for example, a native `group_id_hex` stored as an INTEGER) or `InvalidAction` instead of `AuthorityHold`.
*Fix:* catch `IntegrityError` in `enroll` and raise `InvalidAction`. Type-check the native fields before `re.fullmatch` and raise `AuthorityHold`.

**8. Cancelling an unknown execution id silently succeeds** (`deployment.py:302`)
`cancel()` doesn't check `rowcount`, so a typo like `cancel('executon1')` reports success while the real job keeps running.
*Fix:* refuse when `rowcount != 1`.

**9. The parsed operator policy isn't the bytes that were hashed** (`deployment_authority.py:155`)
`_operators` reads the policy files again instead of using the bytes from the before sweep. Each file is read four times, and the membership that gets parsed isn't necessarily what was pinned.
*Fix:* parse the operators from the before-sweep bytes.

**10. Duplicated helpers and diverging regexes** (`deployment.py:251`, `285`)
`_require` duplicates `codec._fail`, which forces dead `if row is None: raise` checks after each call. `_ID` and `_HEX` duplicate the codec and core patterns with different bounds: 128 characters here versus 256 in `codec._ID`.
*Fix:* use `_fail` and reuse the codec patterns.

## Bottom line

Findings 1 and 2 are the merge blockers for the authority and approval-binding contract. Findings 3 and 4 block the evidence and CI. The step-1 scope itself looks right for a dormant proof of concept, and nothing here asks for the rest of the harness to be built first.

This approves no host deployment. The production host gate stays HOLD.
