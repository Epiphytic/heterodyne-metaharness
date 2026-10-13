# btq-jjnqm: one live session per bead branch, refused at launch

Bead: btq-jjnqm. Base: main 850d531. ADR 0001 r15 (28e3df4) is unchanged: this enforces at launch an invariant the ADR already assumes (one session's commits land on `btq/<bead>`, D13), so it needs no amendment.

## Problem

T14's final live run (32c0bc7) launched two sessions on bead `btq-live-1`: a claude one and a codex one, with different session keys and the same branch `btq/btq-live-1`. A lifetime expire then ended both. Codex's end committed its lifetime WIP on the host branch. Claude's land was refused, because its tip no longer descended from the landed tip, and its record became `unlanded=True`, which holds the whole workstream.

The runtime failed closed, which is correct, but late. The bad state is visible when the second session launches, and that is where it should be refused.

wsd itself launches one coder role per workstream (`scheduler.py` docstring, step 6), so wsd doesn't create this state today. The runtime is the layer that owns the branch, though, and anything that launches through it directly (the live tests, a future second role, a bug in the role check) gets no protection there. This is defence in depth at the layer that sees every launch.

## Change

In `SandboxRuntime.launch` (`src/heterodyne/sandbox/runtime.py`), add one check. It goes after the existing `prev` handling, so a relaunch of the same live session stays idempotent and a stale record of this key is still ended first. It goes before `_resume` and before any record is written:

```python
BRANCH_TAKEN = "another session of this bead still holds its branch; a branch holds one session at a time"

        others = [r for r in self._records() if r.key != spec.session_key and r.bead == spec.bead
                  and _same_repo(r.repo, spec.repo)]
        if any(r.phase is not Phase.ENDED or r.unlanded for r in others):
            raise LaunchFailed(BRANCH_TAKEN)    # before any settlement: a refusal moves nothing
        for other in others:
            if other.wip_mark:
                with contextlib.suppress(OSError):          # D13: the WIP it owes lands before anyone else's
                    other = self._settle_wip(self.layout(other.key), other)
                if other.wip_mark:
                    raise LaunchFailed(BRANCH_TAKEN)
```

- **What is "the same branch".** The branch is `btq/<bead>` in the record's repository (`gitwip.pin(spec.repo, spec.worktree, f"btq/{spec.bead}")`). Two records name the same branch when they have the same bead and the same repository. `_same_repo` compares `os.path.realpath` of the two paths. A record without a repo can't share one, so it is not counted.
- **Which records hold the branch** (Codex jjnqm r1). ENDED alone doesn't mean the branch is settled: `_end` records ENDED before it settles the lifetime WIP, and a land that fails records ENDED with `unlanded`. So another key's record holds the branch while any of these is true:
  - its phase is not ENDED (CREATING, TESTING, STARTING, RUNNING or STOPPING), whether or not its sandbox is still alive, because it still owes its land or its lifetime WIP (D13);
  - it is `unlanded`: its commits wait for a human (T10 r3), and a new session's commits would diverge from them;
  - it still owes a lifetime WIP (`wip_mark`). Only once no matching record is unended or unlanded (Codex jjnqm r2: a refusal must move nothing, so settling one record and then refusing on another is ruled out) does the check try `_settle_wip` on each that owes, which is what the owner's own end or relaunch does and is idempotent by its mark (a crash between ENDED and the commit is the case this covers). If the mark is still set after that, the branch stays held.
- **How a hold clears.** `sessions(ws)` ends that workstream's dead sessions, and `stop(key)` ends one. A dead record is only ended by a call for its own workstream or key: a launch never ends another key's session, live or not, to make room for itself. A caller handing a bead to another workstream ends the old session first (`stop(other.key)`, or `sessions(other.ws)`). An unlanded record clears only through the operator path that resolves the hold (btq-xb56t).
- **What it doesn't refuse.**
  - The same key: the existing idempotent path handles that.
  - An ended record whose commits were not landed: `sessions()` already holds the workstream on `unlanded`, and the existing per-key check refuses relaunching that key.
- **Concurrency.** wsd serializes launches within a workstream under the Parker's `entry()` lock, and a bead belongs to one workstream. Two concurrent launches of one bead therefore can't race past the check. This is serialized-launch enforcement, not a runtime-wide or cross-process lock: two Parkers, or a direct caller that launches concurrently (no current caller does), could both pass. That residual is noted in the code comment.
- **The reason text** is fixed, with no path, key or bead in it. It is a `LaunchFailed`, which wsd treats like any other failed launch: it spends launch budget, and the bead escalates once the budget is spent.

## Tests (`tests/test_sandbox_runtime.py`)

1. `test_a_second_session_on_the_same_bead_is_refused`, parameterized over every phase but ENDED (CREATING, TESTING, STARTING, RUNNING, STOPPING; the record is written in that phase):
   - launch the claude profile on bead X;
   - launch the codex profile (another key) on bead X with the same repo; expect `LaunchFailed(BRANCH_TAKEN)`;
   - check that no record was written for the second key, that no sandbox was created for it (backend names unchanged), and that the first session is still RUNNING.
2. `test_the_branch_is_free_once_the_first_session_ends`:
   - after `stop(first)` (record ENDED, landed), the second launch succeeds.
3. `test_a_dead_but_not_ended_session_still_holds_the_branch`:
   - kill the first sandbox in the fake backend, leaving its record RUNNING; the second launch is refused;
   - after `sessions(ws)` ends the dead one, the second launch succeeds.
4. `test_an_ended_session_that_owes_still_holds_the_branch`:
   - an ENDED record of another key with `unlanded`: refused, and the record is unchanged;
   - an ENDED record of another key with `wip_mark` (a crash after ENDED, before the WIP commit): the launch settles it (the WIP commit is on the branch, the mark cleared) and then succeeds;
   - the same, with `gitwip.wip_commit` failing: refused, and the mark is kept;
   - the same, with the record write that clears the mark failing after the commit: refused, no record or sandbox for the new key, the mark kept; a retry launches and the branch holds exactly one WIP commit for that mark;
   - an owed-WIP record together with an unended (RUNNING) or an unlanded record of the same bead, in both directory orders (short IDs chosen to sort either way): refused, and the branch tip, the worktree and every record are unchanged (no WIP committed).
5. `test_a_hold_in_another_workstream_clears_through_that_workstream`:
   - a dead RUNNING record of workstream A on bead X; a launch for workstream B on X is refused;
   - `sessions("B")` doesn't clear it; `sessions("A")` does, and the launch then succeeds.
6. `test_a_stale_same_key_record_is_ended_before_the_check`:
   - a dead RUNNING record of the launching key itself: the relaunch ends it, as today, and succeeds.
7. `test_a_repo_alias_is_the_same_repo`:
   - the second key names the repository through a symlink to it: refused.
8. `test_other_beads_and_other_repos_are_not_refused`:
   - a second key on bead Y in the same repo launches;
   - a second key on bead X in another repo launches.
9. The existing `test_launch_is_idempotent_on_a_live_session` still passes unchanged (same key).

Existing tests that launch two sessions at once on the defaults (`tests/sandbox_env.py` gives every spec bead `btq-1` in one repo) now fail at the second launch. They are migrated: each independent session gets its own bead with its own pinned worktree. Known now: `tests/test_wsd_lifetime.py::test_one_stop_that_fails_does_not_keep_the_next_from_stopping`, and the live two-CLI run that produced the shape (T14). The implementation greps the suites for others (two `launch(` calls without a stop between them). A shared branch stays only where refusal or legacy-state recovery is the test's point. The fix is in the tests, never the runtime.

## Docs

`docs/wsd.md` §5, beside the unlanded-hold paragraph: two sentences: a bead's branch holds one session at a time, so a launch is refused while another key's record of that bead is not ended, unlanded, or owes its lifetime WIP; a hold clears through its own workstream's pickup or `stop`.
