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

        for other in self._records():
            if other.key == spec.session_key or other.bead != spec.bead or not _same_repo(other.repo, spec.repo):
                continue
            if other.phase is Phase.ENDED and other.wip_mark and not other.unlanded:
                with contextlib.suppress(OSError):          # D13: the WIP it owes lands before anyone else's
                    other = self._settle_wip(self.layout(other.key), other)
            if other.phase is not Phase.ENDED or other.unlanded or other.wip_mark:
                raise LaunchFailed(BRANCH_TAKEN)
```

- **What is "the same branch".** The branch is `btq/<bead>` in the record's repository (`gitwip.pin(spec.repo, spec.worktree, f"btq/{spec.bead}")`). Two records name the same branch when they have the same bead and the same repository. `_same_repo` compares `os.path.realpath` of the two paths. A record without a repo can't share one, so it is not counted.
- **Which records hold the branch** (Codex jjnqm r1). ENDED alone doesn't mean the branch is settled: `_end` records ENDED before it settles the lifetime WIP, and a land that fails records ENDED with `unlanded`. So another key's record holds the branch while any of these is true:
  - its phase is not ENDED (CREATING, TESTING, STARTING, RUNNING or STOPPING), whether or not its sandbox is still alive, because it still owes its land or its lifetime WIP (D13);
  - it is `unlanded`: its commits wait for a human (T10 r3), and a new session's commits would diverge from them;
  - it still owes a lifetime WIP (`wip_mark`). The check first tries `_settle_wip` on it, which is what the owner's own end or relaunch does and is idempotent by its mark (a crash between ENDED and the commit is the case this covers). If the mark is still set after that, the branch stays held.
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
   - the same, with `gitwip.wip_commit` failing: refused, and the mark is kept.
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

The live test that produced the shape (T14's two-CLI run on one bead) must use one bead per CLI. If it still shares one, it will now fail at launch with `BRANCH_TAKEN`, and that is fixed in the test, not the runtime.

## Docs

`docs/wsd.md` §5, beside the unlanded-hold paragraph: two sentences: a bead's branch holds one session at a time, so a launch is refused while another key's record of that bead is not ended, unlanded, or owes its lifetime WIP; a hold clears through its own workstream's pickup or `stop`.
