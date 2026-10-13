# btq-jjnqm: one live session per bead branch, refused at launch

Bead: btq-jjnqm. Base: main 850d531. ADR 0001 r15 (28e3df4) is unchanged: this enforces at launch an invariant the ADR already assumes (one session's commits land on `btq/<bead>`, D13), so it needs no amendment.

## Problem

T14's final live run (32c0bc7) launched two sessions on bead `btq-live-1`: a claude one and a codex one, with different session keys and the same branch `btq/btq-live-1`. A lifetime expire then ended both. Codex's end committed its lifetime WIP on the host branch. Claude's land was refused, because its tip no longer descended from the landed tip, and its record became `unlanded=True`, which holds the whole workstream.

The runtime failed closed, which is correct, but late. The bad state is visible when the second session launches, and that is where it should be refused.

wsd itself launches one coder role per workstream (`scheduler.py` docstring, step 6), so wsd doesn't create this state today. The runtime is the layer that owns the branch, though, and anything that launches through it directly (the live tests, a future second role, a bug in the role check) gets no protection there. This is defence in depth at the layer that sees every launch.

## Change

In `SandboxRuntime.launch` (`src/heterodyne/sandbox/runtime.py`), add one check. It goes after the existing `prev` handling, so a relaunch of the same live session stays idempotent and a stale record of this key is still ended first. It goes before `_resume` and before any record is written:

```python
BRANCH_TAKEN = "another session of this bead is not yet ended; its branch can hold only one session"

        for other in self._records():
            if (other.key != spec.session_key and other.phase is not Phase.ENDED
                    and other.bead == spec.bead and _same_repo(other.repo, spec.repo)):
                raise LaunchFailed(BRANCH_TAKEN)
```

- **What is "the same branch".** The branch is `btq/<bead>` in the record's repository (`gitwip.pin(spec.repo, spec.worktree, f"btq/{spec.bead}")`). Two records name the same branch when they have the same bead and the same repository. `_same_repo` compares `os.path.realpath` of the two paths. A record without a repo can't share one, so it is not counted.
- **Which records count.** Any phase other than ENDED: CREATING, RUNNING and ENDING. It doesn't matter whether that sandbox is still alive. A session that is not ended still owes its land or its lifetime WIP (D13), so its branch is not free until `_end` has run. `sessions()` (each pickup) ends dead sessions, so the refusal clears by itself once the other session is really gone.
- **What it doesn't refuse.**
  - The same key: the existing idempotent path handles that.
  - An ended record whose commits were not landed: `sessions()` already holds the workstream on `unlanded`, and the existing per-key check refuses relaunching that key.
- **Concurrency.** wsd serializes launches within a workstream under the Parker's `entry()` lock, and a bead belongs to one workstream. Two concurrent launches of one bead therefore can't race past the check. A direct caller that launches concurrently (no current caller does) could. That residual is noted in the code comment.
- **The reason text** is fixed, with no path, key or bead in it. It is a `LaunchFailed`, which wsd treats like any other failed launch: it spends launch budget, and the bead escalates once the budget is spent.

## Tests (`tests/test_sandbox_runtime.py`)

1. `test_a_second_session_on_the_same_bead_is_refused`:
   - launch the claude profile on bead X;
   - launch the codex profile (another key) on bead X with the same repo; expect `LaunchFailed(BRANCH_TAKEN)`;
   - check that no record was written for the second key, that no sandbox was created for it (backend names unchanged), and that the first session is still RUNNING.
2. `test_the_branch_is_free_once_the_first_session_ends`:
   - after `stop(first)` (record ENDED, landed), the second launch succeeds.
3. `test_a_dead_but_not_ended_session_still_holds_the_branch`:
   - kill the first sandbox in the fake backend, leaving its record RUNNING; the second launch is refused;
   - after `sessions(ws)` ends the dead one, the second launch succeeds.
4. `test_other_beads_and_other_repos_are_not_refused`:
   - a second key on bead Y in the same repo launches;
   - a second key on bead X in another repo launches.
5. The existing `test_launch_is_idempotent_on_a_live_session` still passes unchanged (same key).

The live test that produced the shape (T14's two-CLI run on one bead) must use one bead per CLI. If it still shares one, it will now fail at launch with `BRANCH_TAKEN`, and that is fixed in the test, not the runtime.

## Docs

`docs/wsd.md` §5, beside the unlanded-hold paragraph: one sentence saying a bead's branch holds one session at a time and a second launch is refused until the first has ended.
