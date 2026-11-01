I ran `/code-review high 155` (built-in `code-review` skill, args `high 155`, forked execution) on Opus 5.5 (`claude-opus-5-5`) at HEAD `290cc61c681304f0f1cc8ba691c58db3dece3f61`. Nothing was changed: no comments, pushes, edits, service changes or test runs. I reviewed only the PR commit's own diff (`290cc61~1..290cc61`): 11 source lines, a 26-line test, and the review bundle.

**May the source PoC merge?** Yes, after one small fix. There is no security flaw: every new path refuses, nothing can be issued twice, and the cancellation check sits under the same `target.lock` flock right before `_issue`. But one real correctness flaw contradicts what the README claims, and it should be fixed first.

## Real flaws

1. **A cancelled marker hides "already issued, outcome unknown"** (`deployment_root_helper.py:255-265`). I checked this against the source. The new `os.stat(... ".cancelled")` runs before `_issue`'s `O_EXCL` open of `.issued` (line 283), and nothing earlier in `run()` checks `.issued`.
   - **How it happens:** a run issues the packet, then the installer runs or crashes. The admission owner then publishes `.cancelled`, for example on timeout. A retry or reconciliation call now gets `root-helper-cancelled` instead of `root-helper-already-issued-unknown`.
   - **Impact:** no second effect happens, because both paths refuse. But the reported code says "never happened" when the host may already be modified. That inverts the one-use UNKNOWN precedence the README says is preserved.
   - **Fix:** inside the lock, before the cancellation check, check whether `requested + ".issued"` exists (`follow_symlinks=False`) and raise `root-helper-already-issued-unknown` if so. Keep `_issue`'s `O_EXCL` as the authoritative check.
   - **Missing test:** none covers `.issued` and `.cancelled` existing together. The probe deletes the marker before its issue and retry steps (`tests/test_deployment_root_helper.py:138-166`), so this regression and any future reordering pass CI.

2. **Other `stat` errors escape untyped** (line 259). Only `FileNotFoundError` is caught, so EIO, ESTALE or ENOTDIR come out as a raw `OSError`. The probe treats a post-issue `OSError` as UNKNOWN, so a refusal that happened before issue looks like a possible launch failure. It still refuses, so this is low severity.
   - **Fix:** `except OSError: raise InvalidAction("root-helper-cancel-check") from None`, keeping the `FileNotFoundError` branch as "not cancelled".

## Cleanups and minor points

- **Redundant flag** (line 260): the `cancelled` boolean can go. Use `try: os.stat(...)` / `except FileNotFoundError: pass` / `else: raise InvalidAction("root-helper-cancelled")`. That also shrinks the `_RootInstaller` verbosity regression.
- **Better home for the check:** moving it into `_issue`, or a small helper called right next to it, makes "never issue a cancelled packet" true for any future caller of `_issue`.
- **Quality gate failed and nothing records why:** `qualification.json:14` shows `quality_exit: 2` with two gating verbosity regressions (`_RootInstaller` 119→130, `_PROBE` 106→132). Either fix them or add an explicit acknowledgement.
- **Test tied to call order:** the late-cancellation test assumes `_sudo_parent` runs exactly twice, with the second call inside the lock (`checks == 2`, line 128). If a call is added or removed, the test could pass without testing the late-publish path. Triggering on lock acquisition, or on the second `_retained`, would be sturdier.
- **No negative test:** nothing checks that a `.cancelled` marker for a *different* packet digest doesn't block this one.
- **Efficiency only, not correctness:** an already-cancelled packet still hashes the binaries, takes the lock, and copies and fsyncs the installer to `/run` before it is refused. That holds the target lock and can turn away another run with `root-helper-target-busy`. An early unlocked check would avoid this, but the locked final check must stay.

## Not findings

As you scoped it, these are separate work and were not raised:
- the dormant root helper
- the real producer and installed qualification
- the root-owner and future-producer requirement to take the same flock and never delete `.cancelled`

The 83 focused tests passing in 1.95s and Ruff passing are your reported results; I did not re-run them.
