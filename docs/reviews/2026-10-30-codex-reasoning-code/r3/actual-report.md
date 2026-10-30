**Verdict: no critical findings. This is good enough to implement as a PoC.**

I ran the built-in `code-review` skill at medium effort, read-only, against `a3d096d`. It covered only the reasoning-effort commits. The full `main...a3d096d` range is about 1,061 files because `main` is far behind this branch, so that range was not reviewed. The related suites passed (686 tests) during the review. I made no edits and did not touch services or the queue. The deliberate pre-mutation hold on native resume with effort was taken as given.

### Findings I checked against the source (none block the PoC)

1. **Medium: an owed WIP commit blocks a handoff on effort profiles.** `src/heterodyne/sandbox/runtime.py:618-619`
   - `_check_reasoning_resume` raises "lifetime WIP commit is still owed" before launch reaches its own `_settle_wip` (`runtime.py:573-579`). It does this even for `spec.handoff`, which never resumes.
   - The same profile without `reasoning_effort` would commit the owed WIP and launch. This goes against the doc's "An explicit handoff … remains eligible" (`docs/configuration.md:64`).
   - It isn't critical because nothing is lost: `expire()` commits the owed WIP of ended sessions (`runtime.py:649-653`), after which the handoff succeeds.
   - Fix if wanted: skip the WIP check in the gate when the launch won't resume (handoff), or let `_settle_wip` run first.

2. **Low: one held state raises different errors depending on profile.** `runtime.py:616-617`
   - Unlanded commits raise `RuntimeUnavailable` (via `self._unlanded`) when the profile sets `reasoning_effort`. Without it they raise `LaunchFailed` (`runtime.py:570-572`).
   - Callers that treat those two types differently will handle the same state differently depending on a profile setting.

3. **Low: the gate runs on the record from before cleanup.** `runtime.py:621`
   - The gate calls `_resume` on the record before `_end` runs. A dead RUNNING generation whose `native_id` was never recorded can get a different refusal message or type than the one launch would give after cleanup.
   - It still refuses before changing anything, so it is safe, just inconsistent. It also checks the session home twice.

4. **Low: a Codex `profile` in the install defaults overrides the typed effort.** `src/heterodyne/agents/codex.py:177`
   - The effort is written as a top-level `model_reasoning_effort`. If the install's `defaults/config.toml` selects a Codex `profile = "x"` that sets its own effort, that value silently wins.
   - Only operator-controlled defaults can trigger this. One option is to reject `model_reasoning_effort` inside `[profiles.*]` in the defaults when typed effort is set.

### Dismissed after checking

- **"Closed profile-key allowlist breaks existing configs"** (`src/heterodyne/config/layers.py:156`): the allowlist exactly matches every profile key the source reads (`adapter`, `model`, `args`, `reasoning_effort`, `accounts`, `failover`). Rejecting unknown keys is documented on purpose.
- **"The gate repeats checks; it's a reasoning-only special case"**: these are design and tidiness comments, not defects. A general check that any home-config change (model, MCP servers) blocks resume would be a reasonable follow-up, but it is outside this PR's scope.

### On the hold itself

The core property holds. With an effort profile, any launch that would resume raises `LaunchFailed` before `_end`, writing the session record, or creating the sandbox. A live session is left as is, since the "already running" return comes before the gate. Integrity holds are reported before the reasoning hold, as documented.
