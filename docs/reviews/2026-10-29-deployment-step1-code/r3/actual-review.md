# Actual Claude Opus code review round 3

**Verdict: APPROVE as a PoC. I found no critical flaws.**

I ran the built-in `code-review` skill at medium effort on `ee87166...eef8c8f` (HEAD is `eef8c8fee6bf…`). It returned 9 unverified candidates. I checked the important ones against the source. None is a safety bypass or a regression of the R1/R2 blockers, and every one either fails closed or falls under a limit you've already documented. In this session I only reviewed. I didn't re-run the tests or the checkers; the skill run reports the 45 tests passing and `check_install_agnostic.py` clean.

**Confirmed but not blocking. Worth tracking:**

1. **Approval card can be too large to ever approve.** `src/heterodyne/actions/deployment.py:146` accepts up to 16 deviations of 1024 characters each plus 8 links. But the authority check at `deployment_authority.py:141-144` requires one untruncated card, and admind caps a card at 3,500 characters (`admind/asks.py:41`). A packet with a few long deviations passes validation and then never gets approved. It fails closed, so it's safe, but it's a dead path. Fix: bound the total rendered approval text in `validate()`.
2. **A cancelled job locks its target forever.** `cancel()` at `deployment.py:329-335` only sets `cancelled=1`. The job keeps `phase='executing'`, so the `active_target` unique index (`deployment.py:208`) still holds the target, and the next enrollment for it fails with a target conflict. Forward steps are correctly blocked (`deployment.py:278`). This hurts usability, not safety, and is acceptable for a fixture whose cancel stands in for an independent stop.
3. **Tampering is reported as an outage.** `_core()` at `deployment_authority.py:95` catches `ValueError`, which also swallows the specific `InvalidAction` refusals (pin mismatch, unsafe chain, source changed). They all come out as `authority-source-unavailable`. It still holds, but an operator can't tell tampering from a missing file. Fix: re-raise `InvalidAction` before the `ValueError` catch.

**Raised, but covered by your documented limits:**

- **Harness bytecode cache isn't in the digest.** The harness `__pycache__` contents are left out of `harness_package_sha256` (`deployment_runtime.py:101`). A poisoned `.pyc` in the controller's own package would run before any check, so a self-check can't catch it; this is the documented "controller in-memory imports" limit. Your R2 fix still holds: the child runs with a fresh private `pycache_prefix` (`deployment_runtime.py:133`), so it never reads those caches.
- **The child's standard library isn't pinned.** The child uses the resolved base interpreter (`deployment_authority.py:225`) and its stdlib, and only the interpreter binary is hashed. That's your documented stdlib limit, so it's fine as long as you don't claim an "exact executable closure" beyond that.

**Minor:**

- `scripts/check_install_agnostic.py:146` calls `read_bytes()` outside the `OSError` handler, so a dangling symlink crashes the checker with a traceback.
- The pin on `admind-wrapper` (`deployment_authority.py:196`) covers a file the evidence path never runs. A harmless reinstall can cause a false hold.
- `stream_hash` re-checks every parent directory for each file (`deployment_runtime.py:29`), which costs extra system calls on large trees.
- Hashing the live harness `__pycache__` can race with an import writing a new `.pyc`, causing a false hold.

Both R2 blockers look causally fixed: the isolated child doesn't use the cache, and the role-keyed `required` dict (`deployment_authority.py:195-201`) checks each role separately, so two roles pointing at the same physical file are still both validated. I'd fix items 1 and 3 in a follow-up, not as merge blockers. The host opener staying on HOLD (`deployment.py:338`) matches the no-live-deployment scope.
