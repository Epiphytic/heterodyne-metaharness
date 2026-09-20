# Task worktrees

Authority: [tasks](tasks.md), [sessions](sessions.md), [change lifecycle](changes.md).

New repository Beads get an owned local branch and checkout under
runs/RUN/beads/ISSUE. The base is the canonical local repository HEAD (operator
integration owns that checkout); no fetch or clone occurs. The intent manifest
pins that base before Git mutation. Retry adopts only the exact recorded path,
branch and repository, never a latest branch or another writer's checkout.
Existing bound Beads without a task manifest remain in their existing checkout.
A new bind/claim requires a clean worktree and a confirmed idle native worker,
with no approval or recovery hold. Dirty boundaries are workflow errors.

Existing .venv, venv and node_modules directories are copied locally using
cp --reflink=auto: reflinks when supported, independent local copies otherwise.
No download/install is run. The manifest records sources and the fallback policy,
not an unverified claim that reflinks were supported. Dependency-root symlinks and
tracked destination files fail closed. Python environments can retain absolute
script shebangs and editable paths: inspect them before mutation; dependency
reuse does not promise arbitrary venv relocation or version compatibility.
[Agenticow](https://github.com/ruvnet/agenticow) implements vector-memory branching,
not filesystem worktrees; its sharing principle is reused without a new package.

A successful boundary exact-resumes the same idle coding conversation in the new
cwd, removes stale Codex -C arguments, and sends no task prompt. Manager identity
is unchanged. Hook cwd checks use the new durable workdir. Interrupted switches
retain workspace_transition for operator reconciliation; do not replay workloads
or infer success from a directory's existence. Native sandbox cwd permissions must
be supplied by the resumed provider; the harness never edits approval settings.

Completed worktrees are retained. No automatic deletion/pruning exists. Explicit
operator cleanup must verify closed Bead, pinned completion evidence, correct
manifest/path/branch, no active worker at that cwd, clean Git state and no needed
untracked/ignored dependency artifacts. Remove only that exact worktree through
Git; never enumerate-and-delete unrelated worktrees or force removal.

## Concurrent review tasks

One worktree owns one Bead; one native worker executes in one current worktree.
A task may remain claimed during review, merge and deployment while the worker
claims another eligible task. Every existing active claim must have an owned task
worktree, unchanged verified owner, valid chronological lifecycle evidence through
PR-open, a passing full suite and recorded pushed review commit. Its checkout must
remain clean at the latest recorded lifecycle commit. Otherwise a new claim fails.
The native idle, approval and recovery boundary still applies before switching.
No existing task is released or closed merely to make another claim possible.

Concurrent admission reuses BTQ's exact ready/routing/design checks and native
atomic `bd update --claim`, replacing only its legacy one-active-claim restriction
with the review checks above. Post-claim owner/routing/design are reverified; an
uncertain mutation remains uncertain. Unmanaged BTQ retains its existing policy.
Lifecycle stage/close commands for a retained task validate that task's exact owned
checkout, never the newly active worktree. They do not switch or restart the worker.
Unread scope changes still block closure; rebind the retained task at a safe boundary
to consume revised task context before resuming implementation. There is no second
writer, automatic merge, automatic approval or implicit deployment.

Explicit [queue interruption](queue-order.md) also permits handoff from a verified
parked clean owned checkout. Its claim remains retained; no review evidence is
invented for unfinished work. Resume reuses the recorded dependency-copy source.
